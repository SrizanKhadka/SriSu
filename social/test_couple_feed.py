from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from threading import Barrier, Event
from unittest.mock import patch

from django.core.cache import cache, caches
from django.db import IntegrityError, connection, connections, transaction
from django.test import SimpleTestCase, override_settings, skipUnlessDBFeature
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone
from redis.exceptions import ConnectionError as RedisConnectionError
from rest_framework.test import APITestCase, APITransactionTestCase

from authentication.models import UserModel, UserInterestModel, InterestModel
from social.models import (CoupleConnectionModel, CoupleFaveModel, CoupleMembershipModel,
                           CoupleMomentModel, CoupleMomentViewModel, SingleConnectionModel)
from social.repository.couple_repository import ranked_couples
from social.services.couple_profile_service import create_or_get_couple_for_connection
from social.services.couple_scorer import compose, interest_set, rank_score
from social.services.fave_service import add_fave, remove_fave


class CoupleScorerTests(SimpleTestCase):
    def test_worked_example_and_missing_metadata(self):
        now = timezone.now()
        source = {"travel", "coffee"}
        a = rank_score(source, {"travel"}, ("Kathmandu", "Nepal"), [("kathmandu", "nepal")],
                       False, now - timedelta(hours=8), now)
        b = rank_score(source, {"travel", "music", "art"}, ("Kathmandu", "Nepal"), [("Sydney", "Australia")],
                       True, now - timedelta(hours=2), now)
        self.assertAlmostEqual(a, 52.5)
        self.assertAlmostEqual(b, 42.6134, places=3)
        self.assertEqual(rank_score(set(), set(), (None, None), [(None, None)], False, now, now), 15)
        self.assertEqual(interest_set([" Travel ", "travel", "", "ＣＯＦＦＥＥ"]), {"travel", "coffee"})

    def test_exploration_is_deterministic_distinct_and_prefers_non_faves(self):
        ids = list(range(1, 41))
        result = compose(ids, set(range(1, 31)), "user:day")
        self.assertEqual(result, compose(ids, set(range(1, 31)), "user:day"))
        self.assertEqual(set(result), set(ids))
        self.assertEqual(len(result), len(ids))
        self.assertGreater(result[9], 30)
        self.assertEqual(result[:9], ids[:9])
        self.assertEqual(compose(ids, set(), "seed", interval=0), ids)


class FeedFixtures:
    def setUp(self):
        cache.clear()
        caches["couple_feed"].clear()
        self.counter = 0
        self.viewer = self.user()
        self.client.force_authenticate(self.viewer)
        self.feed_url = reverse("couple-feed")
        self.library_url = reverse("couple-faves")
        self.sequence_url = reverse("couple-moment-sequence")

    def user(self, **kwargs):
        self.counter += 1
        return UserModel.objects.create_user(phone_number=f"+97798111{self.counter:05}", **kwargs)

    def couple(self, **kwargs):
        first, second = self.user(), self.user()
        connection = CoupleConnectionModel.objects.create(sender_number=first.phone_number,
            receiver_number=second.phone_number, connection_status="ACCEPTED")
        couple = create_or_get_couple_for_connection(connection)
        for key, value in kwargs.items():
            setattr(couple, key, value)
        couple.save()
        return couple

    def moment(self, couple, hours=0, **kwargs):
        members = list(couple.memberships.order_by("id"))
        moment = CoupleMomentModel.objects.create(couple=couple, created_by=members[0].user,
            caption="Public moment", moment_date=timezone.localdate(), visibility=kwargs.pop("visibility", "public"),
            audience_membership_ids=[m.pk for m in members], audience_user_ids=[m.user_id for m in members], **kwargs)
        if hours:
            created = timezone.now() - timedelta(hours=hours)
            CoupleMomentModel.objects.filter(pk=moment.pk).update(created_at=created, expires_at=created + timedelta(hours=24))
            moment.refresh_from_db()
        return moment

    def fave_url(self, couple):
        return reverse("couple-fave-detail", args=[couple.pk])

    def get_data(self, url=None, **params):
        response = self.client.get(url or self.feed_url, params)
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response["Cache-Control"], "private, no-store")
        return response.data["data"]


class CoupleFaveTests(FeedFixtures, APITestCase):
    def test_personal_idempotent_add_remove_without_content(self):
        couple = self.couple()
        first = self.client.put(self.fave_url(couple))
        second = self.client.put(self.fave_url(couple))
        self.assertEqual((first.status_code, second.status_code), (201, 200))
        self.assertEqual(first.data["data"]["created_at"], second.data["data"]["created_at"])
        self.assertEqual(CoupleFaveModel.objects.count(), 1)
        saved = self.get_data(self.library_url)["results"][0]
        self.assertTrue(saved["available"])
        self.assertFalse(saved["has_active_content"])
        for _ in range(2):
            self.assertEqual(self.client.delete(self.fave_url(couple)).status_code, 204)
        self.assertFalse(CoupleFaveModel.objects.exists())

    def test_partners_do_not_share_faves_or_inherit_ownership(self):
        own, target = self.couple(), self.couple()
        partner_one, partner_two = list(own.members.all())
        self.client.force_authenticate(partner_one)
        self.assertEqual(self.client.put(self.fave_url(target)).status_code, 201)
        self.client.force_authenticate(partner_two)
        self.assertEqual(self.get_data(self.library_url)["results"], [])
        self.assertEqual(self.client.delete(self.fave_url(target)).status_code, 204)
        self.assertTrue(CoupleFaveModel.objects.filter(user=partner_one, couple=target).exists())
        self.assertEqual(self.client.put(self.fave_url(target), {"user": partner_one.pk}, format="json").status_code, 400)
        self.assertEqual(self.client.put(self.fave_url(own)).status_code, 400)

    def test_authentication_validation_and_unavailable_targets(self):
        target = self.couple()
        self.client.force_authenticate(None)
        for url in (self.feed_url, self.library_url, self.sequence_url):
            self.assertEqual(self.client.get(url).status_code, 401)
        self.assertEqual(self.client.put(self.fave_url(target)).status_code, 401)
        self.client.force_authenticate(self.viewer)
        for value in ("0", "-1", "abc", "99999999999999999999"):
            self.assertEqual(self.client.put(reverse("couple-fave-detail", args=[value])).status_code, 400)
        self.assertEqual(self.client.put(reverse("couple-fave-detail", args=[99999])).status_code, 404)
        member = target.members.first()
        SingleConnectionModel.objects.create(sender_number=member.phone_number,
            receiver_number=self.viewer.phone_number, connection_status="BLOCKED")
        self.assertEqual(self.client.put(self.fave_url(target)).status_code, 404)

    def test_unavailable_placeholders_do_not_leak_profile_and_remain_removable(self):
        target = self.couple(title="Secret title", journey_story="Secret story")
        self.client.put(self.fave_url(target))
        self.moment(target)
        target.couple_connection.connection_status = "BREAK-UP"
        target.couple_connection.save()
        result = self.get_data(self.library_url)["results"][0]
        self.assertEqual(set(result), {"couple_id", "created_at", "is_faved", "available", "has_active_content"})
        self.assertFalse(result["available"])
        self.assertFalse(result["has_active_content"])
        self.assertEqual(self.get_data(section="faves")["results"], [])
        self.assertEqual(self.client.delete(self.fave_url(target)).status_code, 204)

    def test_database_uniqueness_and_cascades(self):
        target = self.couple()
        CoupleFaveModel.objects.create(user=self.viewer, couple=target)
        with self.assertRaises(IntegrityError), transaction.atomic():
            CoupleFaveModel.objects.create(user=self.viewer, couple=target)
        target.delete()
        self.assertEqual(CoupleFaveModel.objects.count(), 0)
        target = self.couple()
        CoupleFaveModel.objects.create(user=self.viewer, couple=target)
        self.viewer.delete()
        self.assertEqual(CoupleFaveModel.objects.count(), 0)

    def test_library_keyset_handles_ties_additions_and_removals(self):
        targets = [self.couple() for _ in range(3)]
        for target in targets:
            self.client.put(self.fave_url(target))
        CoupleFaveModel.objects.update(created_at=timezone.now() - timedelta(minutes=1))
        first = self.get_data(self.library_url, page_size=1)
        self.client.delete(self.fave_url(targets[1]))
        self.client.put(self.fave_url(self.couple()))
        second = self.get_data(first["next"])
        self.assertEqual(first["results"][0]["couple_id"], targets[2].pk)
        self.assertEqual(second["results"][0]["couple_id"], targets[0].pk)
        self.assertIsNone(second["next"])


class CoupleFeedTests(FeedFixtures, APITestCase):
    def test_strict_faves_empty_states_and_global_overlap(self):
        target, discovery = self.couple(), self.couple()
        self.moment(discovery)
        empty = self.get_data(section="faves")
        self.assertEqual(empty["empty_reason"], "no_faves")
        self.client.put(self.fave_url(target))
        self.assertEqual(self.get_data(section="faves")["empty_reason"], "no_active_fave_moments")
        self.moment(target)
        self.assertEqual([r["couple"]["id"] for r in self.get_data(section="faves")["results"]], [target.pk])
        global_cards = self.get_data()["results"]
        self.assertEqual({r["couple"]["id"] for r in global_cards}, {target.pk, discovery.pk})
        self.assertTrue(next(r for r in global_cards if r["couple"]["id"] == target.pk)["is_faved"])

    def test_faves_unopened_then_latest_and_no_global_cap(self):
        older, newer = self.couple(), self.couple()
        old = self.moment(older, hours=5)
        new = self.moment(newer)
        for target in (older, newer):
            self.client.put(self.fave_url(target))
        CoupleMomentViewModel.objects.create(moment=new, viewer=self.viewer)
        with override_settings(COUPLE_FEED_CANDIDATE_LIMIT=1):
            rows = self.get_data(section="faves")["results"]
        self.assertEqual([r["couple"]["id"] for r in rows], [older.pk, newer.pk])
        CoupleMomentViewModel.objects.create(moment=old, viewer=self.viewer)
        self.assertEqual([r["couple"]["id"] for r in self.get_data(section="faves")["results"]], [newer.pk, older.pk])

    def test_all_eligibility_rules_and_profile_privacy(self):
        visible = self.couple(title="Private couple title", shared_dreams=["Secret dream"])
        self.moment(visible)
        private = self.couple()
        self.moment(private, visibility="private")
        self.moment(private, visibility="friends")
        self.moment(private, is_archived=True)
        self.moment(private, is_time_capsule=True)
        self.moment(private, hours=24)
        broken = self.couple()
        self.moment(broken)
        broken.couple_connection.connection_status = "BREAK-UP"
        broken.couple_connection.save()
        blocked = self.couple()
        self.moment(blocked)
        SingleConnectionModel.objects.create(sender_number=self.viewer.phone_number,
            receiver_number=blocked.members.first().phone_number, connection_status="BLOCKED")
        inactive = self.couple()
        self.moment(inactive)
        inactive.members.filter(pk=inactive.members.first().pk).update(is_active=False)
        replaced = self.couple()
        self.moment(replaced)
        membership = replaced.memberships.first()
        original = membership.user
        position = membership.position
        membership.delete()
        CoupleMembershipModel.objects.create(couple=replaced, user=original, position=position)
        data = self.get_data()
        self.assertEqual([r["couple"]["id"] for r in data["results"]], [visible.pk])
        card = data["results"][0]
        self.assertEqual(card["couple"], {"id": visible.pk})
        self.assertNotIn("appreciation_notes", card["preview"])
        self.assertNotIn("Private couple title", str(data))
        self.assertNotIn("phone_number", str(data))

    def test_own_couple_is_excluded(self):
        own = self.couple()
        self.moment(own)
        self.client.force_authenticate(own.members.first())
        self.assertEqual(self.get_data()["results"], [])

    def test_unopened_is_personal_and_feed_does_not_record_views(self):
        target = self.couple()
        moment = self.moment(target)
        self.assertTrue(self.get_data()["results"][0]["has_unopened"])
        self.get_data(self.sequence_url, couple=target.pk)
        self.assertEqual(CoupleMomentViewModel.objects.count(), 0)
        self.client.get(reverse("couple_moments-detail", args=[moment.pk]))
        self.assertFalse(self.get_data()["results"][0]["has_unopened"])
        self.client.force_authenticate(self.user())
        self.assertTrue(self.get_data()["results"][0]["has_unopened"])

    def test_sessions_stabilize_order_and_skip_expired_private_deleted_or_blocked(self):
        targets = [self.couple() for _ in range(6)]
        moments = [self.moment(target, hours=index) for index, target in enumerate(targets)]
        first = self.get_data(page_size=1)
        self.assertEqual(first["results"][0]["couple"]["id"], targets[0].pk)
        CoupleMomentModel.objects.filter(pk=moments[1].pk).update(expires_at=timezone.now())
        CoupleMomentModel.objects.filter(pk=moments[2].pk).update(visibility="private")
        moments[3].delete()
        SingleConnectionModel.objects.create(sender_number=targets[4].members.first().phone_number,
            receiver_number=self.viewer.phone_number, connection_status="BLOCKED")
        fresh = self.couple()
        self.moment(fresh)
        second = self.get_data(first["next"])
        self.assertEqual([r["couple"]["id"] for r in second["results"]], [targets[5].pk])
        self.assertIsNone(second["next"])
        self.assertIn(fresh.pk, [r["couple"]["id"] for r in self.get_data()["results"]])

    def test_fave_changes_invalidate_both_sections_but_repeated_put_does_not(self):
        a, b = self.couple(), self.couple()
        for target in (a, b):
            self.moment(target)
            self.client.put(self.fave_url(target))
        for section in ("global", "faves"):
            with self.subTest(section=section):
                first = self.get_data(section=section, page_size=1)
                self.client.put(self.fave_url(a))
                self.assertEqual(self.client.get(first["next"]).status_code, 200)
                self.client.delete(self.fave_url(a))
                self.assertEqual(self.client.get(first["next"]).status_code, 409)
                self.client.put(self.fave_url(a))
                # Remove/re-add is a real change even if the couple set is identical.
                self.assertEqual(self.client.get(first["next"]).status_code, 409)

    def test_cursor_binding_tampering_parameters_expiry_and_redis_failure(self):
        for _ in range(2):
            self.moment(self.couple())
        first = self.get_data(page_size=1)
        url = first["next"]
        for suffix in ("x", "&section=faves", "&content=challenges", "&page_size=2"):
            self.assertEqual(self.client.get(url + suffix).status_code, 400)
        self.client.force_authenticate(self.user())
        self.assertEqual(self.client.get(url).status_code, 400)
        self.client.force_authenticate(self.viewer)
        caches["couple_feed"].clear()
        self.assertEqual(self.client.get(url).status_code, 410)
        with patch("social.services.couple_feed_service.session_cache", side_effect=RedisConnectionError):
            degraded = self.get_data(page_size=1)
            self.assertEqual(len(degraded["results"]), 1)
            self.assertFalse(degraded["pagination_available"])
            self.assertIsNone(degraded["next"])
        first = self.get_data(page_size=1)
        with patch("django.core.signing.time.time", return_value=timezone.now().timestamp() + 1000):
            self.assertEqual(self.client.get(first["next"]).status_code, 410)
        for params in ({"section": "bad"}, {"page_size": "0"}, {"page_size": "51"}, {"cursor": ""}):
            self.assertEqual(self.client.get(self.feed_url, params).status_code, 400)

    def test_pool_limit_and_many_uploads_do_not_duplicate_couples(self):
        targets = [self.couple() for _ in range(4)]
        for target in targets:
            self.moment(target)
        for _ in range(20):
            self.moment(targets[0])
        with override_settings(COUPLE_FEED_CANDIDATE_LIMIT=3):
            rows = self.get_data()["results"]
            self.assertEqual(len(rows), 3)
            self.assertEqual(len({r["couple"]["id"] for r in rows}), 3)

    def test_interest_fallback_and_removed_interests(self):
        coffee = InterestModel.objects.create(name="Coffee")
        UserInterestModel.objects.create(user=self.viewer, name="legacy", interest=coffee)
        UserInterestModel.objects.create(user=self.viewer, name="Travel", removed=True)
        a, b = self.couple(), self.couple(shared_interests=["Travel"])
        UserInterestModel.objects.create(user=a.members.first(), name="coffee")
        for target in (a, b):
            self.moment(target)
        self.assertEqual(ranked_couples(self.viewer, "global", timezone.now(), set())[0], a.pk)
        UserInterestModel.objects.filter(user=a.members.first()).update(removed=True)
        a.shared_interests = [" Coffee "]
        a.save()
        self.assertEqual(ranked_couples(self.viewer, "global", timezone.now(), set())[0], a.pk)

    def test_requester_shared_interest_fallback_and_location_are_not_exclusions(self):
        own = self.couple(shared_interests=["Coffee"])
        viewer = own.members.first()
        self.client.force_authenticate(viewer)
        viewer.city, viewer.country = "Kathmandu", "Nepal"
        viewer.save()
        a, b = self.couple(shared_interests=["coffee"]), self.couple()
        for target in (a, b):
            self.moment(target)
        b.members.all().update(city="Kathmandu", country="Nepal")
        self.assertEqual([r["couple"]["id"] for r in self.get_data()["results"]], [a.pk, b.pk])
        UserInterestModel.objects.create(user=viewer, name="Music")
        # Personal selections supersede the shared fallback; local b now ranks first.
        self.assertEqual(self.get_data()["results"][0]["couple"]["id"], b.pk)

    def test_faves_block_and_membership_change_invalidate_content_on_next_page(self):
        targets = [self.couple() for _ in range(3)]
        for hours, target in enumerate(targets):
            self.moment(target, hours=hours)
            self.client.put(self.fave_url(target))
        first = self.get_data(section="faves", page_size=1)
        SingleConnectionModel.objects.create(sender_number=targets[1].members.first().phone_number,
            receiver_number=self.viewer.phone_number, connection_status="BLOCKED")
        member = targets[2].memberships.first()
        old_user, position = member.user, member.position
        member.delete()
        CoupleMembershipModel.objects.create(couple=targets[2], user=old_user, position=position)
        self.assertEqual(self.get_data(first["next"])["results"], [])
        self.assertEqual(CoupleFaveModel.objects.filter(user=self.viewer).count(), 3)

    def test_expiry_during_preview_hydration_drops_card(self):
        target = self.couple()
        moment = self.moment(target)
        expiry = timezone.now() + timedelta(seconds=1)
        CoupleMomentModel.objects.filter(pk=moment.pk).update(expires_at=expiry)
        from social.api.couple_serializers import MomentPreviewSerializer
        original = MomentPreviewSerializer.to_representation

        def expiring(serializer, instance):
            data = original(serializer, instance)
            # Represent a clock crossing the boundary during serialization.
            instance.expires_at = timezone.now() - timedelta(seconds=1)
            return data

        with patch.object(MomentPreviewSerializer, "to_representation", expiring):
            self.assertEqual(self.get_data()["results"], [])

    def test_visibility_changed_after_card_hydration_is_rechecked_at_delivery(self):
        target = self.couple()
        moment = self.moment(target)
        from social.services.couple_feed_service import cards

        def changing(*args, **kwargs):
            result = cards(*args, **kwargs)
            CoupleMomentModel.objects.filter(pk=moment.pk).update(visibility="private")
            return result

        with patch("social.services.couple_feed_service.cards", side_effect=changing):
            self.assertEqual(self.get_data()["results"], [])

    def test_feed_start_and_fave_write_throttles(self):
        from social.api.couple_views import FeedStartThrottle, FaveWriteThrottle
        target = self.couple()
        self.moment(target)
        self.moment(self.couple())
        with patch.object(FeedStartThrottle, "rate", "1/min"):
            first = self.get_data(page_size=1)
            self.assertEqual(self.client.get(self.feed_url).status_code, 429)
            self.assertEqual(self.client.get(first["next"]).status_code, 200)
        with patch.object(FaveWriteThrottle, "rate", "1/min"):
            self.assertEqual(self.client.put(self.fave_url(target)).status_code, 201)
            self.assertEqual(self.client.delete(self.fave_url(target)).status_code, 429)
        self.assertEqual(self.client.get(self.library_url).status_code, 200)

    def test_card_queries_do_not_grow_per_couple(self):
        self.moment(self.couple())
        with CaptureQueriesContext(connection) as small:
            self.get_data()
        for _ in range(9):
            self.moment(self.couple())
        with CaptureQueriesContext(connection) as large:
            self.get_data()
        self.assertGreater(len(small), 0)
        self.assertEqual(len(small), len(large))

    @override_settings(COUPLE_FEED_ENABLED=False)
    def test_feature_flag_only_disables_new_endpoints(self):
        self.assertEqual(self.client.get(self.feed_url).status_code, 404)
        self.assertEqual(self.client.get(self.library_url).status_code, 404)
        self.assertEqual(self.client.get(reverse("couple_moments-list")).status_code, 200)


class CoupleSequenceTests(FeedFixtures, APITestCase):
    def test_chronological_unopened_sequence_keyset_and_replay(self):
        target = self.couple()
        old, middle, new = [self.moment(target, hours=hours) for hours in (3, 2, 1)]
        CoupleMomentViewModel.objects.create(moment=middle, viewer=self.viewer)
        first = self.get_data(self.sequence_url, couple=target.pk, page_size=1)
        self.assertEqual([r["id"] for r in first["results"]], [old.pk])
        self.client.get(reverse("couple_moments-detail", args=[old.pk]))
        added = self.moment(target)
        second = self.get_data(first["next"])
        self.assertEqual([r["id"] for r in second["results"]], [new.pk])
        self.assertIsNone(second["next"])
        replay = self.get_data(self.sequence_url, couple=target.pk, mode="all")
        self.assertEqual([r["id"] for r in replay["results"]], [old.pk, middle.pk, new.pk, added.pk])
        self.assertEqual([r["has_opened"] for r in replay["results"]], [True, True, False, False])

    def test_sequence_rechecks_access_and_expiry_and_does_not_embed_private_notes(self):
        target = self.couple()
        first = self.moment(target, hours=2)
        second = self.moment(target, hours=1)
        self.moment(target, visibility="private")
        data = self.get_data(self.sequence_url, couple=target.pk, page_size=1)
        self.assertNotIn("appreciation_notes", data["results"][0])
        CoupleMomentModel.objects.filter(pk=second.pk).update(expires_at=timezone.now())
        self.assertEqual(self.get_data(data["next"])["results"], [])
        SingleConnectionModel.objects.create(sender_number=self.viewer.phone_number,
            receiver_number=target.members.first().phone_number, connection_status="BLOCKED")
        self.assertEqual(self.get_data(self.sequence_url, couple=target.pk, mode="all")["results"], [])
        self.assertEqual(self.client.get(self.sequence_url, {"couple": "invalid"}).status_code, 400)


class CoupleFaveConcurrencyTests(FeedFixtures, APITransactionTestCase):
    @skipUnlessDBFeature("has_select_for_update")
    def test_simultaneous_adds_create_one_row(self):
        target = self.couple()
        barrier = Barrier(2)

        def add():
            try:
                barrier.wait(timeout=10)
                return add_fave(self.viewer, target.pk)[1]
            finally:
                connections.close_all()

        with ThreadPoolExecutor(max_workers=2) as pool:
            jobs = [pool.submit(add) for _ in range(2)]
            created = [job.result(timeout=20) for job in jobs]
        self.assertEqual(sorted(created), [False, True])
        self.assertEqual(CoupleFaveModel.objects.count(), 1)

    @skipUnlessDBFeature("has_select_for_update")
    def test_simultaneous_add_remove_are_serialized(self):
        target = self.couple()
        locked, deleting = Event(), Event()

        def add_while_locked():
            try:
                with transaction.atomic():
                    UserModel.objects.select_for_update().get(pk=self.viewer.pk)
                    locked.set()
                    if not deleting.wait(timeout=10):
                        raise AssertionError("Delete thread did not start")
                    add_fave(self.viewer, target.pk)
            finally:
                connections.close_all()

        def delete_after_lock():
            try:
                if not locked.wait(timeout=10):
                    raise AssertionError("Add thread did not acquire lock")
                deleting.set()
                remove_fave(self.viewer, target.pk)
            finally:
                connections.close_all()

        with ThreadPoolExecutor(max_workers=2) as pool:
            jobs = [pool.submit(add_while_locked), pool.submit(delete_after_lock)]
            for job in jobs:
                job.result(timeout=20)
        # Delete started after the add acquired the user lock and must run last.
        self.assertFalse(CoupleFaveModel.objects.exists())

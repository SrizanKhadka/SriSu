"""Individual, consent-bound following; never read or mutate private Faves."""
from django.db import OperationalError, transaction
from django.db.models import F, Q
from rest_framework.exceptions import APIException, PermissionDenied
from authentication.models import UserModel
from social.models import CoupleModel, CoupleMembershipModel, CouplePublicFollow, CoupleFollowVersion
from social.services.discovery_identity import approved_identity
from social.services.fave_service import available_couples
from social.services.moment_service import active_couples, blocked_user_ids
from social.services.couple_profile_sections import audience


class FollowConflict(APIException):
    status_code = 409
    default_detail = 'Following changed. Reload before trying again.'
    default_code = 'follow_version_changed'


def follow_version(user):
    return CoupleFollowVersion.objects.filter(user=user).values_list('version', flat=True).first() or 0


def context(user, couple_id):
    source = CoupleMembershipModel.objects.select_related('couple', 'user').filter(user=user,
        couple_id__in=active_couples().values('pk')).first()
    if source and source.couple_id == couple_id:
        return source, None, 'own_couple'
    target = available_couples(user).filter(pk=couple_id).first()
    if target is None or not approved_identity(target, list(target.memberships.select_related('user'))):
        return source, None, 'unavailable'
    if source is None:
        return None, target, 'connect_partner'
    members = list(source.couple.memberships.select_related('user'))
    if not approved_identity(source.couple, members):
        return source, target, 'approve_public_identity'
    return source, target, None


def valid_follow(row, source, target):
    return (source is not None and target is not None and row.source_membership_id == source.pk
        and row.identity_version == source.couple.privacy_proposal_version
        and row.target_audience == audience(list(target.memberships.all())))


def snapshot(user, couple_id):
    source, target, reason = context(user, couple_id)
    row = CouplePublicFollow.objects.filter(user=user, couple_id=couple_id).first()
    return {'couple_id': couple_id, 'is_following': reason is None and row is not None and valid_follow(row, source, target),
        'can_follow': reason is None, 'reason': reason, 'version': follow_version(user)}


def mutation_version(user, expected):
    state, _ = CoupleFollowVersion.objects.get_or_create(user=user)
    if expected is not None and state.version != expected:
        raise FollowConflict()
    return state


@transaction.atomic
def follow(user, couple_id, expected=None):
    # Relationship changes lock users before couples. Read current membership only
    # after this user's barrier, so disconnect/relink cannot leave a stale source.
    UserModel.objects.select_for_update().get(pk=user.pk)
    source_id = CoupleMembershipModel.objects.filter(user=user).values_list('couple_id', flat=True).first()
    try:
        # Other writers can hold a couple before requesting user foreign-key
        # locks. Never wait for their couple while retaining our user barrier.
        # Sorted locks also serialize publication revocation on either couple.
        with transaction.atomic():
            list(CoupleModel.objects.select_for_update(nowait=True).filter(
                pk__in=[i for i in (source_id, couple_id) if i]).order_by('pk'))
    except OperationalError as error:
        cause = error.__cause__
        if (getattr(cause, 'sqlstate', None) or getattr(cause, 'pgcode', None)) == '55P03':
            # Only PostgreSQL lock_not_available is expected contention. The outer
            # atomic block releases the user barrier before the API can retry/read.
            raise FollowConflict() from error
        raise
    state = mutation_version(user, expected)
    source, target, reason = context(user, couple_id)
    if reason:
        raise PermissionDenied({'follow': reason})
    row, created = CouplePublicFollow.objects.update_or_create(user=user, couple=target, defaults={
        'source_membership': source, 'identity_version': source.couple.privacy_proposal_version,
        'target_audience': audience(list(target.memberships.all()))})
    # Conditional no-ops are barriers against delayed opposite writes.
    state.version += 1
    state.save(update_fields=['version'])
    return created


@transaction.atomic
def unfollow(user, couple_id, expected=None):
    UserModel.objects.select_for_update().get(pk=user.pk)
    state = mutation_version(user, expected)
    deleted, _ = CouplePublicFollow.objects.filter(user=user, couple_id=couple_id).delete()
    if deleted or expected is not None:
        state.version += 1
        state.save(update_fields=['version'])


def revoke_couple_follows(couple):
    """Caller holds the couple lock. Consent revocation is permanent, not a hidden toggle."""
    rows = CouplePublicFollow.objects.filter(Q(couple=couple) | Q(source_membership__couple=couple))
    users = list(rows.values_list('user_id', flat=True).distinct())
    rows.delete()
    CoupleFollowVersion.objects.filter(user_id__in=users).update(version=F('version') + 1)


def incoming_rows(request, target, members, rows):
    # Incoming identity disclosure has the same mutual-approval boundary as the
    # follower snapshot, including target name/photo edits that invalidate consent.
    if not approved_identity(target, members):
        return []
    target_audience = audience(members)
    blocked = set()
    for member in members:
        blocked.update(blocked_user_ids(member.user))
    result = []
    active = set(active_couples().filter(pk__in=[r.source_membership.couple_id for r in rows]).values_list('pk', flat=True))
    for row in rows:
        source = row.source_membership
        people = list(source.couple.memberships.all())
        if (source.ended_at is not None or source.user_id != row.user_id or source.couple_id not in active
                or row.target_audience != target_audience or row.identity_version != source.couple.privacy_proposal_version
                or any(person.user_id in blocked for person in people) or not approved_identity(source.couple, people)):
            continue
        person = next((person.user for person in people if person.pk == source.pk and person.user_id == row.user_id), None)
        if person is None:
            continue
        # Exactly one consenting individual; never label their partner as a follower.
        result.append({'user_id': person.pk, 'couple_id': source.couple_id,
            'display_name': person.full_name or person.username or '',
            'avatar_url': request.build_absolute_uri(f'/api/social/profiles/{source.couple_id}/members/{person.pk}/photo/') if person.profile_photo else None,
            'followed_at': row.created_at})
    return result

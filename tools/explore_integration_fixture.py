"""Synthetic Explore fixture for the existing disposable HTTP test server only."""
from io import BytesIO
from django.core.files.base import ContentFile
from django.utils import timezone
from PIL import Image, ImageDraw


def seed_explore(payload, first, second, visitor, existing_couple):
    from authentication.models import UserModel, InterestModel, UserInterestModel
    from authentication.sessions import create_session
    from social.models import (CoupleConnectionModel, CoupleMomentModel, CoupleMomentPhotoModel,
        CouplePrivacyConsentModel, MomentVideo, MomentVideoFile)
    from social.services.couple_profile_service import create_or_get_couple_for_connection
    from social.services.couple_profile_sections import audience, digest, raw_sections

    def picture(label, color):
        image = Image.new('RGB', (720, 960), color)
        draw = ImageDraw.Draw(image)
        draw.ellipse((100, 180, 620, 700), fill='#f4d8be')
        draw.rectangle((0, 730, 720, 960), fill='#273e47')
        draw.text((32, 770), label, fill='white')
        data = BytesIO(); image.save(data, 'JPEG', quality=92)
        return ContentFile(data.getvalue(), name='synthetic-explore.jpg')

    def person(number, name):
        return UserModel.objects.create_user(phone_number=number, full_name=name,
            is_phone_verified=True, is_profile_complete=True, city='Kathmandu', country='Nepal')

    def pair(a, b):
        link = CoupleConnectionModel.objects.create(sender_number=a.phone_number,
            receiver_number=b.phone_number, connection_status='ACCEPTED')
        return create_or_get_couple_for_connection(link)

    def interest(user, item):
        UserInterestModel.objects.get_or_create(user=user, interest=item,
            defaults={'name': item.name, 'removed': False})

    def approve(couple):
        members = list(couple.memberships.select_related('user').order_by('position'))
        raw = raw_sections(couple, members, members[0].user)
        sections = ['identity', 'interests']
        couple.privacy_proposal_version = 1
        couple.privacy_proposal = {'sections': sections, 'audience': audience(members),
            'fingerprints': {key: digest(raw[key]) for key in sections}}
        couple.save(update_fields=['privacy_proposal', 'privacy_proposal_version'])
        CouplePrivacyConsentModel.objects.bulk_create([CouplePrivacyConsentModel(couple=couple,
            user=m.user, proposal_version=1) for m in members])

    def moment(couple, creator, caption, private=False):
        members = list(couple.memberships.order_by('id'))
        return CoupleMomentModel.objects.create(couple=couple, created_by=creator,
            caption=caption, moment_date=timezone.localdate(), visibility='private' if private else 'public',
            audience_membership_ids=[m.pk for m in members], audience_user_ids=[m.user_id for m in members])

    first.full_name = 'Alex'; second.full_name = 'Sam'; visitor.full_name = 'Maya'
    for user in (first, second, visitor):
        user.city = 'Kathmandu'; user.country = 'Nepal'
        user.profile_photo = picture('Synthetic avatar', '#557b87')
        user.save(update_fields=['full_name', 'city', 'country', 'profile_photo'])
    visitor_partner = person('+15005550106', 'Owen')
    visitor_partner.profile_photo = picture('Synthetic avatar', '#b18c82')
    visitor_partner.save(update_fields=['profile_photo'])
    target = pair(visitor, visitor_partner)
    other_first, other_second = person('+15005550107', 'Nora'), person('+15005550108', 'Eli')
    other = pair(other_first, other_second)
    hidden_first, hidden_second = person('+15005550109', 'Hidden Maya'), person('+15005550110', 'Hidden Owen')
    hidden = pair(hidden_first, hidden_second)
    hiking, _ = InterestModel.objects.get_or_create(name='Hiking')
    photography, _ = InterestModel.objects.get_or_create(name='Photography')
    for user in (first, second, visitor, visitor_partner):
        interest(user, hiking)
    for user in (other_first, other_second):
        interest(user, photography)
    for couple in (existing_couple, target, other):
        approve(couple)
    photo_moment = moment(target, visitor, 'A quiet trail together — synthetic QA photo')
    image = CoupleMomentPhotoModel.objects.create(moment=photo_moment,
        image=picture('Synthetic shared trail', '#426b78'), order=0, width=720, height=960)
    ids = [photo_moment.pk]
    for index in range(11):
        text = moment(target, visitor, f'Synthetic shared memory {index + 1}')
        ids.append(text.pk)
    private = moment(target, visitor, 'Synthetic private memory — never in Global', private=True)
    other_moment = moment(other, other_first, 'Synthetic afternoon light')
    CoupleMomentPhotoModel.objects.create(moment=other_moment,
        image=picture('Synthetic afternoon', '#9c7161'), order=0, width=720, height=960)
    hidden_moment = moment(hidden, hidden_first, 'Synthetic anonymous public memory')
    payload.update(
        account_name=first.full_name, partner_name=second.full_name,
        visitor_name=visitor.full_name, visitor_couple_id=target.pk,
        visitor_partner_id=visitor_partner.pk, visitor_partner_access=create_session(visitor_partner)['access'],
        explore_interest_id=hiking.pk, explore_interest_label=hiking.name,
        explore_other_interest_id=photography.pk,
        explore_photo_moment_id=photo_moment.pk, explore_photo_id=image.pk,
        explore_text_moment_id=text.pk, explore_private_moment_id=private.pk,
        explore_moment_ids=ids, explore_creator_name=visitor.full_name,
        explore_target_name='Maya & Owen', explore_other_couple_id=other.pk,
        explore_other_moment_id=other_moment.pk,
        explore_hidden_couple_id=hidden.pk, explore_hidden_moment_id=hidden_moment.pk,
    )
    # --moment-video already encoded a real synthetic HLS fixture. Copy its immutable
    # derivatives into a distinct owned video, so HTTP revocation tests need no fake
    # player data and cannot change the source couple's private video fixture.
    if payload.get('video_id'):
        source_video = MomentVideo.objects.get(pk=payload['video_id'])
        video = MomentVideo.objects.create(owner=visitor, state='ready', source_bytes=source_video.source_bytes,
            received_bytes=source_video.received_bytes, duration_ms=source_video.duration_ms,
            width=source_video.width, height=source_video.height)
        for asset in source_video.files.all():
            with asset.file.open('rb') as file:
                copied = ContentFile(file.read(), name=asset.name)
            MomentVideoFile.objects.create(video=video, name=asset.name, file=copied,
                size=asset.size, digest=asset.digest)
        public_video = moment(target, visitor, 'Synthetic short trail video')
        poster = video.files.get(name='poster.jpg')
        with poster.file.open('rb') as file:
            poster_copy = ContentFile(file.read(), name='synthetic-video-poster.jpg')
        video_photo = CoupleMomentPhotoModel.objects.create(moment=public_video, video=video,
            image=poster_copy, order=0, width=video.width, height=video.height)
        payload.update(explore_video_id=str(video.pk), explore_video_moment_id=public_video.pk,
            explore_video_photo_id=video_photo.pk)
        payload['explore_moment_ids'].append(public_video.pk)

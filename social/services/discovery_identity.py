"""Bulk, consent-bound identity projection; never serialize member-only profiles."""
from django.db.models import Prefetch
from social.models import CoupleModel, CoupleMembershipModel
from social.services.couple_profile_sections import audience, digest


def identities(request, ids):
    members = CoupleMembershipModel.objects.select_related('user').order_by('position')
    rows = CoupleModel.objects.filter(pk__in=ids).prefetch_related(
        Prefetch('memberships', queryset=members), 'privacy_consents')
    result = {}
    for couple in rows:
        people = list(couple.memberships.all())
        proposal = couple.privacy_proposal or {}
        raw = [{'id': m.user_id, 'name': m.user.full_name or m.user.username or '',
                'photo': m.user.profile_photo.name} for m in people]
        approved = {c.user_id for c in couple.privacy_consents.all()
                    if c.proposal_version == couple.privacy_proposal_version}
        value = {'id': couple.pk}
        if (len(people) == 2 and proposal.get('audience') == audience(people)
                and approved == {m.user_id for m in people}
                and 'identity' in proposal.get('sections', [])
                and proposal.get('fingerprints', {}).get('identity') == digest(raw)):
            value['display_name'] = ' & '.join(p['name'] for p in raw if p['name'])
            value['avatars'] = [request.build_absolute_uri(
                f'/api/social/profiles/{couple.pk}/members/{p["id"]}/photo/')
                for p in raw if p['photo']]
        result[couple.pk] = value
    return result

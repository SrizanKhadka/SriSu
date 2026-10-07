"""Domain completion and membership projection. Never creates a couple."""
def profile_progress(user):
    from social.models import CoupleMembershipModel
    complete = user.is_profile_complete
    step = "complete" if complete else ("name" if not (user.full_name and user.username) else "photo")
    membership = CoupleMembershipModel.objects.filter(user=user,
        ended_at__isnull=True,
        couple__couple_connection__connection_status="ACCEPTED").first()
    return {"phone_verified": user.is_phone_verified, "profile_complete": complete,
            "next_step": step, "photo_skipped": user.profile_photo_skipped,
            "couple_id": membership.couple_id if membership else None,
            "membership": "linked" if membership else "unlinked"}

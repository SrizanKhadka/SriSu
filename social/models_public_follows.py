"""Explicit public follows. Personal Faves are never converted or disclosed."""
from django.db import models


class CoupleFollowVersion(models.Model):
    user = models.OneToOneField('authentication.UserModel', on_delete=models.CASCADE, primary_key=True)
    version = models.PositiveBigIntegerField(default=0)


class CouplePublicFollow(models.Model):
    user = models.ForeignKey('authentication.UserModel', on_delete=models.CASCADE, related_name='public_couple_follows')
    couple = models.ForeignKey('social.CoupleModel', on_delete=models.CASCADE, related_name='public_followers')
    source_membership = models.ForeignKey('social.CoupleMembershipModel', on_delete=models.CASCADE, related_name='public_follows')
    # Consent belongs to this individual and the approved identity/relationship at opt-in.
    identity_version = models.PositiveBigIntegerField()
    target_audience = models.JSONField(default=list)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at', '-id']
        constraints = [models.UniqueConstraint(fields=['user', 'couple'], name='unique_public_couple_follow')]
        indexes = [models.Index(fields=['couple', '-created_at', '-id'], name='public_follow_incoming_idx')]

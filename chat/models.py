from django.db import models
from utils.choices import CoupleConnectionStatus

# Create your models here.


class CoupleConnectionModel(models.Model):
    sender_number = models.CharField(max_length=15)
    receiver_number = models.CharField(max_length=15)
    connection_status = models.CharField(
        max_length=20,
        choices=CoupleConnectionStatus,
        default=CoupleConnectionStatus.NOTHING,
    )
    breakup_reason = models.TextField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = [
            "sender_number",
            "receiver_number",
        ]  # Prevent duplicate requests

        ordering = ["-updated_at"]

    def __str__(self):
        return f"{self.sender_number}-{self.receiver_number}"

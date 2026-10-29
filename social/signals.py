from django.db.models.signals import post_delete
from django.dispatch import receiver

from social.models import CoupleMomentPhotoModel, MomentDraftPhoto
from social.services.moment_service import queue_file_deletion


@receiver(post_delete, sender=CoupleMomentPhotoModel)
@receiver(post_delete, sender=MomentDraftPhoto)
def remove_moment_photo(sender, instance, **kwargs):
    queue_file_deletion(instance.image.name)

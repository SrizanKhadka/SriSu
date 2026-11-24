from django.db.models.signals import post_delete
from django.dispatch import receiver

from social.models import CoupleMomentPhotoModel, MomentDraftPhoto, MomentVideo, MomentVideoFile
from social.services.moment_service import queue_file_deletion


@receiver(post_delete, sender=CoupleMomentPhotoModel)
@receiver(post_delete, sender=MomentDraftPhoto)
def remove_moment_photo(sender, instance, **kwargs):
    queue_file_deletion(instance.image.name)


@receiver(post_delete, sender=CoupleMomentPhotoModel)
@receiver(post_delete, sender=MomentDraftPhoto)
def remove_unused_video(sender, instance, **kwargs):
    if instance.video_id and not (MomentDraftPhoto.objects.filter(video_id=instance.video_id).exists()
            or CoupleMomentPhotoModel.objects.filter(video_id=instance.video_id).exists()):
        MomentVideo.objects.filter(pk=instance.video_id).delete()


@receiver(post_delete, sender=MomentVideoFile)
def remove_video_file(sender, instance, **kwargs):
    queue_file_deletion(instance.file.name)

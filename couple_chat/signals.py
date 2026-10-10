from django.db import transaction
from django.db.models.signals import post_delete
from django.dispatch import receiver
from .models import Attachment, FileCleanup


@receiver(post_delete, sender=Attachment)
def queue_attachment_cleanup(sender,instance,**kwargs):
    from .media import cleanup_files, storage_name
    # A process can die after the immutable file write and before uploaded_at
    # commits. The reserved canonical path still lets cascades clean that file.
    FileCleanup.objects.get_or_create(name=instance.blob.name or storage_name(instance.room_id,instance.pk))
    transaction.on_commit(cleanup_files)

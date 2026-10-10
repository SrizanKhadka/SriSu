"""Private opaque-byte transport. Actual media cryptography has native tests."""
from datetime import timedelta
import hashlib
from io import StringIO
from unittest.mock import patch
from uuid import uuid4
from django.core.management import call_command
from django.core.files.storage import default_storage
from django.test import override_settings
from django.utils import timezone
from rest_framework.test import APITestCase
from .models import Attachment, FileCleanup, Message
from .test_protocol import ProtocolFixtures, registration


@override_settings(COUPLE_CHAT_PREVIEW_ENABLED=True)
class MediaTests(ProtocolFixtures,APITestCase):
    ciphertext=b"opaque-test-ciphertext"*4

    def reserve(self,**updates):
        data={"device_id":self.first_keys["device_id"],"attachment_id":str(uuid4()),"message_id":str(uuid4()),
            "ciphertext_size":len(self.ciphertext),"sha256":hashlib.sha256(self.ciphertext).hexdigest(),**updates}
        response=self.first_client.post(self.path+"attachments/",data,format="json")
        self.assertEqual(response.status_code,200,response.data)
        self.assert_contract("attachment",response.data["data"])
        return data

    def path_for(self,data,action):return self.path+f"attachments/{data['attachment_id']}/{action}/"

    def upload(self,data,client=None,body=None):
        return (client or self.first_client).put(self.path_for(data,"upload"),self.ciphertext if body is None else body,
            content_type="application/octet-stream",HTTP_X_CHAT_DEVICE=self.first_keys["device_id"])

    def finish(self,data):
        self.assertEqual(self.upload(data).status_code,200)
        response=self.first_client.post(self.path_for(data,"finalize"),{"device_id":self.first_keys["device_id"]},format="json")
        self.assertEqual(response.status_code,200,response.data)
        body=self.envelope(operation_id=data["message_id"],target_id=data["message_id"],attachments=[data["attachment_id"]])
        self.send(body)
        return body

    def token(self,data):
        response=self.second_client.post(self.path_for(data,"capability"),{"device_id":self.second_keys["device_id"]},format="json")
        self.assertEqual(response.status_code,200,response.data)
        return response.data["data"]["capability"]

    def test_immutable_lifecycle_and_authorized_download(self):
        data=self.reserve()
        self.assertEqual(self.first_client.post(self.path+"attachments/",data,format="json").status_code,200)
        self.assertEqual(self.upload(data,body=b"different").status_code,409)
        self.assertEqual(self.second_client.post(self.path_for(data,"capability"),{"device_id":self.second_keys["device_id"]},format="json").status_code,404)
        body=self.finish(data)
        self.assertEqual(self.upload(data).status_code,200)
        self.send(body)
        self.assertEqual(Attachment.objects.count(),1)
        capability=self.token(data)
        response=self.second_client.get(self.path_for(data,"content"),HTTP_X_CHAT_MEDIA_CAPABILITY=capability)
        self.assertEqual(response.status_code,200)
        self.assertEqual(b"".join(response.streaming_content),self.ciphertext)
        self.assertEqual(response["Cache-Control"],"private, no-store")
        op=next(c["operation"] for c in self.sync().data["data"]["changes"] if c.get("operation"))
        self.assertEqual(op["attachments"],[data["attachment_id"]])

    def test_unrelated_accounts_and_unfinished_or_cross_message_references_are_denied(self):
        data=self.reserve()
        self.assertEqual(self.upload(data,client=self.third_client).status_code,404)
        self.assertEqual(self.upload(data,client=self.second_client).status_code,409)
        self.send(self.envelope(attachments=[data["attachment_id"]]),status=409)
        self.assertFalse(Message.objects.exists())
        self.finish(data)
        token=self.token(data)
        self.assertEqual(self.third_client.get(self.path_for(data,"content"),HTTP_X_CHAT_MEDIA_CAPABILITY=token).status_code,403)
        self.assertEqual(self.second_client.get(self.path_for(data,"content")).status_code,403)
        self.assertEqual(self.second_client.get(self.path_for(data,"content"),HTTP_X_CHAT_MEDIA_CAPABILITY=token+"changed").status_code,403)

    def test_deletion_revokes_already_issued_capability_and_retries_file_cleanup(self):
        data=self.reserve(); self.finish(data); token=self.token(data)
        with patch("couple_chat.media.default_storage.delete",side_effect=OSError("synthetic storage unavailable")):
            with self.captureOnCommitCallbacks(execute=True):self.send(self.envelope(kind="delete",target=data["message_id"]))
        attachment=Attachment.objects.get()
        self.assertIsNotNone(attachment.deleted_at)
        self.assertTrue(default_storage.exists(attachment.blob.name))
        self.assertEqual(self.second_client.get(self.path_for(data,"content"),HTTP_X_CHAT_MEDIA_CAPABILITY=token).status_code,404)
        call_command("cleanup_couple_chat_media",stdout=StringIO())
        self.assertFalse(default_storage.exists(attachment.blob.name))

    def test_expiry_cancellation_and_quotas(self):
        with override_settings(COUPLE_CHAT_ROOM_MEDIA_BYTES=len(self.ciphertext)):
            data=self.reserve()
            response=self.first_client.post(self.path+"attachments/",{**data,"attachment_id":str(uuid4())},format="json")
            self.assertEqual(response.status_code,409)
        self.assertEqual(self.upload(data).status_code,200)
        Attachment.objects.filter(pk=data["attachment_id"]).update(expires_at=timezone.now()-timedelta(seconds=1))
        self.assertEqual(self.upload(data).status_code,409)
        call_command("cleanup_couple_chat_media",dry_run=True,stdout=StringIO())
        self.assertIsNone(Attachment.objects.get().deleted_at)
        call_command("cleanup_couple_chat_media",stdout=StringIO())
        self.assertIsNotNone(Attachment.objects.get().deleted_at)
        data=self.reserve()
        for _ in range(2):
            response=self.first_client.post(self.path_for(data,"cancel"),{"device_id":self.first_keys["device_id"]},format="json")
            self.assertEqual(response.status_code,200)
        self.assertEqual(self.upload(data).status_code,409)

    def test_device_replacement_and_unlink_revoke_media(self):
        data=self.reserve();self.finish(data);token=self.token(data)
        replacement=registration();replacement["replace_device_id"]=self.second_keys["device_id"]
        self.assertEqual(self.second_client.post("/api/couple-chat/v1/devices/",replacement,format="json").status_code,200)
        self.assertEqual(self.second_client.get(self.path_for(data,"content"),HTTP_X_CHAT_MEDIA_CAPABILITY=token).status_code,409)
        self.assertEqual(self.second_client.post(self.path_for(data,"capability"),{"device_id":replacement["device_id"]},format="json").status_code,404)
        self.room.revoked_at=timezone.now();self.room.save(update_fields=["revoked_at"])
        self.assertEqual(self.upload(data).status_code,404)

    def test_cascade_deletion_queues_a_retryable_file_job(self):
        data=self.reserve();self.finish(data)
        name=Attachment.objects.get().blob.name
        with patch("couple_chat.media.default_storage.delete",side_effect=OSError("synthetic")):
            with self.captureOnCommitCallbacks(execute=True):Attachment.objects.get().delete()
        self.assertTrue(FileCleanup.objects.filter(name=name).exists())
        call_command("cleanup_couple_chat_media",stdout=StringIO())
        self.assertFalse(FileCleanup.objects.exists());self.assertFalse(default_storage.exists(name))

    def test_direct_media_urls_are_never_public(self):
        data=self.reserve();self.finish(data)
        name=Attachment.objects.get().blob.name
        self.assertEqual(self.client.get("/media/"+name).status_code,404)
        self.assertEqual(self.client.get("/media/"+name.upper()).status_code,404)

    def test_file_persisted_before_commit_is_adopted_without_duplicate_storage(self):
        data=self.reserve()
        name=f"couples/couple_chat_private/{self.room.pk}/{data['attachment_id']}.bin"
        from django.core.files.base import ContentFile
        self.assertEqual(default_storage.save(name,ContentFile(self.ciphertext)),name)
        self.assertEqual(self.upload(data).status_code,200)
        self.assertEqual(Attachment.objects.get().blob.name,name)

    def test_crash_before_upload_commit_is_still_cleaned_on_cancel_or_cascade(self):
        from django.core.files.base import ContentFile
        for cascade in (False,True):
            data=self.reserve()
            attachment=Attachment.objects.get(pk=data["attachment_id"])
            name=attachment.blob.name
            self.assertTrue(name)
            default_storage.save(name,ContentFile(self.ciphertext))
            self.assertIsNone(attachment.uploaded_at)
            with self.captureOnCommitCallbacks(execute=True):
                if cascade:
                    attachment.delete()
                else:
                    response=self.first_client.post(self.path_for(data,"cancel"),{"device_id":self.first_keys["device_id"]},format="json")
                    self.assertEqual(response.status_code,200)
            self.assertFalse(default_storage.exists(name))

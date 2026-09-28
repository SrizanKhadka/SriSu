"""Server-managed, single-use SMS proofs. Twilio Messaging is delivery only."""
import math
import secrets
import uuid
from datetime import timedelta

from django.conf import settings
from django.db import transaction
from django.utils.crypto import constant_time_compare, salted_hmac
from django.utils.timezone import now
from rest_framework.exceptions import APIException, Throttled, ValidationError
from twilio.http.http_client import TwilioHttpClient
from twilio.rest import Client

from authentication.models import OtpModel, OtpRequestBudget, UserModel
from utils.choices import OtpStatusChoices


class DeliveryUnavailable(APIException):
    status_code = 503
    default_detail = "Verification delivery is unavailable. Please try again later."
    default_code = "temporarily_unavailable"


class DeliveryPending(APIException):
    status_code = 409
    default_detail = "Verification request is still being processed. Retry this request."
    default_code = "request_pending"


def proof(phone, challenge, code):
    return salted_hmac("srisu.otp.login.v1", f"{phone}:{challenge}:{code}", algorithm="sha256").hexdigest()


def budget(scope, value):
    key = scope + ":" + salted_hmac("srisu.otp.budget", value, algorithm="sha256").hexdigest()
    row, _ = OtpRequestBudget.objects.get_or_create(key=key)
    return OtpRequestBudget.objects.select_for_update().get(pk=row.pk)


def reserve_budget(row, limit, seconds, at):
    if at >= row.window_start + timedelta(seconds=seconds):
        row.window_start, row.count = at, 0
    if row.count >= limit:
        raise Throttled(math.ceil((row.window_start + timedelta(seconds=seconds) - at).total_seconds()))
    row.count += 1
    row.save(update_fields=["window_start", "count"])


def metadata(row, at=None):
    at = at or now()
    return {"challenge_id": str(row.challenge_id), "expires_at": row.expires_at.isoformat(),
            "resend_at": row.resend_at.isoformat(), "server_time": at.isoformat(),
            "retry_after_seconds": max(0, math.ceil((row.resend_at - at).total_seconds()))}


def deliver(phone, code):
    client = Client(settings.TWILIO_ACCOUNT_SID, settings.TWILIO_AUTH_TOKEN,
                    http_client=TwilioHttpClient(timeout=8, max_retries=0))
    client.messages.create(body=f"Your SriSu Verification Code is {code}",
                           from_=settings.TWILIO_PHONE_NUMBER, to=phone)


def request_code(phone, ip, request_id=None):
    at = now()
    with transaction.atomic():
        # Lock ordering is global -> source -> phone. Reservations include failed delivery.
        global_budget = budget("global", "sms")
        source_budget = budget("source", ip)
        row, _ = OtpModel.objects.get_or_create(phone_number=phone, defaults={"otp_code": ""})
        row = OtpModel.objects.select_for_update().get(pk=row.pk)
        if request_id and row.request_id == request_id:
            if row.delivery_state == "sent" and row.expires_at and at < row.expires_at:
                return metadata(row, at)
            if row.delivery_state == "sending" and row.expires_at and at < row.expires_at:
                raise DeliveryPending()
            raise DeliveryUnavailable()
        if row.resend_at and at < row.resend_at:
            raise Throttled(math.ceil((row.resend_at - at).total_seconds()))
        if at >= row.last_request_time + timedelta(minutes=10):
            row.otp_attempts, row.last_request_time = 0, at
        if row.otp_attempts >= 3:
            raise Throttled(max(1, math.ceil((row.last_request_time + timedelta(minutes=10) - at).total_seconds())))
        reserve_budget(global_budget, settings.OTP_GLOBAL_HOURLY_LIMIT, 3600, at)
        reserve_budget(source_budget, settings.OTP_IP_HOURLY_LIMIT, 3600, at)
        code = f"{secrets.randbelow(1000000):06d}"
        row.challenge_id, row.request_id = uuid.uuid4(), request_id
        row.otp_code = proof(phone, row.challenge_id, code)
        row.otp_status, row.delivery_state = OtpStatusChoices.NEW, "sending"
        row.failed_attempts, row.otp_attempts = 0, row.otp_attempts + 1
        row.expires_at, row.resend_at = at + timedelta(minutes=5), at + timedelta(seconds=60)
        row.save()
        challenge = row.challenge_id
    try:
        deliver(phone, code)
    except Exception:
        # A timeout is ambiguous: never retry paid delivery automatically.
        OtpModel.objects.filter(pk=row.pk, challenge_id=challenge).update(
            delivery_state="failed", otp_status=OtpStatusChoices.EXPIRED, otp_code="")
        raise DeliveryUnavailable() from None
    OtpModel.objects.filter(pk=row.pk, challenge_id=challenge).update(delivery_state="sent")
    return metadata(row)


def verify_code(phone, code, challenge_id=None):
    at, error, user = now(), None, None
    with transaction.atomic():
        row = OtpModel.objects.select_for_update().filter(phone_number=phone).first()
        if row is None or (challenge_id and row.challenge_id != challenge_id):
            error = "invalid"
        elif row.otp_status != OtpStatusChoices.NEW or row.delivery_state != "sent":
            error = "consumed_or_unavailable"
        elif row.expires_at is None or at >= row.expires_at:
            row.otp_status, row.otp_code = OtpStatusChoices.EXPIRED, ""
            row.save(update_fields=["otp_status", "otp_code"])
            error = "expired"
        elif row.failed_attempts >= 5:
            error = "attempts_exhausted"
        elif not constant_time_compare(row.otp_code, proof(phone, row.challenge_id, code)):
            row.failed_attempts += 1
            row.save(update_fields=["failed_attempts"])
            error = "attempts_exhausted" if row.failed_attempts >= 5 else "invalid"
        else:
            user, _ = UserModel.objects.get_or_create(phone_number=phone)
            if not user.is_active:
                error = "unavailable"
            else:
                user.is_phone_verified = True
                user.save(update_fields=["is_phone_verified", "updated_date"])
            row.otp_status, row.otp_code = OtpStatusChoices.EXPIRED, ""
            row.save(update_fields=["otp_status", "otp_code"])
    # Raise after commit so failed-attempt accounting and consumption cannot roll back.
    if error:
        raise ValidationError({"otp_code": [ValidationError.default_detail]}, code=error)
    return user

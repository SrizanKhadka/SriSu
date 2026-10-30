"""Lazy ADC initialization. Never log SDK exceptions, target IDs or response bodies."""
from datetime import timedelta
from functools import lru_cache
from django.conf import settings
from django.utils import timezone
from django.utils.crypto import salted_hmac


class ProviderFailure(Exception):
    def __init__(self, code, retry_after=0):
        self.code = code
        self.retry_after = retry_after
        super().__init__(code)


@lru_cache(maxsize=1)
def app():
    import firebase_admin
    return firebase_admin.initialize_app(options={"projectId": settings.FIREBASE_PROJECT_ID, "httpTimeout": 15}, name="srisu-notifications")


def message(delivery, *, audible):
    from firebase_admin import messaging
    event, install = delivery.event, delivery.installation
    ttl = max(0, int((event.expires_at - timezone.now()).total_seconds()))
    group = salted_hmac("notification.group", str(event.couple_id or "activity")).hexdigest()[:32]
    # At most two FCM collapse groups per installation, while the OS groups each room.
    collapse = "chat" if event.kind in {"chat.message", "matrix.message"} else "activity"
    return messaging.Message(**{install.target_kind: install.target},
        notification=messaging.Notification(title="SriSu", body="You have an update. Open SriSu to view it."),
        data={"schema": "1", "delivery_id": str(delivery.pk), "expires_at": str(int(event.expires_at.timestamp()))},
        android=messaging.AndroidConfig(priority="high", ttl=timedelta(seconds=ttl), collapse_key=collapse,
            notification=messaging.AndroidNotification(channel_id="srisu_updates" if audible else "srisu_updates_quiet",
                tag=group, sound="default" if audible else None, visibility="private")),
        apns=messaging.APNSConfig(headers={"apns-push-type": "alert", "apns-priority": "10",
            "apns-expiration": str(int(event.expires_at.timestamp())), "apns-collapse-id": group},
            payload=messaging.APNSPayload(aps=messaging.Aps(thread_id=group, sound="default" if audible else None))))


def send(delivery, *, audible):
    from firebase_admin import exceptions, messaging
    try:
        messaging.send(message(delivery, audible=audible), app=app())
    except messaging.UnregisteredError:
        raise ProviderFailure("unregistered") from None
    except (messaging.SenderIdMismatchError, messaging.ThirdPartyAuthError, exceptions.PermissionDeniedError,
            exceptions.UnauthenticatedError):
        raise ProviderFailure("configuration") from None
    except (exceptions.ResourceExhaustedError, exceptions.UnavailableError, exceptions.InternalError,
            exceptions.DeadlineExceededError) as error:
        response = getattr(error, "http_response", None)
        header = response.headers.get("Retry-After", "") if response is not None else ""
        try:
            delay = max(60, int(header))
        except (ValueError, TypeError):
            from email.utils import parsedate_to_datetime
            try:
                delay = max(60, int((parsedate_to_datetime(header) - timezone.now()).total_seconds()))
            except (ValueError, TypeError, OverflowError):
                delay = 60
        raise ProviderFailure("transient", delay) from None
    except Exception:
        # INVALID_ARGUMENT may indicate our payload, not a bad registration. Never retire it.
        raise ProviderFailure("configuration") from None

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
    # OS identity is distinct from the bounded FCM transport collapse key.
    # A Plan must never replace a chat alert, nor another Plan's actionable entry.
    family = "moment-note" if event.kind in {"moment.appreciated", "moment.replied"} else event.kind.split('.')[0]
    group_source = (f"chat:{event.resource}" if event.kind in {"chat.message", "matrix.message"}
        else f"activity:{family}:{event.resource}")
    group = salted_hmac("notification.group", group_source).hexdigest()[:32]
    # Retained for legacy automatic-display clients. Schema 2 must not collapse
    # unrelated activity together: a status cannot erase a queued Spark or Challenge.
    collapse = "chat" if event.kind in {"chat.message", "matrix.message"} else "activity"
    from .previews import CAPABILITY, FALLBACKS
    title, body = FALLBACKS.get(event.kind, ("SriSu", "Open SriSu to see what's new."))
    if install.platform == "android" and CAPABILITY in install.capabilities:
        # Data-only delivery lets Android fetch authorized details and decrypt
        # messages locally, including when the activity/process is not running.
        return messaging.Message(**{install.target_kind: install.target},
            data={"schema": "2", "delivery_id": str(delivery.pk), "expires_at": str(int(event.expires_at.timestamp())),
                "kind": event.kind, "title": title, "body": body, "group": group,
                "installation_id": str(install.pk), "binding_version": str(delivery.binding_version),
                "created_at": str(event.matrix_timestamp_ms or int(event.created_at.timestamp() * 1000)),
                "audible": "true" if audible else "false"},
            # Transport urgency and sound are separate. Every eligible schema-2
            # message immediately produces a visible alert (or foreground banner),
            # even when its notification channel is quiet.
            android=messaging.AndroidConfig(priority="high", ttl=timedelta(seconds=ttl),
                collapse_key="chat" if collapse == "chat" else None))
    return messaging.Message(**{install.target_kind: install.target},
        notification=messaging.Notification(title=title, body=body),
        data={"schema": "1", "delivery_id": str(delivery.pk), "expires_at": str(int(event.expires_at.timestamp()))},
        android=messaging.AndroidConfig(priority="normal" if event.quiet else "high", ttl=timedelta(seconds=ttl), collapse_key=collapse,
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

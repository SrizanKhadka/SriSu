# Local test accounts

SriSu's local Docker environment can be populated with deterministic, synthetic
accounts for authentication, relationship, couple-profile, permission, and secure
chat testing:

```sh
docker compose exec -T web \
  python manage.py seed_local_test_accounts --confirm-local
```

The command is intentionally restricted to `DEBUG=true` with mock OTP delivery
and a valid six-digit mock code configured. It does not delete data, reset an
account, create a password, issue an authentication token, or print an OTP.
Re-running it reuses the same records and preserves profile edits and relationship
progress. It stops instead of unlinking a seed user who belongs to an unexpected
couple or overwriting a reserved phone or username owned by another local identity.

## Account scenarios

| Phone number | Name | Initial scenario |
| --- | --- | --- |
| `+9779800000101` | Asha Thapa | Member one of the Asha & Bikash couple |
| `+9779800000102` | Bikash Shrestha | Member two of the Asha & Bikash couple |
| `+9779800000103` | Mira Gurung | Member one of the Mira & Rohan couple |
| `+9779800000104` | Rohan Rai | Member two of the Mira & Rohan couple |
| `+9779800000105` | Nisha Karki | Receiver of Suman's pending invitation |
| `+9779800000106` | Suman KC | Sender of the pending invitation to Nisha |

The two accepted couples have distinct shared profiles, active memberships, a
relationship-scoped `ChatRoom`, and Matrix mapping records. This makes the first
couple useful for two-device chat testing and the second useful for visitor and
cross-couple authorization checks. Nisha and Suman exercise the real invitation
acceptance flow. If that invitation is accepted, a later seed run preserves the
accepted relationship rather than silently recreating the pending state.

Profiles deliberately use `profile_photo_skipped=true` and contain no downloaded
or third-party images. Upload profile and chat media through the app when testing
the real media path.

## Sign in locally

Use the normal app flow for every account:

1. Enter one of the phone numbers above and request a fresh OTP.
2. The local development default mock code is `123456`; no SMS is sent.
3. Complete verification using the challenge returned by `send-otp/`.

The mock still enforces challenge creation, expiry, resend cooldowns, attempt
limits, and phone/challenge binding. Restarting or recreating containers can make
an old challenge unusable, so request a new OTP rather than reusing an earlier
screen. See [authentication.md](authentication.md) for the exact API contract and
configuration.

## Confirm secure-chat readiness

The command reports how many seeded rooms are active when it finishes, but room
provisioning is asynchronous and that number can initially be `0/2`. The Docker
`matrix_reconciler` normally provisions new relationship rooms after the seed
transaction commits. Inspect current readiness with:

```sh
docker compose exec -T web python manage.py matrix_status
```

If the seed command reports success but a new mapping is still pending, allow the
reconciler to process it and check again. Treat only an active Matrix room as ready
for secure-message testing.

These accounts are development fixtures only. Never enable the command's required
mock/DEBUG settings or run it against staging or production data.

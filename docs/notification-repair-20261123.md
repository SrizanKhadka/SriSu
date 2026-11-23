# Android chat notification repair — 23 November 2026

## Deployed source

- Backend repository: `SrizanKhadka/SriSu`.
- Windows checkout: `D:\Djangos\SriSu`, Compose project `srisu`.
- Repair branch: `codex/notification-push-repair`.
- Application revision: `7a1846d9368c1bc7ac3e84295e231dadfe6275fa`.
  This merges notification rollout `2e47984` with current
  `dev-core-architecture` revision `797b0a0`, retaining its profile/interest fixes.
- Frontend inspected: `SriZan12/SriSu`, primary checkout on
  `dev-core-architecture` at `2d33ad0`. No frontend source or APK was changed.
  Installed device binaries were exercised, but their exact source commits were
  not independently established. No additional KMP worktree was created.
- Figma: no UI changes or new design references. Apple/APNs remains skipped.

## Confirmed blockers and repair

The running Windows web service had notifications disabled, Matrix chat push
disabled, and no configured AS ingress credential. Its notification worker had
been stopped for weeks. The ignored local environment now enables notifications,
the Matrix feed, and the `notifications` Compose profile. The existing local
Firebase sender credential is mounted read-only into the worker; credential
refresh and an FCM validation-only request passed. No credentials were committed.

The database already recorded migration
`0005_event_context_event_quiet_preferences_categories_and_more`, while the old
running branch only defined notification models through `0004`. Its Event model
omitted the required `context` and `quiet` columns, and its Preferences model
omitted `categories`. The AS endpoint returned 503 and created no notification
events. Deploying the combined revision restores agreement between code and the
existing schema. No migration reversal or database reset was performed.

Before repair, the Realme RMX3511 was in Vibrate mode with notification volume
zero, and its account-level Push notifications preference was off. The user
enabled Sound mode and raised volume; the account switch was enabled in SriSu.
Chat notifications were enabled and the conversation was not muted. The sender
emulator's account-level push preference was also enabled.

Both Mac AVDs (`SriSuHomeQA` and `SriSuNavigationQA`) use the AOSP Android 35
image and have neither Google Play services nor Play Store installed. They cannot
receive FCM. `SriSuNavigationQA` additionally had Android notification permission
denied. A Google APIs image installation ran out of Mac disk space while
extracting; its download and incomplete extraction were removed. The Home AVD
was restarted without wiping its data. A Google Play image and the existing
`srisu_notifications_v1` AVD were found on Windows, but were not run or validated
in this repair.

## Verification

- Disposable PostgreSQL suite: **421 tests passed** via
  `tools/auth_postgres_tests.py --postgres-bin /opt/homebrew/opt/postgresql@17/bin`.
- Workspace Django checks and migration consistency check passed.
- Deployed Django `check` and `migrate --check` passed.
- Synapse AS registration matched the message room; after repair, its queue was
  empty and service state was up. Worker and Synapse health checks passed.
- Three chat push deliveries were accepted by FCM; final status had no pending
  deliveries. Earlier foreground receipts alone were not counted as sound proof.
- A fresh labelled message sent from the Mac emulator reached the Realme while
  SriSu was in the background. Android posted it on `srisu_updates`, importance
  DEFAULT, with the default notification sound. The user confirmed hearing it.
- The notification was tapped through the Android shade. SriSu opened the chat
  containing the exact synthetic test message, with the composer visible.
- Phone-to-Mac-emulator FCM reception remains unverified and blocked by the AOSP
  images. Use a Google APIs/Google Play AVD for that direction. An open receiving
  chat intentionally stays quiet; closely spaced pushes also share the existing
  30-second audible-alert budget.

## Subsequent starts on this Windows host

Keep the backend on the deployed repair branch until it is merged into the
integration branch. Do not run a pre-catalogue backend against the migrated
database. From Windows PowerShell:

```powershell
Set-Location D:\Djangos\SriSu
git switch codex/notification-push-repair
git pull --ff-only
.\start-local-server.ps1 --with-notifications
docker compose exec web python manage.py notification_status
```

This checkout's ignored environment retains the notification flags, profile,
sender credential path, and Matrix feed configuration. Protected configuration
and database backups are retained under the Windows SriSu-tools backups folder.
Database volumes and existing private media were preserved.

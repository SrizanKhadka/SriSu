# Historical schema compatibility

This package retains only model declarations and migration history for installations
that applied the earlier notification/legacy chat migrations. It registers no signals,
API routes, sockets or workers. Matrix is the sole runtime chat transport. Existing
rows are retained for an explicitly reviewed backup/retention process; new notification
events use current couple membership instead. See `docs/notifications.md`.

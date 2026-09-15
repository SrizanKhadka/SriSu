"""Allow historical unlimited CharFields when replaying PostgreSQL migrations on SQLite."""
from django.db.backends.sqlite3.base import DatabaseWrapper as SQLiteDatabaseWrapper


class DatabaseWrapper(SQLiteDatabaseWrapper):
    data_types = {**SQLiteDatabaseWrapper.data_types, "CharField": "text"}

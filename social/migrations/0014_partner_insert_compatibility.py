"""Keep retained columns from the earlier chat-v2 branch insert-compatible.

That branch applied social.0014_relationship_lifecycle and
chat.0004_chat_v2_foundation. Their Python defaults are absent from PostgreSQL,
so current models cannot insert invitations or the legacy acceptance-room adapter.
Only supply missing database defaults on those two known, required columns.
Do not remove old columns, rows, constraints, counters or migration records.
"""
from django.db import migrations, router


def supply_retained_defaults(apps, schema_editor):
    connection = schema_editor.connection
    if connection.vendor != "postgresql":
        return
    for app, model_name, column, default in (
        ("social", "CoupleConnectionModel", "revision", 1),
        ("chat", "ChatRoom", "last_sequence", 0),
    ):
        model = apps.get_model(app, model_name)
        if not router.allow_migrate_model(connection.alias, model):
            continue
        table = model._meta.db_table
        with connection.cursor() as cursor:
            cursor.execute("""
                SELECT format_type(a.atttypid, a.atttypmod), a.attnotnull,
                       pg_get_expr(d.adbin, d.adrelid)
                FROM pg_attribute a
                LEFT JOIN pg_attrdef d ON d.adrelid = a.attrelid AND d.adnum = a.attnum
                WHERE a.attrelid = to_regclass(%s) AND a.attname = %s
                  AND NOT a.attisdropped
            """, [table, column])
            field = cursor.fetchone()
        if field is None or not field[1] or field[2] is not None:
            continue  # Fresh schemas, nullable columns and explicit defaults need no repair.
        if field[0] != "bigint":
            raise RuntimeError(f"Unexpected type for retained {table}.{column}; inspect before migration.")
        schema_editor.execute(
            f"ALTER TABLE {schema_editor.quote_name(table)} "
            f"ALTER COLUMN {schema_editor.quote_name(column)} SET DEFAULT {default}"
        )


class Migration(migrations.Migration):
    dependencies = [
        ("social", "0013_cover_bounds"),
        ("chat", "0003_couple_profile_cards"),
    ]
    operations = [
        # Retain these compatibility defaults on rollback: dropping them would
        # recreate the failure and serves no data-restoration purpose.
        migrations.RunPython(supply_retained_defaults, migrations.RunPython.noop),
    ]

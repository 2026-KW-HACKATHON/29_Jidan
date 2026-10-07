"""MySQL schema snapshot from information_schema, for comparing databases built by different paths.

Covers what `compare_metadata` misses: column order, collation, generated expressions,
CHECK bodies, index details and foreign key actions. Names of the schema itself are left out
so two databases compare equal.
"""
from sqlalchemy import text

QUERIES = {
    "tables": (
        "SELECT table_name, engine, table_collation FROM information_schema.tables "
        "WHERE table_schema = DATABASE() AND table_type = 'BASE TABLE'"
    ),
    "columns": (
        "SELECT table_name, ordinal_position, column_name, column_type, is_nullable, column_default, "
        "character_set_name, collation_name, extra, generation_expression "
        "FROM information_schema.columns WHERE table_schema = DATABASE()"
    ),
    "indexes": (
        "SELECT table_name, index_name, non_unique, seq_in_index, column_name, sub_part, collation, "
        "index_type, expression FROM information_schema.statistics WHERE table_schema = DATABASE()"
    ),
    "constraints": (
        "SELECT table_name, constraint_name, constraint_type, enforced "
        "FROM information_schema.table_constraints WHERE table_schema = DATABASE()"
    ),
    "checks": (
        "SELECT constraint_name, check_clause FROM information_schema.check_constraints "
        "WHERE constraint_schema = DATABASE()"
    ),
    "foreign_keys": (
        "SELECT k.table_name, k.constraint_name, k.ordinal_position, k.column_name, "
        "k.referenced_table_name, k.referenced_column_name, r.update_rule, r.delete_rule "
        "FROM information_schema.key_column_usage k JOIN information_schema.referential_constraints r "
        "ON r.constraint_schema = k.constraint_schema AND r.constraint_name = k.constraint_name "
        "WHERE k.table_schema = DATABASE() AND k.referenced_table_name IS NOT NULL"
    ),
}


def mysql_schema_snapshot(connection) -> dict[str, list[tuple]]:
    """Sorted rows per category for the connection's current database."""
    return {
        name: sorted(tuple("" if value is None else str(value) for value in row)
                     for row in connection.execute(text(sql)))
        for name, sql in QUERIES.items()
    }


def snapshot_diff(left: dict, right: dict) -> list[str]:
    """Human-readable differences; empty when the schemas match."""
    lines = []
    for name in QUERIES:
        a, b = set(left[name]), set(right[name])
        lines += [f"{name} only left: {row}" for row in sorted(a - b)]
        lines += [f"{name} only right: {row}" for row in sorted(b - a)]
    return lines

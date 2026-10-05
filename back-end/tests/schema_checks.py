"""Compare CHECK constraints of the models with a migrated database.

`alembic.compare_metadata` ignores CHECK constraints, so a CHECK added, dropped or edited in
only one of models/migration would go unnoticed. Names must match exactly and the SQL text
must match after normalization.

Normalization (applied to both sides):
- keywords, function names and identifiers are lower-cased; backticks/quotes around
  identifiers are removed; runs of whitespace disappear; `!=` becomes `<>`;
- MySQL's `_utf8mb4` introducer in front of string literals is dropped;
- string literals are kept verbatim and case-sensitive, so a changed enum value is a difference.
SQLite keeps the text we wrote, so it is compared with parentheses intact. MySQL rewrites the
expression (it parenthesizes every comparison and operand), so for MySQL parentheses are not
compared; every identifier, literal, operator and their order still is. The residual
looseness is precedence-only changes such as `(a OR b) AND c` versus `a OR (b AND c)`.
"""

import re

from sqlalchemy import CheckConstraint

TOKEN = re.compile(
    r"""
    (?P<literal>'(?:[^']|'')*')
  | `(?P<quoted>[^`]+)`
  | "(?P<dquoted>[^"]+)"
  | (?P<word>[A-Za-z_][A-Za-z_0-9]*)
  | (?P<number>\d+(?:\.\d+)?)
  | (?P<op><>|!=|>=|<=|[=<>+\-*/%,()])
  | (?P<space>\s+)
    """,
    re.VERBOSE,
)


def normalize_check_sql(sql: str, keep_parentheses: bool) -> str:
    tokens: list[str] = []
    position = 0
    while position < len(sql):
        match = TOKEN.match(sql, position)
        if match is None:
            raise ValueError(f"Cannot tokenize CHECK SQL at {sql[position:position + 20]!r}")
        position = match.end()
        kind = match.lastgroup
        text = match.group(kind)
        if kind == "space":
            continue
        if kind == "literal":
            tokens.append(text)
        elif kind in ("quoted", "dquoted", "word"):
            if kind == "word" and text.startswith("_") and sql[position:position + 1] == "'":
                continue  # charset introducer such as _utf8mb4'x'
            tokens.append(text.lower())
        elif kind == "op":
            if text in "()" and not keep_parentheses:
                continue
            tokens.append("<>" if text == "!=" else text)
        else:
            tokens.append(text)
    return " ".join(tokens)


def model_checks(metadata) -> dict[tuple[str, str], str]:
    """{(table, constraint name): raw SQL} for every CheckConstraint in the metadata."""
    checks = {}
    for table in metadata.tables.values():
        for constraint in table.constraints:
            if isinstance(constraint, CheckConstraint):
                assert constraint.name, f"unnamed CHECK on {table.name}"
                checks[(table.name, str(constraint.name))] = str(constraint.sqltext)
    return checks


def database_checks(inspector) -> dict[tuple[str, str], str]:
    checks = {}
    for table in inspector.get_table_names():
        if table == "alembic_version":
            continue
        for item in inspector.get_check_constraints(table):
            assert item["name"], f"unnamed CHECK in database table {table}"
            checks[(table, item["name"])] = item["sqltext"]
    return checks


def check_differences(
    model: dict[tuple[str, str], str], database: dict[tuple[str, str], str],
    keep_parentheses: bool,
) -> list[str]:
    problems = []
    for key in sorted(model.keys() - database.keys()):
        problems.append(f"CHECK {key[0]}.{key[1]} is in the models but missing from the database")
    for key in sorted(database.keys() - model.keys()):
        problems.append(f"CHECK {key[0]}.{key[1]} is in the database but missing from the models")
    for key in sorted(model.keys() & database.keys()):
        expected = normalize_check_sql(model[key], keep_parentheses)
        actual = normalize_check_sql(database[key], keep_parentheses)
        if expected != actual:
            problems.append(
                f"CHECK {key[0]}.{key[1]} differs: models {expected!r} vs database {actual!r}"
            )
    return problems

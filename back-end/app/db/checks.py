"""CHECK expressions whose text differs per database so that both enforce the same rule.

SQLite and MySQL disagree on string semantics: `LENGTH` counts characters in SQLite but bytes in
MySQL, `TRIM` removes only U+0020 in both, and MySQL's default collation treats zero-width and
control characters as equal to the empty string. `DialectSql` renders one text per dialect;
migrations repeat the same texts and `tests/test_schema_drift.py` compares them per dialect.
"""

from sqlalchemy.ext.compiler import compiles
from sqlalchemy.sql.elements import ColumnElement
from sqlalchemy.sql.sqltypes import Boolean

# Unicode White_Space (what MySQL's ICU `[:space:]` matches), spelled as code points for SQLite's
# `TRIM(x, set)`. Verified identical to ICU for every code point below U+3100.
_WHITESPACE_CODE_POINTS = (
    9, 10, 11, 12, 13, 32, 133, 160, 5760, *range(8192, 8203), 8232, 8233, 8239, 8287, 12288,
)
SQLITE_WHITESPACE = f"char({','.join(map(str, _WHITESPACE_CODE_POINTS))})"


class DialectSql(ColumnElement):
    """A boolean SQL expression with one text for MySQL/MariaDB and one for everything else."""

    inherit_cache = True
    type = Boolean()

    def __init__(self, *, mysql: str, sqlite: str):
        self.mysql = mysql
        self.sqlite = sqlite


@compiles(DialectSql)
def _compile_default(element: DialectSql, compiler, **_kw) -> str:
    return element.sqlite


@compiles(DialectSql, "mysql")
@compiles(DialectSql, "mariadb")
def _compile_mysql(element: DialectSql, compiler, **_kw) -> str:
    return element.mysql


def digits_only(column: str, length: int) -> DialectSql:
    """Exactly `length` ASCII digits (full-width digits, letters, spaces, hyphens are rejected)."""
    return DialectSql(
        mysql=f"CHAR_LENGTH({column}) = {length} AND REGEXP_LIKE({column}, '^[0-9]{{{length}}}$')",
        sqlite=f"LENGTH({column}) = {length} AND {column} NOT GLOB '*[^0-9]*'",
    )


def not_blank(column: str, nullable: bool = False) -> DialectSql:
    """Contains a non-whitespace character (Unicode White_Space, not only U+0020).

    With `nullable`, NULL is accepted too (the column is optional)."""
    prefix = f"{column} IS NULL OR " if nullable else ""
    return DialectSql(
        mysql=f"{prefix}REGEXP_LIKE({column}, '[^[:space:]]')",
        sqlite=f"{prefix}TRIM({column}, {SQLITE_WHITESPACE}) <> ''",
    )

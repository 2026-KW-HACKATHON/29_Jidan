"""Collection-time audit: every test that touches MySQL carries the `mysql` marker.

The marker drives `-m mysql`, the skip/exit logic in tests/conftest.py and the separate
"mysql: ran=..." summary. `db_engine`/`session` add it through their own params, but a test that
overrides them with `@pytest.mark.parametrize("db_engine", ["mysql"], indirect=True)` replaces
those params and silently loses the marker (three lease tests did). Loaded by
tests/test_mysql_markers.py as `pytest --collect-only -p tests.mysql_marker_audit`; writes the
number of MySQL tests checked on the first line ("checked=N"), then the node IDs of unmarked
ones, one per line, to JIDAN_MARKER_AUDIT_OUT.
"""

import os

MYSQL_PARAMETERS = ("db_engine", "session")
MYSQL_FIXTURES = ("mysql_schema", "mysql_engine", "mysql_session")


def uses_mysql(item) -> bool:
    callspec = getattr(item, "callspec", None)
    params = callspec.params if callspec is not None else {}
    if any(params.get(name) == "mysql" for name in MYSQL_PARAMETERS):
        return True
    return any(name in getattr(item, "fixturenames", ()) for name in MYSQL_FIXTURES)


def unmarked(items) -> list[str]:
    return [item.nodeid for item in items if uses_mysql(item) and item.get_closest_marker("mysql") is None]


def pytest_collection_finish(session):
    path = os.getenv("JIDAN_MARKER_AUDIT_OUT")
    if path:
        with open(path, "w", encoding="utf-8") as out:
            checked = sum(uses_mysql(item) for item in session.items)
            out.write(f"checked={checked}\n" + "".join(f"{nodeid}\n" for nodeid in unmarked(session.items)))

"""Marks work started by an operator command line, not by a request or a background job.

`python -m app.demo_seed` and the demo E2E cleanup delete whole demo accounts by foreign key
ranges, children first, in one run on a non-production database (`demo_seed.check_target`).
Their entry points run inside `operator_cli()`. The MySQL lock scope guard
(tests/lock_scope.py) exempts only statements issued while it is active, so a request path
that happens to call the same helpers is still checked.
"""

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar

_active: ContextVar[bool] = ContextVar("jidan_operator_cli", default=False)


@contextmanager
def operator_cli() -> Iterator[None]:
    token = _active.set(True)
    try:
        yield
    finally:
        _active.reset(token)


def operator_cli_active() -> bool:
    return _active.get()

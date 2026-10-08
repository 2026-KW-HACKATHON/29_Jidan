"""Whether a database error means the database cannot serve us right now (503), as opposed to
a fault of the statement or its data (which stays what it is).

Decided by the driver's error code, never by the exception class: PyMySQL reports CHECK
violations (3819), bad values (1366) and lock waits (1205) as OperationalError too, and those
are not unavailability. Only the codes below are.
"""

from sqlalchemy.exc import DBAPIError

# MySQL client (2xxx) and server (1xxx) codes for "the server cannot take this statement now".
UNAVAILABLE_MYSQL_CODES = frozenset({
    2002,  # CR_CONNECTION_ERROR: cannot connect through the local socket
    2003,  # CR_CONN_HOST_ERROR: cannot connect to the server host
    2006,  # CR_SERVER_GONE_ERROR: the server has gone away
    2013,  # CR_SERVER_LOST: connection lost during the query
    2055,  # CR_SERVER_LOST_EXTENDED: connection lost, with the system error
    1040,  # ER_CON_COUNT_ERROR: too many connections
    1053,  # ER_SERVER_SHUTDOWN: server shutdown in progress
    1021,  # ER_DISK_FULL: disk full, the server is waiting for space
    1114,  # ER_RECORD_FILE_FULL: the table is full
    1041,  # ER_OUT_OF_RESOURCES: the server is out of memory
    4031,  # ER_CLIENT_INTERACTION_TIMEOUT (MySQL 8.0.24+): the server closed an idle connection
    1927,  # ER_CONNECTION_KILLED: the connection was killed (failover, admin kill); not a query
           # interruption (1317), which is a statement-level cancel and stays what it is
})

# SQLite primary result codes (extended codes keep them in the low byte). BUSY/LOCKED are lock
# contention, answered like MySQL lock waits; READONLY is a configuration fault, not transient.
_SQLITE_IOERR, _SQLITE_FULL, _SQLITE_CANTOPEN = 10, 13, 14
UNAVAILABLE_SQLITE_CODES = frozenset({_SQLITE_IOERR, _SQLITE_FULL, _SQLITE_CANTOPEN})
_SQLITE_MESSAGES = ("disk i/o error", "database or disk is full", "unable to open database file")


def is_unavailable(error: DBAPIError) -> bool:
    original = error.orig
    args = getattr(original, "args", ())
    if args and isinstance(args[0], int):  # PyMySQL: (code, message)
        return args[0] in UNAVAILABLE_MYSQL_CODES
    code = getattr(original, "sqlite_errorcode", None)
    if isinstance(code, int):
        return code & 0xFF in UNAVAILABLE_SQLITE_CODES
    text = str(original).lower()  # sqlite3 errors raised without a result code
    return any(message in text for message in _SQLITE_MESSAGES)

"""Exact e-mail equality in SQL, shared by invitations, the inbox, worker management and
notifications (kept apart from those modules so none of them imports another for it)."""
from sqlalchemy import and_, func


def email_is(column, email: str):
    """SQL: `column` is exactly the normalized `email`.

    E-mail columns use utf8mb4_0900_as_ci on MySQL (`email_string`): accents and ß no longer
    fold, but `=` still equates full-width letters with ASCII ('ｊｏｓｅ@' = 'jose@') and ignores
    zero-width characters. Google e-mails and rows stored before input validation are not
    limited to ASCII, so comparing the UTF-8 bytes too makes the match exact on every database,
    as acceptance's Python comparison is. The plain `=` stays first so an index on the column
    can still narrow the rows.
    """
    return and_(column == email, func.hex(column) == email.encode("utf-8").hex().upper())

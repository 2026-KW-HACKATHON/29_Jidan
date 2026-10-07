#!/usr/bin/env python3
"""Validate private raw Compose environment files without displaying their contents."""
from pathlib import Path
import sys

BACK_END = Path(__file__).resolve().parents[2] / 'back-end'
ORIGINS = {
    'dev': 'https://dev-jidan.leehyowon14.dev',
    'production': 'https://jidan.leehyowon14.dev',
}


def raw_values(path, name):
    """Every raw value assigned to `name`, or None if the file cannot be read."""
    try:
        # Raw env lines use newlines; Unicode separators remain part of the value.
        lines = Path(path).read_text(encoding='utf-8').split('\n')
    except (OSError, UnicodeError):
        return None
    values = []
    for line in lines:
        if line.lstrip().startswith('#'):
            continue
        key, _, value = line.partition('=')
        if key.strip() == name:
            values.append(value)
    return values


def valid_allowed_origins(environment, path):
    expected = ORIGINS.get(environment)
    if expected is None:
        return False
    values = raw_values(path, 'ALLOWED_ORIGINS')
    # Compose uses format: raw. Quotes, interpolation and lists are deliberately
    # rejected: deployed environments must each allow exactly their own origin.
    return values == [expected]


def valid_admin_password_hash(path):
    # One supported hash from `python -m app.admin_password`; never display its value.
    values = raw_values(path, 'ADMIN_PASSWORD_HASH')
    if values is None or len(values) != 1:
        return False
    # The login parser uses only the standard library, so the deployment host needs no API
    # dependencies. Imported here: frontend-only checkouts never reach this backend check.
    sys.path.insert(0, str(BACK_END))
    try:
        from app.admin_password_config import parse_password_hash
        parse_password_hash(values[0])
    except (ImportError, ValueError):
        return False
    finally:
        sys.path.remove(str(BACK_END))
    return True


def main(args):
    if len(args) != 2 or not valid_allowed_origins(args[0], args[1]):
        print('Invalid backend ALLOWED_ORIGINS; configure the single environment origin.',
              file=sys.stderr)
        return 2
    if not valid_admin_password_hash(args[1]):
        print('Invalid backend ADMIN_PASSWORD_HASH; configure one supported hash from '
              '`python -m app.admin_password`.', file=sys.stderr)
        return 2
    return 0


if __name__ == '__main__':
    raise SystemExit(main(sys.argv[1:]))

#!/usr/bin/env python3
"""Validate private raw Compose environment files without displaying their contents."""
from pathlib import Path
import sys

ORIGINS = {
    'dev': 'https://dev-jidan.leehyowon14.dev',
    'production': 'https://jidan.leehyowon14.dev',
}


def valid_allowed_origins(environment, path):
    expected = ORIGINS.get(environment)
    if expected is None:
        return False
    try:
        # Raw env lines use newlines; Unicode separators remain part of the value.
        lines = Path(path).read_text(encoding='utf-8').split('\n')
    except (OSError, UnicodeError):
        return False
    values = []
    for line in lines:
        if line.lstrip().startswith('#'):
            continue
        key, _, value = line.partition('=')
        if key.strip() == 'ALLOWED_ORIGINS':
            values.append(value)
    # Compose uses format: raw. Quotes, interpolation and lists are deliberately
    # rejected: deployed environments must each allow exactly their own origin.
    return values == [expected]


def main(args):
    if len(args) != 2 or not valid_allowed_origins(args[0], args[1]):
        print('Invalid backend ALLOWED_ORIGINS; configure the single environment origin.',
              file=sys.stderr)
        return 2
    return 0


if __name__ == '__main__':
    raise SystemExit(main(sys.argv[1:]))

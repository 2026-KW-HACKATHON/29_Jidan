import base64

import pytest

from app.admin_password_config import parse_password_hash


def encoded(rounds="600000", salt=b"s" * 16, digest=b"d" * 32):
    return "$".join(("pbkdf2_sha256", rounds, base64.b64encode(salt).decode(),
                     base64.b64encode(digest).decode()))


@pytest.mark.parametrize("rounds", ["600000", "2000000"])
@pytest.mark.parametrize("salt_size", [16, 64])
def test_accepts_work_factor_and_salt_boundaries(rounds, salt_size):
    assert parse_password_hash(encoded(rounds, b"s" * salt_size)) == (int(rounds), b"s" * salt_size, b"d" * 32)


@pytest.mark.parametrize("value", [
    "", "do-not-print-secret", encoded().replace("pbkdf2_sha256", "sha256"),
    *[encoded(rounds) for rounds in ["599999", "2000001", "0", "-1", "x", " 600000", "+600000", "0600000"]],
    *[encoded(salt=b"s" * size) for size in [0, 15, 65]],
    *[encoded(digest=b"d" * size) for size in [0, 31, 33]],
    encoded() + "$extra", encoded() + "=", encoded() + "\n",
    encoded().replace("c3Nzc3Nzc3Nzc3Nzc3Nzcw==", "not-base64!"),
])
def test_rejects_invalid_configuration_without_exposing_value(value):
    with pytest.raises(ValueError, match="^Invalid administrator password configuration$"):
        parse_password_hash(value)

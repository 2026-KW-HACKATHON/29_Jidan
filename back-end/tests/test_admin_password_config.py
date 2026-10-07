"""`app.admin_password_config`: the scrypt hash format shared by login, health and the deploy gate."""

import subprocess
import sys
from pathlib import Path

import pytest

from app import admin_password
from app.admin_password_config import AdminPasswordConfigError, parse_password_hash

BACK_END = Path(__file__).resolve().parents[1]
PASSWORD = "config-check-password"
VALID = admin_password.hash_password(PASSWORD, log2_n=admin_password.MIN_LOG2_N)
SALT, KEY = VALID.split("$")[4:]


def test_generated_hash_round_trips():
    salt, log2_n, r, p, key = parse_password_hash(VALID)
    assert (log2_n, r, p) == (admin_password.MIN_LOG2_N, 8, 1)
    assert len(salt) == admin_password.SALT_BYTES and len(key) == admin_password.KEY_BYTES


def test_padded_base64url_and_boundary_parameters_are_accepted():
    pad = "=" * (-len(SALT) % 4)
    assert parse_password_hash(f"scrypt$14$8$1${SALT}{pad}${KEY}=")[1] == 14
    # OpenSSL needs log2 N < 16 * r: r=1 tops out at 2**15; with r=2 memory is the limit.
    assert parse_password_hash(f"scrypt$15$1$16${SALT}${KEY}")[1:4] == (15, 1, 16)
    assert parse_password_hash(f"scrypt$19$2$1${SALT}${KEY}")[1:4] == (19, 2, 1)
    assert parse_password_hash(f"scrypt$14$32$1${SALT}${KEY}")[2] == 32


@pytest.mark.parametrize("stored", [
    "", " \r\n", "secret-do-not-print",
    f"scrypt$14$8$1${SALT}",  # missing key
    f"scrypt$14$8$1${SALT}${KEY}$x",
    f"SCRYPT$14$8$1${SALT}${KEY}",
    f"scrypt$13$8$1${SALT}${KEY}", f"scrypt$21$1$1${SALT}${KEY}",
    f"scrypt$14$0$1${SALT}${KEY}", f"scrypt$14$33$1${SALT}${KEY}",
    f"scrypt$14$8$0${SALT}${KEY}", f"scrypt$14$8$17${SALT}${KEY}",
    f"scrypt$+14$8$1${SALT}${KEY}", f"scrypt$ 14$8$1${SALT}${KEY}", f"scrypt$1_4$8$1${SALT}${KEY}",
    f"scrypt$١٤$8$1${SALT}${KEY}",
    f"scrypt$14$8$1${SALT[:-2]}${KEY}",  # 15-byte salt
    f"scrypt$14$8$1${SALT}${KEY[:-1]}", f"scrypt$14$8$1${SALT}${KEY}AA",
    f"scrypt$14$8$1${SALT}${KEY[:-1]}+", f"scrypt$14$8$1${SALT}${KEY[:-1]}/",
    f"scrypt$14$8$1${SALT}${KEY}===", f"scrypt$14$8$1${SALT}$={KEY}",
    f"scrypt$14$8$1${SALT} ${KEY}", f"scrypt$14$8$1${SALT}${KEY[:20]}\n{KEY[20:]}",
    f"scrypt$18$8$1${SALT}${KEY}",  # 256 MiB+: every login would fail
    # N >= 2**(16 * r): OpenSSL refuses these whatever the memory limit.
    f"scrypt$16$1$1${SALT}${KEY}", f"scrypt$20$1$1${SALT}${KEY}",
])
def test_malformed_hash_is_rejected_without_echoing_it(stored):
    with pytest.raises(AdminPasswordConfigError) as caught:
        parse_password_hash(stored)
    assert isinstance(caught.value, ValueError)
    assert str(caught.value) in ("ADMIN_PASSWORD_HASH is not set", "ADMIN_PASSWORD_HASH is malformed")
    # Neither the message nor a chained parsing error carries the configured value.
    assert caught.value.__cause__ is None
    assert caught.value.__context__ is None or caught.value.__suppress_context__


def test_parser_imports_without_api_dependencies():
    # deploy/scripts/check_runtime_env.py runs on the deploy host with only the standard library.
    code = (
        "import sys; sys.path.insert(0, sys.argv[1]); import app.admin_password_config; "
        "bad = sorted(m for m in sys.modules if m.split('.')[0] in "
        "{'fastapi', 'sqlalchemy', 'pydantic', 'pymysql', 'starlette'} or m in {'app.errors', 'app.ratelimit'}); "
        "print(bad)"
    )
    result = subprocess.run([sys.executable, "-I", "-c", code, str(BACK_END)], capture_output=True, text=True,
                            check=True)
    assert result.stdout.strip() == "[]"


@pytest.mark.parametrize("log2_n", [14, 15, 16, 17])
@pytest.mark.parametrize("r", [1, 2])
@pytest.mark.parametrize("p", [1, 16])
def test_parser_accepts_exactly_what_openssl_scrypt_can_derive(log2_n, r, p):
    # Accepted parameters must work at login; rejected ones are those OpenSSL refuses.
    stored = f"scrypt${log2_n}${r}${p}${SALT}${KEY}"
    try:
        admin_password._derive(PASSWORD, b"s" * 16, log2_n, r, p)
        derivable = True
    except ValueError:
        derivable = False
    try:
        parse_password_hash(stored)
        accepted = True
    except AdminPasswordConfigError:
        accepted = False
    assert accepted == derivable


def test_default_and_previously_generated_hashes_still_pass(monkeypatch):
    # Generator defaults (N=2**15, r=8, p=1) and the test-speed N=2**14 both keep working.
    for stored in (admin_password.hash_password(PASSWORD), VALID):
        assert parse_password_hash(stored)[2:4] == (8, 1)
        monkeypatch.setenv(admin_password.HASH_ENV, stored)
        assert admin_password.verify_password(PASSWORD)

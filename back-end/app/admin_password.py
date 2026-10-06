"""Body password verification with a server-side PBKDF2 hash and reserved rate limits."""
import base64
import hashlib
import hmac
import os
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator

from app.admin_password_config import ITERATIONS, parse_password_hash
from app.errors import ApiError, ErrorCode


class AdminPasswordInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    password: Annotated[SecretStr, Field(min_length=1, max_length=1024)]

    @field_validator("password", mode="before")
    @classmethod
    def raw_password(cls, value):
        if not isinstance(value, str) or not value.strip():
            raise ValueError("Nonblank string required")
        return value  # Preserve every character, including leading and trailing whitespace.


def password_hash(password: str) -> str:
    salt = os.urandom(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, ITERATIONS)
    return "$".join(("pbkdf2_sha256", str(ITERATIONS),
                     base64.b64encode(salt).decode(), base64.b64encode(digest).decode()))


def verify_password(password: SecretStr, attempt) -> None:
    try:
        rounds, salt, expected = parse_password_hash(os.environ["ADMIN_PASSWORD_HASH"])
    except (KeyError, ValueError):
        attempt.failed()
        raise ApiError(500, ErrorCode.INTERNAL_ERROR) from None
    actual = hashlib.pbkdf2_hmac("sha256", password.get_secret_value().encode(), salt, rounds)
    if not hmac.compare_digest(actual, expected):
        attempt.failed()
        raise ApiError(401, ErrorCode.ADMIN_PASSWORD_INVALID)
    attempt.succeeded()


if __name__ == "__main__":
    from getpass import getpass

    value = getpass("관리자 비밀번호: ")
    AdminPasswordInput(password=value)
    print(password_hash(value))

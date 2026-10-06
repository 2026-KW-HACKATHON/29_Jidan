"""Dependency-free validation shared by authentication, health and deployment."""
import base64

ITERATIONS = 600_000


def parse_password_hash(value: str) -> tuple[int, bytes, bytes]:
    """Reject invalid configuration without including secret values in errors."""
    try:
        algorithm, rounds_text, salt_text, digest_text = value.split("$")
        rounds = int(rounds_text)
        salt = base64.b64decode(salt_text, validate=True)
        digest = base64.b64decode(digest_text, validate=True)
        if algorithm != "pbkdf2_sha256" or not ITERATIONS <= rounds <= 2_000_000:
            raise ValueError
        if str(rounds) != rounds_text or not 16 <= len(salt) <= 64 or len(digest) != 32:
            raise ValueError
        if base64.b64encode(salt).decode() != salt_text or base64.b64encode(digest).decode() != digest_text:
            raise ValueError
    except ValueError:
        raise ValueError("Invalid administrator password configuration") from None
    return rounds, salt, digest

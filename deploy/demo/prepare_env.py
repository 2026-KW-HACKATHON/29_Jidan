"""Copy the approved raw dev runtime settings, overriding only public demo settings.

Run on RPi4 with an SSH-copied dev-base.env. Never prints credentials. mysql.env holds
the initialization root secret separately from the API environment. Existing files are
reused so rerunning preparation cannot silently rotate a populated database password.
"""
import os
import secrets
import sys
from pathlib import Path


def raw_values(path: Path) -> dict[str, str]:
    values = {}
    for line in path.read_text().splitlines():
        if line and not line.lstrip().startswith("#") and "=" in line:
            key, value = line.split("=", 1)
            values[key.strip()] = value
    return values


def save(path: Path, values: dict[str, str]) -> None:
    if path.is_symlink():
        raise ValueError("environment destination must not be a symlink")
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(descriptor, "w") as stream:
        for key, value in values.items():
            stream.write(f"{key}={value}\n")
    path.chmod(0o600)


def prepare(source: Path, destination: Path) -> None:
    base = raw_values(source)
    if any(not base.get(key, "").strip() for key in ("DB_USER", "DB_PASSWORD", "OPENAI_API_KEY", "ADMIN_PASSWORD_HASH")):
        raise ValueError("dev environment is missing required settings")
    if base["DB_USER"] == "root":
        raise ValueError("demo API must use a non-root MySQL account")
    destination.mkdir(parents=True, exist_ok=True, mode=0o700)
    mysql_path = destination / "mysql.env"
    if mysql_path.exists():
        mysql = raw_values(mysql_path)
        if mysql.get("MYSQL_USER") != base["DB_USER"] or mysql.get("MYSQL_PASSWORD") != base["DB_PASSWORD"]:
            raise ValueError("existing demo DB credentials differ; migration is required")
        root_password = mysql["MYSQL_ROOT_PASSWORD"]
    else:
        root_password = secrets.token_urlsafe(32)
    save(mysql_path, {"MYSQL_DATABASE": "jidan_demo", "MYSQL_USER": base["DB_USER"],
                      "MYSQL_PASSWORD": base["DB_PASSWORD"], "MYSQL_ROOT_PASSWORD": root_password})
    base.update({"APP_ENV": "dev", "DB_HOST": "mysql", "DB_PORT": "3306", "DB_NAME": "jidan_demo",
                 "MEDIA_ROOT": "/var/lib/jidan/media", "AI_PROVIDER": "openai", "COOKIE_SECURE": "true",
                 "TASK_RUNNER_WORKERS": "1", "TASK_RUNNER_MODE": "background", "BACKGROUND_JOBS": "on"})
    save(destination / "runtime.env", base)


if __name__ == "__main__":
    prepare(Path(sys.argv[1]), Path(sys.argv[2]))
    print("Demo environment prepared; dev credentials reused without output")

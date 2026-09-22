import os

import pymysql


def database_status(environment: str) -> str:
    values = {key: os.getenv(f"DB_{key.upper()}") for key in ("host", "name", "user", "password")}
    if environment == "local" and not any(values.values()):
        return "not_configured"
    if not all(values.values()):
        raise ValueError("Incomplete database configuration")
    port = int(os.getenv("DB_PORT", "3306"))
    if not 1 <= port <= 65535:
        raise ValueError("Invalid database port")
    with pymysql.connect(
        host=values["host"], port=port, user=values["user"], password=values["password"],
        database=values["name"], connect_timeout=3, read_timeout=3, write_timeout=3,
    ) as connection, connection.cursor() as cursor:
        cursor.execute("SELECT 1")
        if cursor.fetchone() != (1,):
            raise ValueError("Unexpected database response")
    return "ok"

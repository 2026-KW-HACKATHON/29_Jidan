"""Guard the agreed remote contract; additional error declarations require a recorded decision."""

import hashlib
import json
from pathlib import Path

from tests.auth_contract import SPEC

BASELINE = json.loads((Path(__file__).parent / "fixtures/approved_remote_contract.json").read_text())
METHODS = {"get", "post", "put", "patch", "delete"}


def _normalize(value):
    if isinstance(value, dict):
        return {key: _normalize(item) for key, item in value.items()
                if key not in {"description", "example", "examples"}}
    if isinstance(value, list):
        return [_normalize(item) for item in value]
    return value


def _digest(value):
    encoded = json.dumps(_normalize(value), sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(encoded.encode()).hexdigest()


def test_all_approved_responses_are_preserved_and_only_agreed_additions_exist():
    actual = {(path, method) for path, item in SPEC["paths"].items()
              for method in item if method in METHODS}
    assert actual == {(row["path"], row["method"]) for row in BASELINE["operations"]}
    for row in BASELINE["operations"]:
        operation = SPEC["paths"][row["path"]][row["method"]]
        assert operation["operationId"] == row["operationId"]
        approved = set(row["approvedResponses"]) | set(row["approvedAdditions"])
        assert set(operation["responses"]) == approved, row["operationId"]


def test_requests_authentication_and_success_responses_keep_the_approved_contract():
    for row in BASELINE["operations"]:
        operation = SPEC["paths"][row["path"]][row["method"]]
        fields = {key: operation[key] for key in ("parameters", "requestBody", "security")
                  if key in operation}
        assert _digest(fields) == row["requestSecurityHash"], row["operationId"]
        success = {key: value for key, value in operation["responses"].items()
                   if key.startswith(("2", "3"))}
        assert _digest(success) == row["successResponseHash"], row["operationId"]


def test_schema_behavior_and_version_match_the_recorded_contract():
    assert _digest(SPEC["components"]["schemas"]) == BASELINE["schemasHash"]
    assert SPEC["info"]["version"] == BASELINE["localVersion"] == "0.11.0"

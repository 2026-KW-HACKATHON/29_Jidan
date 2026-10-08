"""Common contract checks generated from openapi.yaml for every operation.

Each case is derived from the operation's own spec (security requirements, role statement,
path parameter formats, request body, required headers); nothing is listed by hand except the
documented exemptions in `EXEMPTIONS`. An operation the application does not route yet is
skipped and is checked automatically once it is implemented.

Checks (each response also passes the OpenAPI contract client):
  no-session      no cookie                                   -> 401 SESSION_EXPIRED
  wrong-role      the other role's ACTIVE session             -> 403 FORBIDDEN
  suspended       a SUSPENDED member's session                -> 403 ACCOUNT_SUSPENDED, session revoked
  bad-uuid        a `format: uuid` path parameter = "not-a-uuid" -> 422 VALIDATION_ERROR naming it
  broken-json     malformed JSON for an application/json body -> 400 INVALID_REQUEST
  no-origin / wrong-origin / no-csrf on CsrfToken operations   -> 403 CSRF_INVALID
  no-key / bad-key on required Idempotency-Key                -> 422 VALIDATION_ERROR naming the header

Requests satisfy every other precondition (allowed Origin, CSRF token, UUID key, `{}` body) so
the response isolates the condition under test; the shared dependencies run before path, query
and body validation, which is the order the cases rely on.

MySQL runs a deterministic subset: for every check, the first implemented operation of each area
(tests/spec_coverage.AREAS). The checks exercise the shared dependencies (session lookup,
suspension revocation, CSRF, idempotency key parsing), whose database work does not depend on the
operation, so one operation per area and check covers the MySQL-specific behavior.
"""
import re
import uuid
from dataclasses import dataclass, field

import pytest
from sqlalchemy.orm import Session

from app.db.models import AuthSession, User
from tests.api_contract import ORIGIN, login
from tests.auth_contract import SPEC, resolve
from tests.factories import make_user, make_worker
from tests.spec_coverage import METHODS, _shape, app_routes, area_of

OTHER_ROLE = {"OWNER": "WORKER", "WORKER": "OWNER"}

# Operations a generic check does not apply to, with the spec statement that says so. Everything
# else is derived from the spec structure; an exemption without a reason here is not allowed.
EXEMPTIONS = {
    ("GET", "/api/auth/google"): ("*", "security: [] -- the anonymous OAuth entry"),
    ("GET", "/api/auth/google/callback"): ("*", "security: [] -- Google's browser callback"),
    ("POST", "/api/admin/store-approval-requests/search"): (
        "session,csrf", "security: [] -- the admin password, no session; Origin 403 in test_store_approvals"),
    ("POST", "/api/admin/store-approval-requests/{requestId}/approve"): (
        "session,csrf", "security: [] -- the admin password, no session; Origin 403 in test_store_approvals"),
    ("POST", "/api/auth/logout"): (
        "session", "security includes {} -- '쿠키가 없거나 이미 만료되었으면 Origin 검증 후 204'"),
    ("GET", "/api/auth/registration"): (
        "suspended", "'회원 세션만 있으면 403 ALREADY_REGISTERED' -- a member session never reaches suspension"),
}


# Mismatches the matrix found, reported for the owners to fix (strict xfail: the mark itself fails
# once the mismatch is gone, so the entry must then be removed). Not exemptions.
KNOWN_MISMATCHES: dict[tuple[str, str, str], str] = {}


@dataclass(frozen=True)
class Operation:
    method: str
    template: str
    spec: dict = field(repr=False, compare=False, hash=False)

    @property
    def label(self) -> str:
        return f"{self.method} {self.template}"

    @property
    def text(self) -> str:
        return str(self.spec)

    @property
    def security(self) -> list[dict]:
        return self.spec.get("security", SPEC.get("security", []))

    @property
    def role(self) -> str | None:
        """OWNER/WORKER/BOTH from the description's session statement; None if it names none."""
        description = self.spec.get("description", "")
        roles = set(re.findall(r"ACTIVE `?(WORKER|OWNER)`?", description))
        both = (
            re.search(r"OWNER 또는 WORKER|WORKER 또는 OWNER", description)
            or ("**OWNER**" in description and "**WORKER**" in description)
            or len(roles) == 2
        )
        if both:
            return "BOTH"
        return roles.pop() if roles else None

    def exempt(self, check: str) -> bool:
        groups = {"no-session": "session", "suspended": "suspended", "wrong-role": "role",
                  "no-origin": "csrf", "wrong-origin": "csrf", "no-csrf": "csrf"}
        scope, _reason = EXEMPTIONS.get((self.method, self.template), ("", ""))
        return scope == "*" or groups.get(check, check) in scope.split(",")

    @property
    def needs_session(self) -> bool:
        return bool(self.security) and {} not in self.security

    @property
    def needs_csrf(self) -> bool:
        return any("CsrfToken" in requirement for requirement in self.security)

    @property
    def member_session(self) -> bool:
        return any("SessionCookie" in requirement for requirement in self.security)

    def parameters(self, where: str) -> list[dict]:
        return [p for p in map(resolve, self.spec.get("parameters", [])) if p["in"] == where]

    @property
    def uuid_path_params(self) -> list[str]:
        return [p["name"] for p in self.parameters("path") if p["schema"].get("format") == "uuid"]

    @property
    def needs_key(self) -> bool:
        return any(p["name"] == "Idempotency-Key" and p.get("required") for p in self.parameters("header"))

    @property
    def json_body(self) -> bool:
        body = self.spec.get("requestBody")
        return bool(body) and "application/json" in resolve(body)["content"]


OPERATIONS = sorted(
    (Operation(method.upper(), template, op)
     for template, item in SPEC["paths"].items() for method, op in item.items() if method in METHODS),
    key=lambda op: (op.template, op.method),
)
IMPLEMENTED = {(method, _shape(path)) for method, path in app_routes()}


def applies(op: Operation, check: str) -> bool:
    if op.exempt(check):
        return False
    if check == "no-session":
        return op.needs_session
    if check == "wrong-role":
        return op.needs_session and op.role in OTHER_ROLE
    if check == "suspended":
        return op.member_session and op.needs_session and (
            op.role is not None or "ACCOUNT_SUSPENDED" in op.text)
    if check in ("no-origin", "wrong-origin", "no-csrf"):
        return op.needs_csrf
    if check in ("no-key", "bad-key"):
        return op.needs_key
    if check == "broken-json":
        return op.json_body
    raise AssertionError(check)


CHECKS = ("no-session", "wrong-role", "suspended", "no-origin", "wrong-origin", "no-csrf",
          "no-key", "bad-key", "broken-json")


def _mysql_subset() -> set[tuple[str, str, str]]:
    chosen, seen = set(), set()
    for check in (*CHECKS, "bad-uuid"):
        for op in OPERATIONS:
            if (op.method, _shape(op.template)) not in IMPLEMENTED:
                continue
            ok = bool(op.uuid_path_params) and not op.exempt(check) if check == "bad-uuid" else applies(op, check)
            if ok and (check, area_of(op.spec)) not in seen:
                seen.add((check, area_of(op.spec)))
                chosen.add((check, op.method, op.template))
    return chosen


MYSQL_SUBSET = _mysql_subset()


def _cases(check: str, ops_and_ids):
    cases = []
    for op, extra, case_id in ops_and_ids:
        known = KNOWN_MISMATCHES.get((check, op.method, op.template))
        marks = [pytest.mark.xfail(strict=True, reason=known)] if known else []
        cases.append(pytest.param(op, extra, "sqlite", id=f"{case_id}-sqlite", marks=marks))
        in_subset = (check, op.method, op.template) in MYSQL_SUBSET
        if in_subset and (check != "bad-uuid" or extra == op.uuid_path_params[0]):
            cases.append(pytest.param(op, extra, "mysql", id=f"{case_id}-mysql", marks=[*marks, pytest.mark.mysql]))
    return cases


def cases_for(check: str):
    return _cases(check, [(op, None, op.label) for op in OPERATIONS if applies(op, check)])


def uuid_cases():
    return _cases("bad-uuid", [
        (op, name, f"{op.label}[{name}]") for op in OPERATIONS if not op.exempt("bad-uuid")
        for name in op.uuid_path_params
    ])


# --- requests -------------------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _fresh_rate_limits():
    """Admin operations reserve in-memory rate-limit slots; keep them from leaking across tests."""
    from app.ratelimit import reset_all_limits

    reset_all_limits()
    yield
    reset_all_limits()


def _require_implemented(op: Operation) -> None:
    if (op.method, _shape(op.template)) not in IMPLEMENTED:
        pytest.skip(f"not implemented yet: {op.label}")


def _member(db_engine, role: str) -> str:
    with Session(db_engine) as db:
        user = make_worker(db) if role == "WORKER" else make_user(db, "OWNER")
        db.commit()
        return user.id


def _suspend(db_engine, user_id: str) -> None:
    with Session(db_engine) as db:
        db.get(User, user_id).status = "SUSPENDED"
        db.commit()


def call(api, op: Operation, *, session=None, origin: str | None = ORIGIN, csrf: str | None = "",
         key: str | None = "", path: dict | None = None, body=b"{}"):
    """`csrf=""`/`key=""` mean "the valid value"; None leaves the header out."""
    values = {name: str(uuid.uuid4()) for name in op.uuid_path_params} | (path or {})
    url = re.sub(r"\{([^}]+)\}", lambda m: values.get(m.group(1), str(uuid.uuid4())), op.template)
    headers = {}
    if origin is not None:
        headers["Origin"] = origin
    if csrf is not None:
        headers["X-CSRF-Token"] = csrf or (session.csrf_token if session else "no-session-token")
    if key is not None and op.needs_key:
        headers["Idempotency-Key"] = key or str(uuid.uuid4())
    content = None
    if op.json_body:
        headers["Content-Type"] = "application/json"
        content = body
    return api.request(op.method, url, headers=headers, content=content)


def _session_for(api, db_engine, op: Operation):
    role = op.role if op.role in OTHER_ROLE else "WORKER"
    return login(api, _member(db_engine, role))


def _code(response) -> str:
    return response.json().get("code")


# --- checks ---------------------------------------------------------------------------------------


@pytest.mark.parametrize(("op", "_extra", "db_engine"), cases_for("no-session"), indirect=["db_engine"])
def test_no_session_is_401(api, op, _extra, db_engine):
    _require_implemented(op)
    response = call(api, op)
    assert (response.status_code, _code(response)) == (401, "SESSION_EXPIRED"), response.text


@pytest.mark.parametrize(("op", "_extra", "db_engine"), cases_for("wrong-role"), indirect=["db_engine"])
def test_other_role_is_403_forbidden(api, op, _extra, db_engine):
    _require_implemented(op)
    session = login(api, _member(db_engine, OTHER_ROLE[op.role]))
    response = call(api, op, session=session)
    assert (response.status_code, _code(response)) == (403, "FORBIDDEN"), response.text


@pytest.mark.parametrize(("op", "_extra", "db_engine"), cases_for("suspended"), indirect=["db_engine"])
def test_suspended_account_is_403_and_loses_its_session(api, op, _extra, db_engine):
    _require_implemented(op)
    session = _session_for(api, db_engine, op)
    _suspend(db_engine, session.user_id)
    response = call(api, op, session=session)
    assert (response.status_code, _code(response)) == (403, "ACCOUNT_SUSPENDED"), response.text
    with Session(db_engine) as db:
        assert db.query(AuthSession).filter_by(user_id=session.user_id).one().revoked_at is not None


@pytest.mark.parametrize(("op", "name", "db_engine"), uuid_cases(), indirect=["db_engine"])
def test_malformed_uuid_path_parameter_is_422(api, op, name, db_engine):
    _require_implemented(op)
    session = _session_for(api, db_engine, op) if op.needs_session else None
    response = call(api, op, session=session, path={name: "not-a-uuid"},
                    body=b'{"password": "x"}' if not op.needs_session else b"{}")
    assert (response.status_code, _code(response)) == (422, "VALIDATION_ERROR"), response.text
    fields = [error["field"] for error in response.json()["fieldErrors"]]
    assert any(field.split(".")[-1] == name for field in fields), fields


@pytest.mark.parametrize(("op", "_extra", "db_engine"), cases_for("broken-json"), indirect=["db_engine"])
def test_broken_json_is_400(api, op, _extra, db_engine):
    _require_implemented(op)
    session = _session_for(api, db_engine, op) if op.member_session else None
    response = call(api, op, session=session, body=b'{"broken":')
    assert (response.status_code, _code(response)) == (400, "INVALID_REQUEST"), response.text


@pytest.mark.parametrize(("op", "_extra", "db_engine"), cases_for("no-origin"), indirect=["db_engine"])
def test_missing_origin_is_403_csrf(api, op, _extra, db_engine):
    _require_implemented(op)
    response = call(api, op, session=_session_for(api, db_engine, op), origin=None)
    assert (response.status_code, _code(response)) == (403, "CSRF_INVALID"), response.text


@pytest.mark.parametrize(("op", "_extra", "db_engine"), cases_for("wrong-origin"), indirect=["db_engine"])
def test_foreign_origin_is_403_csrf(api, op, _extra, db_engine):
    _require_implemented(op)
    response = call(api, op, session=_session_for(api, db_engine, op), origin="https://evil.example")
    assert (response.status_code, _code(response)) == (403, "CSRF_INVALID"), response.text


@pytest.mark.parametrize(("op", "_extra", "db_engine"), cases_for("no-csrf"), indirect=["db_engine"])
def test_missing_csrf_token_is_403_csrf(api, op, _extra, db_engine):
    _require_implemented(op)
    response = call(api, op, session=_session_for(api, db_engine, op), csrf=None)
    assert (response.status_code, _code(response)) == (403, "CSRF_INVALID"), response.text


@pytest.mark.parametrize(("op", "_extra", "db_engine"), cases_for("no-key"), indirect=["db_engine"])
def test_missing_idempotency_key_is_422(api, op, _extra, db_engine):
    _require_implemented(op)
    response = call(api, op, session=_session_for(api, db_engine, op), key=None)
    assert (response.status_code, _code(response)) == (422, "VALIDATION_ERROR"), response.text
    assert "Idempotency-Key" in [e["field"] for e in response.json()["fieldErrors"]]


@pytest.mark.parametrize(("op", "_extra", "db_engine"), cases_for("bad-key"), indirect=["db_engine"])
def test_malformed_idempotency_key_is_422(api, op, _extra, db_engine):
    _require_implemented(op)
    response = call(api, op, session=_session_for(api, db_engine, op), key="not-a-uuid")
    assert (response.status_code, _code(response)) == (422, "VALIDATION_ERROR"), response.text
    assert "Idempotency-Key" in [e["field"] for e in response.json()["fieldErrors"]]


# --- the matrix itself ----------------------------------------------------------------------------


def test_every_member_operation_has_a_role_or_a_reason():
    """A session operation whose role cannot be read from the spec must be explained."""
    unexplained = [
        op.label for op in OPERATIONS
        if op.needs_session and op.member_session and op.role is None
        and not op.template.startswith("/api/auth/")
    ]
    assert unexplained == []


def test_exemptions_point_at_real_operations():
    labels = {(op.method, op.template) for op in OPERATIONS}
    assert set(EXEMPTIONS) <= labels
    assert {(method, template) for _check, method, template in KNOWN_MISMATCHES} <= labels


def test_the_matrix_is_not_empty():
    assert len(OPERATIONS) == 103  # 0.12.0 adds review media-writing
    assert sum(applies(op, "no-session") for op in OPERATIONS) >= 90
    assert MYSQL_SUBSET

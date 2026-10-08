"""Validate every real /api response against the authoritative OpenAPI, path templates included.

`auth_contract.ContractClient` only checks the auth chapter by exact path. Business endpoints
use `{storeId}`-style templates, so this client matches templates, refuses operations missing
from the spec, and checks the status, the JSON body schema and `Cache-Control: no-store`.
"""
import re
from dataclasses import dataclass

from fastapi.testclient import TestClient
from jsonschema import Draft202012Validator, FormatChecker

from tests.auth_contract import SPEC, resolve

# Deployment checks own these bodies; they are not part of the business contract.
UNCHECKED_PREFIXES = ("/api/health", "/api/swagger")


def _template_pattern(template: str) -> re.Pattern[str]:
    parts = re.split(r"(\{[^}/]+\})", template)
    body = "".join("[^/]+" if part.startswith("{") else re.escape(part) for part in parts)
    return re.compile(f"^{body}$")


# Literal segments win over templates (e.g. `/manual/draft` vs `/manual/{x}`).
_TEMPLATES = sorted(
    ((template, _template_pattern(template)) for template in SPEC.get("paths", {})),
    key=lambda item: (item[0].count("{"), -len(item[0])),
)


def find_operation(path: str, method: str) -> tuple[str, dict] | None:
    for template, pattern in _TEMPLATES:
        if pattern.match(path):
            operation = SPEC["paths"][template].get(method.lower())
            if operation is not None:
                return template, operation
    return None


def validate_response(response) -> None:
    path = response.request.url.path
    method = response.request.method
    if not path.startswith("/api/") or path.startswith(UNCHECKED_PREFIXES):
        return
    found = find_operation(path, method)
    assert found is not None, f"{method} {path} is served but is not in openapi.yaml"
    template, operation = found
    status = str(response.status_code)
    assert status in operation["responses"], (method, template, status, response.text[:500])
    assert "no-store" in response.headers.get("cache-control", ""), (method, template, "no-store")
    contract = resolve(operation["responses"][status])
    content = contract.get("content") or {}
    if response.status_code == 204 or not content:
        assert response.content == b"", (method, template, status, "body must be empty")
        return
    media_type = response.headers.get("content-type", "").split(";")[0].strip()
    if "application/json" in content and media_type == "application/json":
        schema = {**content["application/json"]["schema"], "components": SPEC["components"]}
        Draft202012Validator(schema, format_checker=FormatChecker()).validate(response.json())
    else:
        assert media_type in content, (method, template, status, media_type, list(content))


class ApiContractClient(TestClient):
    def request(self, method, url, *args, **kwargs):
        response = super().request(method, url, *args, **kwargs)
        validate_response(response)
        return response


ORIGIN = "http://frontend.test"


@dataclass(frozen=True)
class LoggedIn:
    """A member session usable by a client: cookie token plus the headers a write needs."""

    user_id: str
    token: str
    csrf_token: str

    def headers(self, idempotency_key: str | None = None) -> dict[str, str]:
        headers = {"Origin": ORIGIN, "X-CSRF-Token": self.csrf_token}
        if idempotency_key is not None:
            headers["Idempotency-Key"] = idempotency_key
        return headers


def login(client: TestClient, user_id: str) -> LoggedIn:
    """Issue a real member session for `user_id` and attach its cookie to `client`."""
    from app.auth import SESSION_COOKIE_NAME, create_session

    issued = create_session(user_id)
    client.cookies.set(SESSION_COOKIE_NAME, issued.token)
    return LoggedIn(user_id=user_id, token=issued.token, csrf_token=issued.csrf_token)

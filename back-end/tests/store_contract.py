"""Check store responses against the hand-authored contract, including dynamic paths."""
from jsonschema import Draft202012Validator, FormatChecker

from tests.auth_contract import SPEC, ContractClient, resolve


class StoreContractClient(ContractClient):
    def request(self, method, url, *args, **kwargs):
        response = super().request(method, url, *args, **kwargs)
        path = response.request.url.path
        if not path.startswith(("/api/stores", "/api/owners", "/api/admin/store-approval")):
            return response
        segments = path.strip("/").split("/")
        for template, operations in SPEC["paths"].items():
            parts = template.strip("/").split("/")
            if len(parts) != len(segments) or not all(
                a == b or a.startswith("{") for a, b in zip(parts, segments, strict=True)
            ):
                continue
            operation = operations.get(response.request.method.lower())
            if operation:
                assert str(response.status_code) in operation["responses"], response.text
                contract = resolve(operation["responses"][str(response.status_code)])
                schema = {**contract["content"]["application/json"]["schema"],
                          "components": SPEC["components"]}
                Draft202012Validator(schema, format_checker=FormatChecker()).validate(response.json())
                assert response.headers["cache-control"] == "no-store"
                break
        return response

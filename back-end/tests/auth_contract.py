"""Validate real auth responses, including errors, against the authoritative OpenAPI."""
from pathlib import Path

import yaml
from fastapi.testclient import TestClient
from jsonschema import Draft202012Validator, FormatChecker


class OpenApiLoader(yaml.SafeLoader):
    def compose_node(self, parent, index):
        # YAML allows an anchor to be redefined; aliases refer to its most recent definition.
        event = self.peek_event()
        if not isinstance(event, yaml.AliasEvent) and event.anchor is not None:
            self.anchors.pop(event.anchor, None)
        return super().compose_node(parent, index)


SPEC = yaml.load((Path(__file__).parents[1] / "openapi.yaml").read_text(), Loader=OpenApiLoader)


def resolve(value):
    if "$ref" not in value:
        return value
    current = SPEC
    for name in value["$ref"].removeprefix("#/").split("/"):
        current = current[name]
    return resolve(current)


class ContractClient(TestClient):
    def request(self, method, url, *args, **kwargs):
        response = super().request(method, url, *args, **kwargs)
        path = response.request.url.path
        operation = SPEC.get("paths", {}).get(path, {}).get(response.request.method.lower())
        if operation and path.startswith("/api/auth/"):
            assert str(response.status_code) in operation["responses"], (path, response.status_code, response.text)
            contract = resolve(operation["responses"][str(response.status_code)])
            if "content" in contract:
                schema = {**contract["content"]["application/json"]["schema"], "components": SPEC["components"]}
                Draft202012Validator(schema, format_checker=FormatChecker()).validate(response.json())
            elif response.status_code == 204:
                assert response.content == b""
            if response.status_code == 302:
                assert response.headers.get("location", "").startswith(("http://", "https://"))
        return response

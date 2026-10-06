"""A server-generated identifier shared by all processing of one HTTP request."""
import uuid

from starlette.requests import Request


def new_request_id() -> str:
    return f"req_{uuid.uuid4().hex[:16]}"


def request_id_for(request: Request) -> str:
    state = request.scope.setdefault("state", {})
    if "request_id" not in state:
        state["request_id"] = new_request_id()
    return state["request_id"]

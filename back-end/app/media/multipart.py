"""Streaming `multipart/form-data` reader for media uploads (`purpose` + `file`).

The body is parsed while it arrives and reading stops as soon as the file exceeds the largest
accepted size (413 MEDIA_TOO_LARGE), so an oversized upload is never buffered or spooled to
disk. Only the two contract fields are accepted, each exactly once; the first part that breaks
that rule stops parsing (422). A body without its closing boundary is incomplete (422). The
client's file name and part Content-Type are ignored (the real type is judged from bytes by
app.media.inspection).
"""

from dataclasses import dataclass, field

from fastapi import Request
from python_multipart.multipart import MultipartParser, parse_options_header

from app.errors import ApiError, ErrorCode

MULTIPART_OVERHEAD = 64 * 1024  # boundaries, part headers and the purpose field
MAX_PURPOSE_BYTES = 64


@dataclass
class UploadForm:
    purpose: str
    data: bytes = field(repr=False)


class _TooLarge(Exception):
    pass


class _Rejected(Exception):
    """Stop parsing at the first invalid part: nothing after it can make the form valid, and
    parsing the rest of a body of many tiny parts would only burn CPU on the event loop."""

    def __init__(self, error: ApiError):
        self.error = error


def _field_error(name: str, code: str, message: str) -> ApiError:
    return ApiError(422, ErrorCode.VALIDATION_ERROR,
                    field_errors=[{"field": name, "code": code, "message": message}])


def _too_large() -> ApiError:
    return ApiError(413, ErrorCode.MEDIA_TOO_LARGE, "파일 크기 또는 음성 길이가 너무 큽니다.")


async def read_media_form(request: Request, *, max_file_bytes: int,
                          purposes: tuple[str, ...]) -> UploadForm:
    content_type = request.headers.get("content-type", "")
    mime, params = parse_options_header(content_type)
    boundary = params.get(b"boundary")
    if mime != b"multipart/form-data" or not boundary:
        raise _field_error("file", "REQUIRED", "multipart/form-data로 파일을 보내 주세요.")
    limit = max_file_bytes + MULTIPART_OVERHEAD
    declared = request.headers.get("content-length")
    if declared is not None and declared.isdigit() and int(declared) > limit:
        raise _too_large()

    parts: dict[str, bytearray] = {}
    state = {"headers": [], "field": b"", "value": b"", "name": None, "seen": set(), "ended": False}

    def on_part_begin():
        state["headers"] = []
        state["name"] = None

    def on_header_field(data, start, end):
        state["field"] += data[start:end]

    def on_header_value(data, start, end):
        state["value"] += data[start:end]

    def on_header_end():
        state["headers"].append((state["field"].lower(), state["value"]))
        state["field"] = state["value"] = b""

    def on_headers_finished():
        disposition = dict(state["headers"]).get(b"content-disposition", b"")
        kind, options = parse_options_header(disposition)
        name = options.get(b"name", b"").decode("utf-8", "replace")
        if kind != b"form-data" or name not in ("purpose", "file"):
            raise _Rejected(_field_error(name or "form", "INVALID_FORMAT", "허용되지 않는 입력입니다."))
        if name in state["seen"]:
            raise _Rejected(_field_error(name, "INVALID_FORMAT", "한 번만 보내 주세요."))
        if name == "file" and b"filename" not in options:
            raise _Rejected(_field_error("file", "INVALID_FORMAT", "파일로 보내 주세요."))
        state["seen"].add(name)
        state["name"] = name
        parts.setdefault(name, bytearray())

    def on_part_data(data, start, end):
        name = state["name"]
        if name is None:
            return
        buffer = parts[name]
        buffer += data[start:end]
        cap = max_file_bytes if name == "file" else MAX_PURPOSE_BYTES
        if len(buffer) > cap:
            if name == "file":
                raise _TooLarge()
            raise _Rejected(_field_error("purpose", "INVALID_FORMAT", "형식을 확인해 주세요."))

    def on_end():
        state["ended"] = True

    parser = MultipartParser(boundary, callbacks={
        "on_part_begin": on_part_begin, "on_header_field": on_header_field,
        "on_header_value": on_header_value, "on_header_end": on_header_end,
        "on_headers_finished": on_headers_finished, "on_part_data": on_part_data, "on_end": on_end,
    }, max_header_count=8, max_header_size=4096)
    received = 0
    try:
        async for chunk in request.stream():
            received += len(chunk)
            if received > limit:
                raise _TooLarge()
            parser.write(chunk)
        parser.finalize()
    except _TooLarge:
        raise _too_large() from None
    except _Rejected as rejected:
        raise rejected.error from None
    except Exception:  # noqa: BLE001 - malformed multipart framing, never echo it
        raise _field_error("file", "INVALID_FORMAT", "multipart 형식을 확인해 주세요.") from None
    if not state["ended"]:  # no closing boundary: a truncated body is not a complete form
        raise _field_error("file", "INVALID_FORMAT", "multipart 형식을 확인해 주세요.")
    if "purpose" not in parts:
        raise _field_error("purpose", "REQUIRED", "필수 항목입니다.")
    if "file" not in parts:
        raise _field_error("file", "REQUIRED", "필수 항목입니다.")
    purpose = bytes(parts["purpose"]).decode("utf-8", "replace")
    if purpose not in purposes:
        raise _field_error("purpose", "INVALID_FORMAT", "형식을 확인해 주세요.")
    return UploadForm(purpose=purpose, data=bytes(parts["file"]))

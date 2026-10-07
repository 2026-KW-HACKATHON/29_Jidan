"""Streaming multipart reader edge cases (app.media.multipart), without the HTTP stack."""
import asyncio

import pytest
from starlette.requests import Request

from app.errors import ApiError
from app.media.multipart import read_media_form

LIMIT = 1024 * 1024
END = b"--XyZ--\r\n"


def _part(name: bytes, data: bytes, *, filename: bool = True, extra: bytes = b"") -> bytes:
    disposition = b'form-data; name="' + name + b'"' + (b'; filename="a.jpg"' if filename else b"")
    return b"--XyZ\r\nContent-Disposition: " + disposition + b"\r\n" + extra + b"\r\n" + data + b"\r\n"


PURPOSE = _part(b"purpose", b"MANUAL_PHOTO", filename=False)
FILE = _part(b"file", b"x" * 100)


class Body:
    """An ASGI request body delivered in chunks, counting how many were read."""

    def __init__(self, body: bytes, chunk: int = 4096, *, length: bool = True):
        self.chunks = [body[i:i + chunk] for i in range(0, len(body), chunk)] or [b""]
        self.read = 0
        self.headers = [(b"content-type", b"multipart/form-data; boundary=XyZ")]
        if length:
            self.headers.append((b"content-length", str(len(body)).encode()))

    async def receive(self):
        if self.read < len(self.chunks):
            self.read += 1
            return {"type": "http.request", "body": self.chunks[self.read - 1], "more_body": True}
        return {"type": "http.request", "body": b"", "more_body": False}

    def request(self) -> Request:
        return Request({"type": "http", "method": "POST", "headers": self.headers, "path": "/"}, self.receive)


def _read(body: Body):
    return asyncio.run(read_media_form(body.request(), max_file_bytes=LIMIT, purposes=("MANUAL_PHOTO",)))


def _error(body: Body) -> ApiError:
    with pytest.raises(ApiError) as caught:
        _read(body)
    return caught.value


@pytest.mark.parametrize("order", ["purpose_first", "file_first"])
def test_valid_form_in_either_order(order):
    body = PURPOSE + FILE if order == "purpose_first" else FILE + PURPOSE
    form = _read(Body(body + END))
    assert (form.purpose, form.data) == ("MANUAL_PHOTO", b"x" * 100)


def test_parsing_stops_at_the_first_invalid_part():
    # Thousands of tiny unknown parts used to be parsed to the end on the event loop.
    flood = b"".join(_part(b"z%d" % i, b"a", filename=False) for i in range(20_000))
    body = Body(flood + PURPOSE + FILE + END, length=False)  # under the byte cut-off on its own
    error = _error(body)
    assert (error.status_code, error.field_errors[0]["field"]) == (422, "z0")
    assert body.read < 5 < len(body.chunks)  # stopped right after the first chunk(s)


@pytest.mark.parametrize("extra_part", [
    _part(b"purpose", b"MANUAL_PHOTO", filename=False),  # duplicate
    _part(b"other", b"a", filename=False),  # unknown field
])
def test_a_third_part_is_rejected_without_reading_further(extra_part):
    body = Body(PURPOSE + FILE + extra_part + b"x" * (LIMIT // 2) + END)
    assert _error(body).status_code == 422
    assert body.read < len(body.chunks)


def test_oversized_purpose_stops_parsing():
    body = Body(_part(b"purpose", b"P" * 1000, filename=False) + FILE + END)
    error = _error(body)
    assert (error.status_code, error.field_errors[0]["field"]) == (422, "purpose")


@pytest.mark.parametrize("body", [
    PURPOSE + FILE,  # closing boundary missing (truncated upload)
    PURPOSE + FILE + b"--XyZ\r\n",  # a part was announced and never sent
])
def test_incomplete_body_is_invalid(body):
    error = _error(Body(body))
    assert (error.status_code, error.field_errors[0]["field"]) == (422, "file")


def test_file_over_the_limit_without_content_length_is_cut_off():
    body = Body(PURPOSE + _part(b"file", b"x" * (LIMIT * 3)) + END, chunk=65536, length=False)
    assert _error(body).status_code == 413
    assert body.read < len(body.chunks)


@pytest.mark.parametrize("extra", [
    b"".join(b"X-%d: a\r\n" % i for i in range(9)),  # too many part headers
    b"X-Big: " + b"a" * 5000 + b"\r\n",  # too large a part header
])
def test_part_header_limits(extra):
    assert _error(Body(PURPOSE + _part(b"file", b"x", extra=extra) + END)).status_code == 422

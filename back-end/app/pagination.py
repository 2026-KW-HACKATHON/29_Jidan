"""Shared `page` / `size` query parameters and the list response envelope.

Pages are 0-based (openapi.yaml after #102): `page` defaults to 0 and `size` to 20 with a
maximum of 100. Anything else is `422 VALIDATION_ERROR` with a field error naming the
parameter. Asking for a page past the end is not an error; the endpoint answers 200 with an
empty `items`.
"""

import math
import re
from dataclasses import dataclass
from typing import Annotated, Any

from fastapi import Depends, Request

from app.errors import ApiError, ErrorCode

DEFAULT_PAGE = 0
DEFAULT_SIZE = 20
MAX_SIZE = 100
# Keeps `page * size` far below the database's OFFSET limit instead of failing with a 500.
MAX_PAGE = 1_000_000

_DIGITS = re.compile(r"^[0-9]{1,9}$")


@dataclass(frozen=True)
class PageParams:
    page: int = DEFAULT_PAGE
    size: int = DEFAULT_SIZE

    @property
    def offset(self) -> int:
        return self.page * self.size

    @property
    def limit(self) -> int:
        return self.size


def _parse(request: Request, name: str, default: int, minimum: int, maximum: int):
    """(value, None) or (None, field error). Only plain decimal digits are accepted."""
    values = request.query_params.getlist(name)
    if not values:
        return default, None
    if len(values) > 1 or not _DIGITS.match(values[0]):
        return None, {"field": name, "code": "INVALID_FORMAT", "message": "정수로 입력해 주세요."}
    value = int(values[0])
    if not minimum <= value <= maximum:
        return None, {
            "field": name, "code": "OUT_OF_RANGE",
            "message": f"{minimum} 이상 {maximum} 이하로 입력해 주세요.",
        }
    return value, None


def page_params(request: Request) -> PageParams:
    """Dependency for `?page=&size=`: reports every invalid parameter at once."""
    page, page_error = _parse(request, "page", DEFAULT_PAGE, 0, MAX_PAGE)
    size, size_error = _parse(request, "size", DEFAULT_SIZE, 1, MAX_SIZE)
    errors = [error for error in (page_error, size_error) if error is not None]
    if errors:
        raise ApiError(422, ErrorCode.VALIDATION_ERROR, field_errors=errors)
    return PageParams(page, size)


Pagination = Annotated[PageParams, Depends(page_params)]


def page_response(items: list[Any], total_items: int, params: PageParams) -> dict[str, Any]:
    """The `{items, page, size, totalItems, totalPages}` envelope used by list endpoints."""
    return {
        "items": items,
        "page": params.page,
        "size": params.size,
        "totalItems": total_items,
        "totalPages": math.ceil(total_items / params.size),
    }

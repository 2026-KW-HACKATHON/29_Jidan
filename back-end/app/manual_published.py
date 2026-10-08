"""Published manual reading for workers and owners (#118; openapi tag 근무자 매뉴얼).

    GET /api/stores/{storeId}/manual/published                       sections of the current version
    GET /api/stores/{storeId}/manual/published/sections/{sectionId}  one section with steps/photos

Every request re-checks the reader (app.manual_content.require_manual_reader) before looking at
the manual, then answers from the version `current_published_version_id` points at in this
transaction. `expectedVersionId` only detects a replaced version (409); it never selects an
older one.
"""

from typing import Annotated, Literal

from fastapi import APIRouter, Path, Query

from app.auth import CurrentMember, DbSession
from app.db import utcnow
from app.errors import ApiError, ErrorCode
from app.manual_content import (
    published_list_body,
    published_section_body,
    require_manual_reader,
    require_published_version,
)
from app.store_access import UUID_PATTERN, StoreIdPath, normalize_uuid

router = APIRouter()

SectionIdPath = Annotated[str, Path(alias="sectionId", pattern=UUID_PATTERN)]
ExpectedVersionQuery = Annotated[str | None, Query(alias="expectedVersionId", pattern=UUID_PATTERN)]


def _shift_filter_error(message: str, code: str) -> ApiError:
    return ApiError(422, ErrorCode.VALIDATION_ERROR, field_errors=[
        {"field": "shiftId", "code": code, "message": message}])


@router.get("/api/stores/{storeId}/manual/published")
def get_published_manual(
    store_id: StoreIdPath, member: CurrentMember, db: DbSession,
    filter: Annotated[Literal["ALL", "COMMON", "SHIFT"], Query()] = "ALL",
    shift_id: Annotated[str | None, Query(alias="shiftId", pattern=UUID_PATTERN)] = None,
    expected_version_id: ExpectedVersionQuery = None,
) -> dict:
    if filter == "SHIFT" and shift_id is None:
        raise _shift_filter_error("근무조 업무를 보려면 근무조를 선택해 주세요.", "REQUIRED")
    if filter != "SHIFT" and shift_id is not None:
        raise _shift_filter_error("근무조는 SHIFT 보기에서만 선택할 수 있습니다.", "INVALID_FORMAT")
    store = require_manual_reader(db, member, store_id, now=utcnow())
    version = require_published_version(db, store.id, expected_version_id)
    return published_list_body(
        db, store.id, version, filter=filter,
        shift_id=None if shift_id is None else normalize_uuid(shift_id),
    )


@router.get("/api/stores/{storeId}/manual/published/sections/{sectionId}")
def get_published_manual_section(
    store_id: StoreIdPath, section_id: SectionIdPath, member: CurrentMember, db: DbSession,
    expected_version_id: ExpectedVersionQuery = None,
) -> dict:
    store = require_manual_reader(db, member, store_id, now=utcnow())
    version = require_published_version(db, store.id, expected_version_id)
    return published_section_body(db, store.id, version, section_id)

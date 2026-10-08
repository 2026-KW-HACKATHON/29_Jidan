"""#118 drafts at the contract's size limits on MySQL (opt-in: JIDAN_LARGE_CONTENT=1).

MySQL 8 puts whole rows (TEXT/JSON included) into the sort buffer when an ORDER BY cannot use an
index; rows too wide for the buffer fail with 1038 "Out of sort memory" (#120 hit it on review
JSON). The draft's ordered queries sort step, issue, acknowledgement and correction rows, so this
builds a draft at every limit with 4-byte characters (the widest rows) and checks that the owner
and worker paths work and that those queries still succeed with the smallest session sort buffer.

About 27 seconds and 60 MB of rows per run, so the default suite skips it:

    JIDAN_LARGE_CONTENT=1 JIDAN_REQUIRE_MYSQL=1 DB_...=... python -m pytest tests/test_manual_large_content.py
"""

import os
import uuid
from datetime import time

import pytest
from sqlalchemy import insert, text
from sqlalchemy.orm import Session

from app.db.models import (
    ManualDraftCorrection,
    ManualIssueAcknowledgement,
    ManualReviewIssue,
    ManualSection,
    ManualShift,
    ManualStep,
    ManualVersion,
)
from app.manual_content import load_rows
from app.manual_drafts import latest_correction
from app.manual_editing import current_acknowledgements, open_issues
from tests.api_contract import login
from tests.factories import NOW, make_manual_draft, make_store, make_user

pytestmark = [
    pytest.mark.mysql,
    pytest.mark.parametrize("db_engine", ["mysql"], indirect=True),
    pytest.mark.skipif(os.getenv("JIDAN_LARGE_CONTENT") != "1",
                       reason="size-limit test: set JIDAN_LARGE_CONTENT=1 (about 27 s, 60 MB)"),
]

SHIFTS, SECTIONS, STEPS, ISSUES, CORRECTIONS = 20, 200, 100, 200, 50
LONG_STEP = "😀" * 3000      # ManualStep.instruction limit, 4 bytes per character
LONG_INPUT = "😀" * 10000    # correction input limit
LONG_ISSUE = "😀" * 1000     # issue description limit
LONG_NOTE = "😀" * 2000      # acknowledgement note limit
MIN_SORT_BUFFER = 32768      # MySQL's minimum sort_buffer_size (session only)


def _id() -> str:
    return str(uuid.uuid4())


def build_max_draft(db_engine, *, long_steps: int) -> dict:
    """A READY draft at the limits: 20 shifts, 200 sections x 100 steps (the first `long_steps`
    of each at the maximum length), 200 review issues and their acknowledgements, and 50 failed
    corrections, all with maximum-length text. The content needs no missing information, so it
    is also a valid edit body."""
    with Session(db_engine) as db:
        owner = make_user(db, "OWNER")
        store = make_store(db, owner=owner, approval_status="APPROVED", approved_at=NOW)
        version = make_manual_draft(db, store, generation_status="READY")
        db.execute(insert(ManualShift), [
            {"id": _id(), "version_id": version.id, "sort_order": i, "name": f"근무조 {i}",
             "start_time": time(9), "end_time": time(18), "ends_next_day": False} for i in range(SHIFTS)])
        sections = [_id() for _ in range(SECTIONS)]
        db.execute(insert(ManualSection), [
            {"id": section, "version_id": version.id, "shift_id": None, "sort_order": i,
             "category": "COMMON_TASK", "title": f"업무 {i}"} for i, section in enumerate(sections)])
        for chunk in range(0, SECTIONS, 20):
            db.execute(insert(ManualStep), [
                {"id": _id(), "section_id": section, "sort_order": order,
                 "instruction": LONG_STEP if order < long_steps else f"단계 {order}", "checklist_item": False}
                for section in sections[chunk:chunk + 20] for order in range(STEPS)])
        issues = [_id() for _ in range(ISSUES)]
        db.execute(insert(ManualReviewIssue), [
            {"id": issue, "version_id": version.id, "intent_id": None, "description": LONG_ISSUE,
             "created_at": NOW} for issue in issues])
        db.execute(insert(ManualIssueAcknowledgement), [
            {"id": _id(), "issue_id": issue, "version_revision": 1, "content_revision": 1,
             "acknowledged_snapshot": {"description": LONG_ISSUE}, "owner_id": owner.id,
             "owner_note": LONG_NOTE, "acknowledged_at": NOW} for issue in issues])
        db.execute(insert(ManualDraftCorrection), [
            {"id": _id(), "version_id": version.id, "base_revision": 1, "target_kind": "MANUAL",
             "input_method": "TEXT", "input_text": LONG_INPUT, "status": "ERROR",
             "error_code": "AI_PROCESSING_FAILED", "attempt": 1, "requested_by_owner_id": owner.id,
             "created_at": NOW, "updated_at": NOW, "completed_at": NOW} for _ in range(CORRECTIONS)])
        db.commit()
        return {"owner": owner.id, "store": store.id, "version": version.id, "sections": sections}


def test_owner_and_worker_paths_at_the_size_limit(api, db_engine):
    ids = build_max_draft(db_engine, long_steps=5)
    auth = login(api, ids["owner"])
    base = f"/api/stores/{ids['store']}/manual"
    draft = api.get(f"{base}/draft")
    assert draft.status_code == 200, draft.text[:300]
    body = draft.json()
    assert sum(len(s["steps"]) for s in body["content"]["sections"]) == SECTIONS * STEPS
    assert len(body["issues"]) == ISSUES and {i["status"] for i in body["issues"]} == {"ACKNOWLEDGED"}
    assert body["latestCorrection"]["status"] == "ERROR"
    assert api.get(f"{base}/draft/preview").status_code == 200
    same = api.put(f"{base}/draft/content", headers=auth.headers(str(uuid.uuid4())), json={
        "expectedVersionId": ids["version"], "expectedRevision": 1, "content": body["content"]})
    assert same.status_code == 200 and same.json()["revision"] == 1, same.text[:300]
    published = api.post(f"{base}/draft/publication", headers=auth.headers(str(uuid.uuid4())), json={
        "expectedVersionId": ids["version"], "expectedRevision": 1, "confirmed": True,
        "acknowledgedIssueIds": []})
    assert published.status_code == 200, published.text[:300]
    listing = api.get(f"{base}/published")
    assert listing.status_code == 200 and len(listing.json()["sections"]) == SECTIONS
    detail = api.get(f"{base}/published/sections/{ids['sections'][0]}")
    assert detail.status_code == 200 and len(detail.json()["section"]["steps"]) == STEPS


def test_ordered_queries_fit_the_smallest_sort_buffer(db_engine):
    """Every step at the maximum width: the ordered reads succeed even with a 32 KB buffer."""
    ids = build_max_draft(db_engine, long_steps=STEPS)
    with Session(db_engine) as db:
        db.execute(text(f"SET SESSION sort_buffer_size = {MIN_SORT_BUFFER}"))
        try:
            version = db.get(ManualVersion, ids["version"])
            rows = load_rows(db, version.id)
            assert sum(len(steps) for steps in rows.steps.values()) == SECTIONS * STEPS
            assert latest_correction(db, version.id).input_text == LONG_INPUT
            assert len(open_issues(db, version)) == ISSUES
            assert len(current_acknowledgements(db, version)) == ISSUES
        finally:  # the pooled connection keeps session variables
            db.rollback()
            db.execute(text("SET SESSION sort_buffer_size = DEFAULT"))
            db.commit()

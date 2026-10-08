"""Free-form correction regression through real HTTP/MySQL and raw AI/STT fixtures."""
import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.ai.contracts import StructureSnapshot
from app.ai.fake import structure_to_raw
from app.db.models import InterviewEvaluation, InterviewTurn, ManualSection, ManualVersion
from e2e.interview_helpers import interview_case, scenario_server
from e2e.test_interview_photo_lifecycle_http import (
    assert_draft_photos_commit,
    assert_review_commit,
    build_lifecycle_provider,
    finish_answers,
    get,
    link,
    manual,
    photo,
    poll,
    review_url,
    review_with_section,
    section,
    upload,
    voiced_input,
)

NATURAL = "보조 확인은 이제 필요 없으니 목록에서 없애 주시겠어요?"
COMBINED = "보조 확인은 빼고 첫 업무의 이름을 마감 확인으로 바꿔 주세요. 금액을 확인하는 정산 업무도 추가해 주세요."


def freeform_provider():
    provider = build_lifecycle_provider()
    provider.on("transcribe", lambda _: NATURAL)

    def revise(data):
        raw = structure_to_raw(StructureSnapshot.model_validate(data["current"]))
        instruction = data["instruction"]
        if instruction == "fixture-clarify":
            return {"outcome": "CLARIFICATION_REQUIRED", "summary": None, "structure": raw}
        target = data["target"].get("target_id")
        removed = target or next(s["ref"] for s in raw["sections"] if s["title"] == "보조 확인")
        raw["sections"] = [s for s in raw["sections"] if s["ref"] != removed]
        if instruction == COMBINED:
            raw["sections"][0]["title"] = "마감 확인"
            raw["sections"].append({"ref": "new-1", "category": "COMMON_TASK", "shift_ref": None,
                                    "title": "정산", "steps": [{"ref": "new-2", "instruction": "금액을 확인해요.",
                                                                  "checklist_item": False}]})
        elif instruction == "fixture-outside-target":
            raw["sections"][0]["title"] = "무관한 변경"
        elif instruction == "fixture-unknown-id":
            raw["sections"][0]["ref"] = "unknown-id"
        return {"outcome": "APPLIED", "summary": "보조 확인을 제외했어요." if data.get("summary") else None,
                "structure": raw}

    provider.on("revise_structure", revise)
    return provider


@pytest.fixture
def draft_case(real_db, tmp_path):
    with (scenario_server(tmp_path, "e2e.test_correction_freeform_http:freeform_provider") as origin,
          interview_case(real_db, origin) as case):
        sid, listing = finish_answers(case)
        review = review_with_section(listing)
        survivor, removed = review["content"]["sections"][:2]
        expected = [photo(upload(case))]
        response = link(case, sid, review, survivor["id"], expected)
        assert response.status_code == 200, response.text
        listing = get(case, case.url(sid, "/reviews"))
        response = case.post(case.url(sid, "/completion"), {
            "expectedRevision": listing["sessionRevision"], "reviewRevisions": [
                {"intentId": item["intentId"], "revision": item["revision"]} for item in listing["items"]]})
        assert response.status_code == 202, response.text
        case.wait(sid, lambda value: value["phase"] == "COMPLETED")
        yield case, get(case, manual(case, "/draft")), survivor["id"], removed["id"], expected


@pytest.mark.parametrize("command", ["voice", "combined", "fixture-outside-target", "fixture-unknown-id", "fixture-clarify"])
def test_freeform_correction_commit_and_rejected_output_preservation(draft_case, command):
    case, before, survivor, removed, expected = draft_case
    body = {"expectedVersionId": before["versionId"], "expectedRevision": before["revision"],
            "target": {"kind": "MANUAL", "targetId": None} if command == "combined" else {
                "kind": "SECTION", "targetId": removed},
            "input": voiced_input(case) if command == "voice" else {
                "method": "TEXT", "text": COMBINED if command == "combined" else command}}
    response = case.post(manual(case, "/draft/corrections"), body)
    assert response.status_code == 202, response.text
    done = poll(case, manual(case, f"/draft/corrections/{response.json()['id']}"),
                lambda value: value["status"] in {"SUCCEEDED", "ERROR"})
    after = get(case, manual(case, "/draft"))
    success = command in {"voice", "combined"}
    assert done["status"] == ("SUCCEEDED" if success else "ERROR"), done
    assert after["revision"] == before["revision"] + int(success)
    if success:
        assert removed not in {s["id"] for s in after["content"]["sections"]}
        if command == "combined":
            assert section(after, survivor)["title"] == "마감 확인"
            assert any(s["title"] == "정산" for s in after["content"]["sections"])
        else:
            assert section(after, survivor) == section(before, survivor)
    else:
        assert done["error"]["code"] == ("CORRECTION_CLARIFICATION_REQUIRED" if command == "fixture-clarify"
                                         else "AI_PROCESSING_FAILED")
        assert after["content"] == before["content"] and after["issues"] == before["issues"]
    assert section(after, survivor)["photos"] == expected
    assert_draft_photos_commit(case, before["versionId"], survivor, expected)
    with Session(case.engine) as db:
        saved = db.get(ManualVersion, before["versionId"])
        assert saved.revision == after["revision"]
        rows = list(db.scalars(select(ManualSection).where(ManualSection.version_id == saved.id)))
        assert {(s.id, s.title) for s in rows} == {(s["id"], s["title"]) for s in after["content"]["sections"]}


def test_natural_review_deletion_preserves_photos_and_interview_history(real_db, tmp_path):
    with (scenario_server(tmp_path, "e2e.test_correction_freeform_http:freeform_provider") as origin,
          interview_case(real_db, origin) as case):
        sid, listing = finish_answers(case)
        review = review_with_section(listing)
        survivor, removed = review["content"]["sections"][:2]
        expected = [photo(upload(case))]
        response = link(case, sid, review, survivor["id"], expected)
        assert response.status_code == 200, response.text
        review = response.json()

        def history():
            with Session(case.engine) as db:
                return tuple(tuple(dict(row) for row in db.execute(select(model.__table__).where(
                    model.session_id == sid, *([model.turn_kind != "CORRECTION"] if model is InterviewTurn else [])
                ).order_by(model.id)).mappings()) for model in (InterviewTurn, InterviewEvaluation))

        before_history = history()
        before_state = case.get(sid)
        url = review_url(case, sid, review["intentId"])
        response = case.post(url + "/corrections", {"expectedRevision": review["revision"],
                                                   "input": {"method": "TEXT", "text": NATURAL}})
        assert response.status_code == 202, response.text
        after = poll(case, url, lambda value: value["status"] in {"READY", "ERROR"})
        assert after["status"] == "READY", after
        assert removed["id"] not in {s["id"] for s in after["content"]["sections"]}
        assert_review_commit(case, sid, after, survivor["id"], expected)
        assert history() == before_history
        after_state = case.get(sid)
        assert (after_state["revision"], after_state["phase"], after_state["questions"]) == (
            before_state["revision"], before_state["phase"], before_state["questions"])

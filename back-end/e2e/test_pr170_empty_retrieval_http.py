"""Empty BM25 hits cannot admit invented draft steps or times through real HTTP."""
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import ManualSection, ManualStep, ManualVersion
from e2e.interview_helpers import interview_case, scenario_server
from e2e.test_interview_photo_lifecycle_http import finish_answers, get, manual


def empty_retrieval_provider():
    from app.ai.contracts import StructureSnapshot
    from app.ai.fake import FakeAiProvider, structure_to_raw

    def summary(data):
        chunk = data["evidence"][0]
        return {"summary": "zzzzzz", "structure": {"shifts": [], "missing_information": [],
                "sections": [{"ref": "new-1", "title": "zzzzzz", "category": "COMMON_TASK", "shift_ref": None,
                              "steps": [{"ref": "new-2", "instruction": chunk["text"], "checklist_item": False,
                                         "evidence_ids": [chunk["id"]]}]}]}}

    def draft(data):
        assert data["evidence"] == []  # real retrieval: titles/summary share no grams with answers
        raw = {"shifts": [], "sections": [], "missing_information": []}
        for review in data["reviews"]:
            part = structure_to_raw(StructureSnapshot.model_validate(review["structure"]))
            for key, values in part.items():
                raw[key].extend(values)
        raw["sections"][0]["steps"].append({"ref": "new-1", "instruction": "고객에게 보증금을 받아요.",
                                            "checklist_item": False, "evidence_ids": []})
        raw["shifts"].append({"ref": "new-2", "name": "추가 근무조", "start_time": "07:00", "end_time": "11:00",
                              "ends_next_day": False, "evidence_ids": []})
        return {"structure": raw}

    return FakeAiProvider(auto_cite=False).on("summarize_intent", summary).on("compose_draft", draft)


def test_empty_retrieval_preserves_reviewed_steps_and_leaves_new_times_unknown(real_db, tmp_path):
    with (scenario_server(tmp_path, "e2e.test_pr170_empty_retrieval_http:empty_retrieval_provider") as origin,
          interview_case(real_db, origin) as case):
        sid, listing = finish_answers(case)
        expected = {step["id"]: step["instruction"] for review in listing["items"]
                    for section in review["content"]["sections"] for step in section["steps"]}
        response = case.post(case.url(sid, "/completion"), {
            "expectedRevision": listing["sessionRevision"], "reviewRevisions": [
                {"intentId": review["intentId"], "revision": review["revision"]} for review in listing["items"]]})
        assert response.status_code == 202, response.text
        case.wait(sid, lambda body: body["phase"] == "COMPLETED")
        draft = get(case, manual(case, "/draft"))
        assert {step["id"]: step["instruction"] for section in draft["content"]["sections"]
                for step in section["steps"]} == expected
        [shift] = draft["content"]["shifts"]
        assert (shift["startTime"], shift["endTime"], shift["endsNextDay"]) == (None, None, None)
        with Session(real_db) as db:
            version = db.get(ManualVersion, draft["versionId"])
            assert version.generation_input_snapshot["evidence"] == []
            assert version.status == "DRAFT" and version.generation_status == "READY"
            steps = list(db.scalars(select(ManualStep).join(ManualSection, ManualStep.section_id == ManualSection.id)
                                   .where(ManualSection.version_id == version.id)))
            assert {step.id: step.instruction for step in steps} == expected
        assert get(case, manual(case, "/draft"))["content"] == draft["content"]

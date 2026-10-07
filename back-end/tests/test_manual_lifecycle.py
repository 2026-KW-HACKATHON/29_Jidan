"""#120 x #118 integration: interview -> generated draft -> review -> publication -> worker reading,
and the draft-replacement guards between a new interview and draft corrections (Fake AI)."""

import threading
import uuid

import pytest
from sqlalchemy.orm import Session

from app.ai.fake import FakeOutcome
from app.db.models import ManualDraftCorrection, Store
from app.media.storage import LocalMediaStorage, set_media_storage
from tests.api_contract import login
from tests.factories import make_regular_grant, make_worker
from tests.test_interview_api import INSUFFICIENT, build_ctx, code, media, rows
from tests.test_interview_reviews import WORK, put_photos, summaries

M = "/api/stores/{store}/manual"


@pytest.fixture
def flow(api, db_engine, fake_ai, tmp_path):
    set_media_storage(LocalMediaStorage(tmp_path / "media"))
    fake_ai.on("summarize_intent", summaries)
    yield build_ctx(api, db_engine)
    set_media_storage(None)


def url(flow, tail=""):
    return M.format(store=flow.store) + tail


def generate(flow, fake_ai, *, needs_detail=True, photo=True):
    """Run a whole interview (WORK_STRUCTURE ends NEEDS_DETAIL) and the draft generation."""
    sid = flow.started()
    if needs_detail:
        fake_ai.script("judge_sufficiency", *[FakeOutcome.ok(INSUFFICIENT)] * 6)
        for _ in range(6):
            flow.answer_and_run(sid)
        flow.finish_all(sid, 5)
    else:
        flow.finish_all(sid, 6)
    if photo:
        put_photos(flow, sid, WORK, [media(flow)])
    assert flow.complete(sid).status_code == 202
    flow.run()
    assert flow.get(sid)["status"] == "COMPLETED"
    return sid


def owner_post(flow, tail, body, key=None):
    return flow.api.post(url(flow, tail), json=body, headers=flow.auth.headers(key or str(uuid.uuid4())))


def test_interview_to_published_manual_read_by_a_worker(flow, fake_ai):
    with Session(flow.engine) as db:
        worker = make_worker(db)
        make_regular_grant(db, db.get(Store, flow.store), worker)
        db.commit()
        worker_id = worker.id
    sid = generate(flow, fake_ai)

    state = flow.api.get(url(flow)).json()
    draft = flow.api.get(url(flow, "/draft")).json()
    assert state == {"storeId": flow.store, "currentPublishedVersionId": None,
                     "draftVersionId": draft["versionId"], "interviewSessionId": sid}
    assert (draft["revision"], draft["generationStatus"], draft["latestCorrection"]) == (1, "READY", None)
    assert flow.api.get(url(flow, "/draft/preview")).json()["content"] == draft["content"]
    open_ids = [i["id"] for i in draft["issues"] if i["status"] == "OPEN"]
    assert open_ids, draft["issues"]  # at least the NEEDS_DETAIL intent (no target)
    assert any(i["intentId"] == flow.intents[WORK] for i in draft["issues"])

    # Publication without the owner's acknowledgement is refused, then accepted with it.
    missing = owner_post(flow, "/draft/publication", {
        "expectedVersionId": draft["versionId"], "expectedRevision": 1, "confirmed": True,
        "acknowledgedIssueIds": []})
    assert (missing.status_code, code(missing)) == (409, "MANUAL_REVIEW_REQUIRED")
    acked = owner_post(flow, "/draft/acknowledgements", {
        "expectedVersionId": draft["versionId"], "expectedRevision": 1, "issueIds": open_ids[:1],
        "confirmed": True, "note": "운영하면서 정할게요"})
    assert acked.json()["revision"] == 2
    published = owner_post(flow, "/draft/publication", {
        "expectedVersionId": draft["versionId"], "expectedRevision": 2, "confirmed": True,
        "acknowledgedIssueIds": open_ids[1:]})
    assert published.status_code == 200, published.text
    assert published.json()["content"] == draft["content"]

    login(flow.api, worker_id)
    listing = flow.api.get(url(flow, "/published")).json()
    assert listing["versionId"] == draft["versionId"] and listing["versionNumber"] == 1
    assert [s["id"] for s in listing["sections"]] == [s["id"] for s in draft["content"]["sections"]]
    first = draft["content"]["sections"][0]["id"]
    detail = flow.api.get(url(flow, f"/published/sections/{first}"),
                          params={"expectedVersionId": draft["versionId"]})
    assert detail.status_code == 200 and detail.json()["section"]["id"] == first
    notes = flow.api.get("/api/users/me/notifications").json()["items"]
    assert [n["type"] for n in notes] == ["MANUAL_PUBLISHED"]

    # A second interview makes version 2; the worker keeps reading version 1 meanwhile.
    flow.auth = login(flow.api, flow.owner)
    second = flow.start()
    assert second.status_code == 201, second.text
    assert flow.api.get(url(flow)).json()["currentPublishedVersionId"] == draft["versionId"]
    login(flow.api, worker_id)
    assert flow.api.get(url(flow, "/published")).json()["versionId"] == draft["versionId"]


def test_running_correction_blocks_a_new_interview_then_the_draft_does(flow, fake_ai):
    generate(flow, fake_ai, needs_detail=False, photo=False)
    draft = flow.api.get(url(flow, "/draft")).json()
    accepted = owner_post(flow, "/draft/corrections", {
        "expectedVersionId": draft["versionId"], "expectedRevision": 1,
        "target": {"kind": "MANUAL", "targetId": None}, "input": {"method": "TEXT", "text": "그대로 둬요"}})
    assert accepted.status_code == 202
    blocked = flow.start()
    assert (blocked.status_code, code(blocked)) == (409, "MANUAL_CORRECTION_IN_PROGRESS")
    flow.run()  # NO_CHANGE: SUCCEEDED
    after = flow.start()
    assert (after.status_code, code(after)) == (409, "INTERVIEW_ALREADY_EXISTS")


def _race(*calls):
    barrier = threading.Barrier(len(calls))
    results = [None] * len(calls)

    def run(index, call):
        barrier.wait(10)
        results[index] = call()

    threads = [threading.Thread(target=run, args=(i, c)) for i, c in enumerate(calls)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(60)
    return results


@pytest.mark.mysql
@pytest.mark.parametrize("db_engine", ["mysql"], indirect=True)
def test_correction_and_new_interview_race(flow, fake_ai):
    from fastapi.testclient import TestClient

    from app.main import app

    generate(flow, fake_ai, needs_detail=False, photo=False)
    draft = flow.api.get(url(flow, "/draft")).json()

    def correction():
        with TestClient(app, raise_server_exceptions=False) as client:
            auth = login(client, flow.owner)
            return client.post(url(flow, "/draft/corrections"), headers=auth.headers(str(uuid.uuid4())), json={
                "expectedVersionId": draft["versionId"], "expectedRevision": 1,
                "target": {"kind": "MANUAL", "targetId": None}, "input": {"method": "TEXT", "text": "a"}})

    def start():
        with TestClient(app, raise_server_exceptions=False) as client:
            auth = login(client, flow.owner)
            return client.post(url(flow, "/interviews"), json={}, headers=auth.headers(str(uuid.uuid4())))

    for _ in range(3):
        results = _race(correction, start, start)
        assert results[0].status_code in (202, 409), results[0].text
        for response in results[1:]:
            assert response.status_code == 409 and code(response) in (
                "MANUAL_CORRECTION_IN_PROGRESS", "INTERVIEW_ALREADY_EXISTS"), response.text
        flow.run()
    assert len(rows(flow, ManualDraftCorrection)) >= 1


@pytest.mark.mysql
@pytest.mark.parametrize("db_engine", ["mysql"], indirect=True)
def test_publication_and_new_interview_race(flow, fake_ai):
    from fastapi.testclient import TestClient

    from app.main import app

    generate(flow, fake_ai, needs_detail=False, photo=False)
    draft = flow.api.get(url(flow, "/draft")).json()
    issues = [i["id"] for i in draft["issues"]]

    def call(path, body):
        def run():
            with TestClient(app, raise_server_exceptions=False) as client:
                auth = login(client, flow.owner)
                return client.post(url(flow, path), json=body, headers=auth.headers(str(uuid.uuid4())))
        return run

    publish = call("/draft/publication", {"expectedVersionId": draft["versionId"], "expectedRevision": 1,
                                          "confirmed": True, "acknowledgedIssueIds": issues})
    start = call("/interviews", {})
    results = _race(publish, start, start)
    assert results[0].status_code == 200, results[0].text
    starts = sorted((r.status_code, r.json().get("code")) for r in results[1:])
    # Started before the publication: the draft still existed (409); after it: one new draft.
    assert starts in ([(201, None), (409, "INTERVIEW_ALREADY_EXISTS")],
                      [(409, "INTERVIEW_ALREADY_EXISTS"), (409, "INTERVIEW_ALREADY_EXISTS")]), starts
    state = flow.api.get(url(flow)).json()
    assert state["currentPublishedVersionId"] == draft["versionId"]

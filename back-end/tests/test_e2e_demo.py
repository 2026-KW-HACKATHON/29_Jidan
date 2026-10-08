"""Runs the #122 demo scenario (e2e/demo_scenario.py) against a real uvicorn server on MySQL.

It waits in real time for invitation mail (the server's mail job runs every 60 s) and runs the
manual/AI steps M0-M14 on the deterministic fake AI in the server's background task runner. The
unanswered work request, which waits about two more minutes for its deadline, runs only with
JIDAN_E2E_FULL=1; the CLI runner includes it by default.
"""
import os
import socket

import httpx
import pytest
from sqlalchemy.orm import Session

from e2e import demo_scenario
from e2e.mailbox import Mailbox
from tests.interview_factories import ensure_question_set

FULL = os.getenv("JIDAN_E2E_FULL") == "1"


@pytest.mark.mysql
@pytest.mark.parametrize("db_engine", ["mysql"], indirect=True)  # db_engine empties the tables afterwards
def test_demo_scenario_end_to_end(db_engine, monkeypatch, tmp_path):
    monkeypatch.setenv("APP_ENV", "local")
    monkeypatch.delenv("KAKAO_REST_API_KEY", raising=False)  # always the local Kakao fake here
    with Session(db_engine) as db:  # migration 0040's seed; db_engine empties every table between tests
        ensure_question_set(db)
        db.commit()
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    origin = "http://localhost:5173"
    mailbox = Mailbox().start()
    process, password = demo_scenario.start_server(port, origin, mailbox.port, media_root=str(tmp_path / "media"))
    try:
        scenario = demo_scenario.Scenario(f"http://127.0.0.1:{port}", origin, password, fake_kakao=True,
                                          mailbox=mailbox, realtime_expiry=FULL)
        ok = scenario.execute()
    finally:
        process.terminate()
        process.wait(timeout=10)
        mailbox.stop()
    assert ok, scenario.results
    expected = [label for label, method in demo_scenario.Scenario.STEPS
                if FULL or method not in demo_scenario.Scenario.REALTIME_STEPS]
    expected += [label for label, _method in demo_scenario.Scenario.MANUAL_STEPS]
    assert [(status, label) for status, label, _ in scenario.results] == [("PASS", label) for label in expected]


@pytest.mark.parametrize("query", ["서울특별시 노원구 광운로 20", "서울 노원구 광운로 20", "서울  노원구 광운로  20"])
def test_fake_kakao_accepts_the_postcode_widget_short_city_name(query):
    """The postcode widget gives "서울 …"; the fake must find it like Kakao Local does."""
    from e2e.serve import KAKAO_HOST, kakao_answer

    with httpx.Client(transport=httpx.MockTransport(kakao_answer), headers={"Authorization": "KakaoAK x"}) as client:
        documents = client.get(KAKAO_HOST + "/v2/local/search/address.json", params={"query": query}).json()["documents"]
    assert len(documents) == 1 and documents[0]["road_address"]["zone_no"] == "01897"


def test_fake_kakao_still_rejects_an_unknown_address():
    from e2e.serve import KAKAO_HOST, kakao_answer

    with httpx.Client(transport=httpx.MockTransport(kakao_answer), headers={"Authorization": "KakaoAK x"}) as client:
        documents = client.get(KAKAO_HOST + "/v2/local/search/address.json",
                               params={"query": "서울 노원구 광운로 21"}).json()["documents"]
    assert documents == []



def test_server_environment_enables_guidance_with_the_production_parser(monkeypatch):
    from app.interview.settings import guidance_responses_enabled
    from e2e.demo_scenario import server_env

    environment, _password = server_env("http://127.0.0.1:8000", 1025)
    monkeypatch.setenv("INTERVIEW_GUIDANCE_RESPONSES", environment["INTERVIEW_GUIDANCE_RESPONSES"])
    assert guidance_responses_enabled() is True

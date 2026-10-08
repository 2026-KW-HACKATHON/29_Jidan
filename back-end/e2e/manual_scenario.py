"""Manual and AI steps of the demo E2E (M0-M14), run after the jobs steps by `e2e.demo_scenario`.

The server runs its AI work in the background task runner; every step waits for the result by
polling the public GET endpoints (`ManualSteps.poll`). In fake mode (`e2e.ai_scenario`) the
outputs are fixed, so steps assert exact structures; in live mode the model writes the summaries,
the draft, corrections, answers and transcripts, and the steps assert only the invariants
(`self.live`). Steps that need many extra calls (M14's second interview) run in fake mode only.

Readers: worker A (REGULAR + TEMPORARY from the jobs steps), worker C (REGULAR) and worker D, who
gets a TEMPORARY grant only in M0, so MANUAL_PUBLISHED's audience (every valid grant) is pinned
down; worker B has no grant at the store and is refused.
"""
import shutil
import subprocess
import tempfile
import time
from pathlib import Path

from e2e import ai_scenario
from tests import media_samples

POLL_INTERVAL = 0.5
FAKE_WAIT = 30.0
LIVE_WAIT = 300.0
VOICE_TEXT = "포스 마감은 정산 버튼을 누르고 영수증을 금고에 넣어요."
FAKE_TRANSCRIPT = "테스트 전사 결과예요."
REVIEW_CORRECTION = "오픈조는 8시 30분에 시작해요."
DRAFT_CORRECTION = "출력한 영수증은 사진을 찍어 점주에게 보내요."
EDITED_STEP = "포스 화면에서 마감 정산 버튼을 눌러요."
# What the owner says per intent (the live model summarizes these; the fake ignores them).
ANSWERS = {
    "WORK_STRUCTURE": "오픈조는 오전 9시부터 오후 3시까지, 마감조는 오후 3시부터 밤 11시까지 일해요.",
    "COMMON_TASKS": "포스 마감은 마감 정산 버튼을 누르고 출력한 영수증을 금고에 넣어요. 손님이 오면 인사하고 주문을 받아요.",
    "SHIFT_TASKS": "오픈조는 불을 켜고 커피 머신을 예열해요. 마감조는 바닥을 청소하고 문을 잠가요.",
    "RULES": "근무 중에는 앞치마를 착용하고 휴대폰은 보관함에 넣어요.",
    "EQUIPMENT": "커피 머신은 매일 닦아요.",
    "EXCEPTIONS": "손님 불만이 있으면 점주에게 바로 전화해요.",
}
PROBE_ANSWER = "정산, 영수증 보관, 청소 순서로 해요. 끝나면 점주에게 메시지를 보내요."


def _check(condition, message, response=None):
    from e2e.demo_scenario import check

    check(condition, message, response)


def _key():
    from e2e.demo_scenario import key

    return key()


class ManualSteps:
    """Mixin for `Scenario`: needs `self.state`, `self.expect_notification`, `self.access_grants`."""

    live = False  # set by Scenario from its AI mode

    MANUAL_STEPS = (
        ("M0 temporary-only reader", "step_m0_temporary_reader"),
        ("M1 manual state", "step_m1_manual_state"),
        ("M2 photo and recording upload", "step_m2_uploads"),
        ("M3 recording transcription", "step_m3_transcription"),
        ("M4 interview start", "step_m4_interview_start"),
        ("M5 interview answers (text, voice, photo, probes)", "step_m5_interview_answers"),
        ("M6 understanding review", "step_m6_review"),
        ("M7 draft generation", "step_m7_draft"),
        ("M8 draft edit and correction", "step_m8_draft_edit_and_correction"),
        ("M9 publication and MANUAL_PUBLISHED", "step_m9_publication"),
        ("M10 workers read the manual", "step_m10_worker_reading"),
        ("M11 Q&A answered with citations", "step_m11_qa_answered"),
        ("M12 Q&A without grounds and by voice", "step_m12_qa_needs_owner_and_voice"),
        ("M13 ended access is refused", "step_m13_access_revoked"),
        ("M14 a new version replaces the old", "step_m14_new_version"),
    )
    FAKE_ONLY_STEPS = frozenset({"step_m14_new_version"})

    # -- helpers -------------------------------------------------------------------------------

    @property
    def wait_seconds(self) -> float:
        return LIVE_WAIT if self.live else FAKE_WAIT

    def manual_url(self, tail: str = "") -> str:
        return f"/api/stores/{self.state['store_id']}/manual{tail}"

    def poll(self, actor, path: str, until, what: str, params=None) -> dict:
        """GET `path` until `until(body)` holds; fails with the last body's state after the wait."""
        deadline = time.monotonic() + self.wait_seconds
        while True:
            response = actor.call("GET", path, expect=200, params=params)
            body = response.json()
            if until(body):
                return body
            if time.monotonic() > deadline:
                state = {k: body.get(k) for k in ("status", "phase", "processing", "error", "generationStatus")
                         if k in body}
                _check(False, f"{what}: still {state} after {self.wait_seconds:.0f} s", response)
            time.sleep(POLL_INTERVAL)

    def upload_manual(self, data: bytes, purpose: str, filename: str, mime: str, key: str | None = None,
                      expect: int = 201):
        return self.state["owner"].call(
            "POST", self.manual_url("/media"), key=key or _key(), expect=expect,
            data={"purpose": purpose}, files={"file": (filename, data, mime)})

    def recording(self) -> tuple[bytes, str, str]:
        """(bytes, filename, mime) of the owner's spoken answer: silent WAV for the fake, synthesized
        Korean speech (macOS say + afconvert) for the real transcription model."""
        if not self.live:
            return media_samples.wav_seconds(3), "answer.wav", "audio/wav"
        return speech(VOICE_TEXT), "answer.m4a", "audio/mp4"

    def owner_write(self, method: str, tail: str, body: dict, expect: int, key: str | None = None):
        return self.state["owner"].call(method, self.manual_url(tail), json=body, key=key or _key(), expect=expect)

    def session(self) -> dict:
        return self.state["owner"].call("GET", self.manual_url(f"/interviews/{self.state['interview']}"),
                                        expect=200).json()

    def wait_session(self, until, what: str) -> dict:
        return self.poll(self.state["owner"], self.manual_url(f"/interviews/{self.state['interview']}"),
                         until, what)

    def draft(self) -> dict:
        return self.state["owner"].call("GET", self.manual_url("/draft"), expect=200).json()

    def run_interview(self, *, voice_transcription: str | None = None, photo: str | None = None,
                      fail_once: bool = False) -> dict:
        """Answer every open question until READY_TO_GENERATE; returns {intentKey: [question kinds]}.
        `fail_once` (fake AI only) makes the SHIFT_TASKS answer's first evaluation fail."""
        intents = {i["id"]: i["key"] for i in self.session()["intents"]}
        asked: dict[str, list[tuple[str, int]]] = {}
        limit = len(intents) * 6  # depth 0-5 per intent; retries keep the depth
        for _ in range(limit + 1):
            state = self.wait_session(lambda s: s["phase"] in ("COLLECTING", "READY_TO_GENERATE", "ERROR"),
                                      "interview waits for an answer")
            _check(state["phase"] != "ERROR", f"interview error: {state.get('error')}")
            if state["phase"] == "READY_TO_GENERATE":
                return asked
            open_questions = [q for q in state["questions"] if not q["answered"]]
            _check(len(open_questions) == 1, f"exactly one open question: {len(open_questions)}")
            question = open_questions[0]
            self.check_question_cards(state, question)
            intent = intents[question["intentId"]]
            asked.setdefault(intent, []).append((question["kind"], question["depth"]))
            body = {"expectedRevision": state["revision"], "questionId": question["id"],
                    "input": {"method": "TEXT",
                              "text": ANSWERS[intent] if question["kind"] == "BASE" else PROBE_ANSWER}}
            if voice_transcription and intent == "RULES" and question["kind"] == "BASE":
                body["input"] = {"method": "VOICE", "transcriptionId": voice_transcription}
            if photo and intent == "WORK_STRUCTURE" and question["kind"] == "BASE":
                body["photoIds"] = [photo]
            failing = fail_once and not self.live and intent == "SHIFT_TASKS" and question["kind"] == "BASE"
            if failing:
                body["input"]["text"] += f" {ai_scenario.FAIL_ONCE}"
            accepted = self.owner_write("POST", f"/interviews/{self.state['interview']}/answers", body, 202).json()
            # The answered question stays on screen while its evaluation runs (OpenAPI 0.11.0).
            _check(accepted["questions"] == [] and accepted["processing"]["kind"] == "EVALUATION",
                   "an accepted answer starts its evaluation")
            _check(accepted["lastAnsweredQuestion"] == {**question, "answered": True},
                   "the accepted answer keeps its question, guidance and cards as shown")
            self.state.setdefault("guided_questions", []).append(question)
            if failing:
                self.failed_evaluation_keeps_its_question(accepted["lastAnsweredQuestion"])
        _check(False, f"the interview asked more than {limit} questions")
        return asked

    def check_question_cards(self, state: dict, question: dict) -> None:
        """Progress reflects observed coverage; photos refer to real reviews in this session."""
        cards = question["guidanceCards"]
        _check(len(cards) <= 5 and len({c["id"] for c in cards}) == len(cards), "bounded, unique card IDs")
        progress = [c for c in cards if c["type"] == "PROGRESS_CHECKLIST"]
        _check(len(progress) == 1, "one generated progress checklist")
        checklist = progress[0]
        _check(len(checklist["items"]) == len(state["intents"]), "checklist covers the actual interview intents")
        identity = (checklist["id"], [i["id"] for i in checklist["items"]])
        previous = self.state.setdefault("guidance_progress", {}).setdefault(state["id"], identity)
        _check(identity == previous, "progress IDs survive question transitions")
        current = 0
        for item, intent in zip(checklist["items"], state["intents"], strict=True):
            if item["status"] == "CURRENT":
                current += 1
                _check(intent["id"] == question["intentId"] and intent["coverage"] == "PENDING",
                       "CURRENT belongs to the actual pending question intent")
            else:
                expected = {"COVERED": "COMPLETED", "NEEDS_DETAIL": "NEEDS_DETAIL"}.get(
                    intent["coverage"], "CURRENT" if intent["id"] == question["intentId"] else "PENDING")
                _check(item["status"] == expected, "checklist status comes from observed coverage")
        pending_target = any(i["id"] == question["intentId"] and i["coverage"] == "PENDING"
                             for i in state["intents"])
        _check(current == int(pending_target), "BASE and PROBE mark their actual pending intent CURRENT")
        targets = set()
        for card in (c for c in cards if c["type"] == "PHOTO_SUGGESTIONS"):
            target = card["attachmentTarget"]
            if target is None:
                continue
            identity = (target["intentId"], target["target"], target["sectionId"])
            _check(identity not in targets, "distinct photo targets use distinct cards")
            targets.add(identity)
            intent = next((i for i in state["intents"] if i["id"] == target["intentId"]), None)
            _check(intent is not None, "photo target belongs to this session")
            review = self.state["owner"].call("GET", self.manual_url(
                f"/interviews/{state['id']}/intents/{target['intentId']}/review"), expect=200).json()
            _check(review["status"] == "READY", "photo target has a real READY review")
            if target["target"] == "WORK_STRUCTURE":
                _check(intent["stage"] == "WORK_STRUCTURE" and target["sectionId"] is None,
                       "work structure photo target uses the WORK_STRUCTURE stage")
            else:
                _check(target["sectionId"] in {s["id"] for s in review["content"]["sections"]},
                       "photo target is an actual section of the READY review")

    def failed_evaluation_keeps_its_question(self, snapshot: dict) -> None:
        """A failed evaluation is ERROR with the answered question still shown; the retry keeps it
        and, once judged, moves on without it."""
        sid = self.state["interview"]
        failed = self.wait_session(lambda s: s["phase"] == "ERROR", "the evaluation fails once")
        _check(failed["processing"]["kind"] == "EVALUATION" and failed["lastAnsweredQuestion"] == snapshot,
               "the failed evaluation keeps its answered question")
        retried = self.owner_write("POST", f"/interviews/{sid}/retries",
                                   {"expectedRevision": failed["revision"]}, 202).json()
        _check(retried["lastAnsweredQuestion"] == snapshot and retried["error"] is None,
               "the retry shows the same question while it runs")
        moved = self.wait_session(lambda s: s["phase"] != "PROCESSING" or s["processing"]["kind"] != "EVALUATION",
                                  "the retried evaluation is applied")
        _check(moved["lastAnsweredQuestion"] is None, "a judged answer leaves no question behind")

    def start_interview(self) -> dict:
        started = self.owner_write("POST", "/interviews", {}, 201).json()
        self.state["interview"] = started["id"]
        return started

    def complete_interview(self) -> dict:
        listing = self.state["owner"].call(
            "GET", self.manual_url(f"/interviews/{self.state['interview']}/reviews"), expect=200).json()
        body = {"expectedRevision": listing["sessionRevision"],
                "reviewRevisions": [{"intentId": i["intentId"], "revision": i["revision"]} for i in listing["items"]]}
        self.owner_write("POST", f"/interviews/{self.state['interview']}/completion", body, 202)
        state = self.wait_session(lambda s: s["phase"] in ("COMPLETED", "ERROR"), "draft generation")
        _check(state["status"] == "COMPLETED", f"the interview completed: {state.get('error')}")
        return self.poll(self.state["owner"], self.manual_url("/draft"),
                         lambda d: d["generationStatus"] in ("READY", "ERROR"), "draft READY")

    def publish(self, draft: dict) -> dict:
        """Acknowledge every OPEN issue at publication and publish; returns the published manual."""
        open_ids = [i["id"] for i in draft["issues"] if i["status"] == "OPEN"]
        return self.owner_write("POST", "/draft/publication", {
            "expectedVersionId": draft["versionId"], "expectedRevision": draft["revision"], "confirmed": True,
            "acknowledgedIssueIds": open_ids}, 200).json()

    # -- steps ---------------------------------------------------------------------------------

    def step_m0_temporary_reader(self):
        """Worker D is confirmed for a shift and holds a TEMPORARY grant and nothing else."""
        owner, store_id = self.state["owner"], self.state["store_id"]
        self.register_worker("worker-d", "정대타", [{"days": ["MON", "TUE", "WED", "THU", "FRI", "SAT", "SUN"],
                                                    "startTime": "06:00", "endTime": "12:00", "endsNextDay": False}])
        job = owner.call("POST", f"/api/stores/{store_id}/job-postings", key=_key(), expect=201,
                         json=self.job_body(3, "07:00", "11:00", "E2E 오전 대타")).json()
        worker = self.state["worker-d"]
        application = worker.call("POST", f"/api/job-postings/{job['id']}/applications", key=_key(), expect=201,
                                  json={"introduction": "오전 근무 가능합니다."}).json()
        self.expect_notification("owner", "NEW_APPLICATION", {
            "type": "JOB_APPLICATION", "applicationId": application["id"], "storeId": store_id, "jobId": job["id"]})
        base = f"/api/stores/{store_id}/job-postings/{job['id']}"
        revision = owner.call("GET", base, expect=200).json()["revision"]
        request = owner.call("POST", f"{base}/applications/{application['id']}/work-requests", key=_key(),
                             json={"expectedJobRevision": revision}, expect=201).json()
        self.expect_notification("worker-d", "WORK_REQUEST_RECEIVED", {
            "type": "WORK_REQUEST", "requestId": request["id"], "storeId": store_id, "jobId": job["id"]})
        accepted = worker.call("POST", f"/api/users/me/work-requests/{request['id']}/response", key=_key(),
                               json={"expectedRevision": 1, "decision": "ACCEPT"}, expect=200).json()
        _check(accepted["accessGrant"]["type"] == "TEMPORARY", "worker D's acceptance gives TEMPORARY access")
        work_date = self.job_body(3)["workDate"]
        events = worker.call("GET", "/api/users/me/calendar/events", params={"month": work_date[:7]},
                             expect=200).json()["events"]
        event = [e for e in events if e["jobId"] == job["id"]]
        target = {"type": "WORK_SCHEDULE", "eventId": event[0]["id"], "storeId": store_id, "workDate": work_date}
        for name in ("worker-d", "owner"):
            self.expect_notification(name, "WORK_CONFIRMED", target)
        _check(self.access_grants("worker-d") == [("TEMPORARY", "ACTIVE")], "worker D holds only TEMPORARY access")

    def step_m1_manual_state(self):
        state = self.state["owner"].call("GET", self.manual_url(), expect=200).json()
        _check(state == {"storeId": self.state["store_id"], "currentPublishedVersionId": None,
                         "draftVersionId": None, "interviewSessionId": None}, f"no manual yet: {state}")
        missing = self.state["worker-a"].call("GET", self.manual_url("/published"), expect=404)
        _check(missing.json()["code"] == "MANUAL_NOT_PUBLISHED", "a reader sees no published manual yet", missing)

    def step_m2_uploads(self):
        photo, idem = media_samples.png(), _key()
        uploaded = self.upload_manual(photo, "MANUAL_PHOTO", "counter.png", "image/png", key=idem).json()
        _check(uploaded["purpose"] == "MANUAL_PHOTO" and uploaded["mimeType"] == "image/png", "photo stored")
        replay = self.upload_manual(photo, "MANUAL_PHOTO", "counter.png", "image/png", key=idem)
        _check(replay.headers.get("Idempotent-Replayed") == "true" and replay.json()["id"] == uploaded["id"],
               "the same key and file replay the upload", replay)
        other = self.upload_manual(media_samples.png(width=33), "MANUAL_PHOTO", "counter.png", "image/png",
                                   key=idem, expect=409)
        _check(other.json()["code"] == "IDEMPOTENCY_KEY_REUSED", "another file under the same key is refused", other)
        data, filename, mime = self.recording()
        audio = self.upload_manual(data, "INTERVIEW_AUDIO", filename, mime).json()
        _check(audio["purpose"] == "INTERVIEW_AUDIO" and audio["mimeType"] == mime, "recording stored")
        self.state.update(photo=uploaded["id"], recording=audio["id"])

    def step_m3_transcription(self):
        started = self.owner_write("POST", "/transcriptions", {"mediaId": self.state["recording"]}, 202).json()
        ready = self.poll(self.state["owner"], self.manual_url(f"/transcriptions/{started['id']}"),
                          lambda t: t["status"] in ("READY", "ERROR"), "transcription")
        _check(ready["status"] == "READY", f"the recording is transcribed: {ready.get('error')}")
        if self.live:
            _check("포스" in ready["text"] or "영수증" in ready["text"], f"the transcript is the speech: {ready['text']}")
        else:
            _check(ready["text"] == FAKE_TRANSCRIPT, "the fake transcript")
        again = self.owner_write("POST", "/transcriptions", {"mediaId": self.state["recording"]}, 200).json()
        _check(again["id"] == started["id"] and again["status"] == "READY", "a READY transcription is not redone")
        self.state.update(transcription=started["id"], transcript=ready["text"])

    def step_m4_interview_start(self):
        started = self.start_interview()
        _check(len(started["intents"]) == 6 and started["status"] == "IN_PROGRESS", "six intents, in progress")
        state = self.wait_session(lambda s: s["phase"] == "COLLECTING", "the first question")
        [question] = state["questions"]
        _check((question["kind"], question["depth"], question["answered"]) == ("BASE", 0, False),
               "one BASE question at depth 0")
        _check(question["intentId"] == state["intents"][0]["id"], "the interview starts with the first intent")
        _check(state["lastAnsweredQuestion"] is None, "nothing is evaluated before the first answer")
        self.check_question_cards(state, question)
        photo_cards = [c for c in question["guidanceCards"] if c["type"] == "PHOTO_SUGGESTIONS"]
        _check(len(photo_cards) == 1 and photo_cards[0]["attachmentTarget"] is None,
               "the first question recommends photos without an attachment target")
        if not self.live:
            [card] = [c for c in question["guidanceCards"] if c["type"] == "LIST"]
            _check(question["guidance"] == ai_scenario.QUESTION_GUIDANCE, "the question shows its guidance")
            _check((card["type"], [(i["label"], i["description"]) for i in card["items"]]) == (
                "LIST", [(ai_scenario.EXAMPLE_LABEL, ai_scenario.EXAMPLE_DESCRIPTION)]),
                "the generator's example is one LIST card")
        busy = self.owner_write("POST", "/interviews", {}, 409)
        _check(busy.json()["code"] == "INTERVIEW_ALREADY_EXISTS", "one interview per store", busy)

    def step_m5_interview_answers(self):
        """Every question answered once; RULES by voice, WORK_STRUCTURE with a photo."""
        owner, sid = self.state["owner"], self.state["interview"]
        state = self.session()
        question = state["questions"][0]
        stale = self.owner_write("POST", f"/interviews/{sid}/answers", {
            "expectedRevision": state["revision"] + 5, "questionId": question["id"],
            "input": {"method": "TEXT", "text": "오래된 화면에서 보낸 답이에요."}}, 409)
        _check(stale.json()["code"] == "REVISION_CONFLICT", "a stale revision is refused", stale)
        asked = self.run_interview(voice_transcription=self.state["transcription"], photo=self.state["photo"],
                                   fail_once=True)
        turns = owner.call("GET", self.manual_url(f"/interviews/{sid}/turns"), params={"size": 100},
                           expect=200).json()["items"]
        answers = [t for t in turns if t["kind"] == "ANSWER"]
        voice = [t for t in answers if t["inputMethod"] == "VOICE"]
        _check(len(voice) == 1 and voice[0]["content"] == self.state["transcript"],
               "the voice answer stores its transcript as the answer")
        with_photo = [t for t in answers if t["photoIds"] == [self.state["photo"]]]
        _check(len(with_photo) == 1, "the photo is attached to one answer")
        replay = self.owner_write("POST", f"/interviews/{sid}/answers", {
            "expectedRevision": self.session()["revision"], "questionId": answers[0]["replyToQuestionId"],
            "input": {"method": "TEXT", "text": "다시 답해요."}}, 409)
        _check(replay.json()["code"] == "QUESTION_ALREADY_ANSWERED", "an answered question is closed", replay)
        questions = sum(len(kinds) for kinds in asked.values())
        _check(len(answers) == questions, "one answer per question")
        from sqlalchemy import select

        from app.db import session_scope
        from app.db.models import InterviewTurn

        with session_scope() as db:  # the server's committed rows, on the runner's own connection
            stored = {turn.id: (turn.guidance, turn.guidance_cards or []) for turn in db.scalars(
                select(InterviewTurn).where(InterviewTurn.session_id == sid, InterviewTurn.turn_kind == "QUESTION"))}
        shown = self.state["guided_questions"]
        _check(len(stored) == len(shown) == questions
               and all(stored[q["id"]] == (q["guidance"], q["guidanceCards"]) for q in shown),
               "every question's guidance and cards are stored as shown")
        if not self.live:
            _check(asked[ai_scenario.PROBED_ONCE] == [("BASE", 0), ("PROBE", 1)], "one probe at depth 1")
            _check(asked[ai_scenario.NEEDS_DETAIL] == [("BASE", 0)] + [("PROBE", d) for d in range(1, 6)],
                   "probes up to depth 5")
            _check(questions == 12, f"6 base questions and 6 probes: {questions}")
        self.state["asked"] = {intent: len(kinds) for intent, kinds in asked.items()}

    def step_m6_review(self):
        """Correct WORK_STRUCTURE, confirm it, and hang the photo on the 포스 section."""
        owner, sid = self.state["owner"], self.state["interview"]
        listing = self.poll(owner, self.manual_url(f"/interviews/{sid}/reviews"),
                            lambda r: len(r["items"]) == 6 and all(i["status"] != "PROCESSING" for i in r["items"]),
                            "six summarized reviews")
        items = {self.intent_key(i["intentId"]): i for i in listing["items"]}
        _check(all(i["status"] == "READY" for i in items.values()), f"six READY reviews: {[i['error'] for i in items.values()]}")
        if not self.live:
            _check(items[ai_scenario.NEEDS_DETAIL]["content"]["needsDetail"] is True,
                   "EQUIPMENT is marked as needing detail")
        work = items["WORK_STRUCTURE"]
        review_url = f"/interviews/{sid}/intents/{work['intentId']}/review"
        accepted = self.owner_write("POST", f"{review_url}/corrections", {
            "expectedRevision": work["revision"], "input": {"method": "TEXT", "text": REVIEW_CORRECTION}}, 202).json()
        _check(accepted["status"] == "PROCESSING", "the correction is accepted and processed in the background")
        stale = self.owner_write("POST", f"{review_url}/confirmations",
                                 {"expectedRevision": work["revision"], "confirmed": True}, 409)
        _check(stale.json()["code"] in ("REVIEW_PROCESSING", "REVISION_CONFLICT"),
               "the pre-correction revision can no longer be confirmed", stale)
        corrected = self.poll(owner, self.manual_url(review_url), lambda r: r["status"] in ("READY", "ERROR"),
                              "review correction")
        _check(corrected["status"] == "READY" and corrected["revision"] > work["revision"], "the correction applied")
        if not self.live:
            _check(corrected["content"]["shifts"][0]["startTime"] == ai_scenario.SHIFT_08_30
                   and corrected["content"]["summary"] == REVIEW_CORRECTION, "the opening shift moved to 08:30")
        confirmed = self.owner_write("POST", f"{review_url}/confirmations",
                                     {"expectedRevision": corrected["revision"], "confirmed": True}, 200).json()
        _check(confirmed["confirmedAt"] is not None, "the owner confirmed the corrected understanding")
        common = items["COMMON_TASKS"]
        section = self.pos_section(common["content"]["sections"])
        photos = self.owner_write("PUT", f"/interviews/{sid}/intents/{common['intentId']}/review/photos", {
            "expectedRevision": common["revision"], "target": "SECTION", "sectionId": section["id"],
            "photos": [{"mediaId": self.state["photo"], "title": "포스 화면", "caption": "마감 정산 버튼"}]}, 200).json()
        attached = self.pos_section(photos["content"]["sections"])["photos"]
        _check([p["mediaId"] for p in attached] == [self.state["photo"]], "the photo hangs on the section")
        self.state["pos_section"] = section["id"]

    def step_m7_draft(self):
        draft = self.complete_interview()
        _check(draft["generationStatus"] == "READY" and draft["revision"] == 1, "the draft is READY at revision 1")
        content = draft["content"]
        section = self.pos_section(content["sections"])
        _check(section["id"] == self.state["pos_section"], "the reviewed section keeps its id in the draft")
        _check([p["mediaId"] for p in section["photos"]] == [self.state["photo"]], "the photo is in the draft")
        _check(any(i["status"] == "OPEN" for i in draft["issues"]), "the draft lists open issues")
        if not self.live:
            _check([s["name"] for s in content["shifts"]] == ["오픈조", "마감조"]
                   and content["shifts"][0]["startTime"] == ai_scenario.SHIFT_08_30, "the corrected shifts")
            needs_detail = self.intent_id(ai_scenario.NEEDS_DETAIL)
            _check(any(i["intentId"] == needs_detail and i["status"] == "OPEN" for i in draft["issues"]),
                   "EQUIPMENT (NEEDS_DETAIL) is an open issue")
        state = self.state["owner"].call("GET", self.manual_url(), expect=200).json()
        _check(state["draftVersionId"] == draft["versionId"] and state["currentPublishedVersionId"] is None,
               "the store has a draft and nothing published")
        self.state["draft"] = draft

    def step_m8_draft_edit_and_correction(self):
        draft = self.state["draft"]
        content = draft["content"]
        self.pos_section(content["sections"])["steps"][0]["instruction"] = EDITED_STEP
        edited = self.owner_write("PUT", "/draft/content", {
            "expectedVersionId": draft["versionId"], "expectedRevision": draft["revision"], "content": content},
            200).json()
        _check(edited["revision"] == draft["revision"] + 1, "editing raises the revision")
        _check(self.pos_section(edited["content"]["sections"])["steps"][0]["instruction"] == EDITED_STEP,
               "the edit is saved")
        stale = self.owner_write("PUT", "/draft/content", {
            "expectedVersionId": draft["versionId"], "expectedRevision": draft["revision"], "content": content}, 409)
        _check(stale.json()["code"] in ("REVISION_CONFLICT", "MANUAL_VERSION_CONFLICT"),
               "an edit from an older revision is refused", stale)
        correction = self.owner_write("POST", "/draft/corrections", {
            "expectedVersionId": draft["versionId"], "expectedRevision": edited["revision"],
            "target": {"kind": "SECTION", "targetId": self.state["pos_section"]},
            "input": {"method": "TEXT", "text": DRAFT_CORRECTION}}, 202).json()
        done = self.poll(self.state["owner"], self.manual_url(f"/draft/corrections/{correction['id']}"),
                         lambda c: c["status"] in ("SUCCEEDED", "ERROR"), "draft correction")
        _check(done["status"] == "SUCCEEDED", f"the correction succeeded: {done.get('error')}")
        corrected = self.draft()
        _check(corrected["revision"] == done["resultRevision"], "the draft is at the correction's revision")
        if not self.live:
            steps = self.pos_section(corrected["content"]["sections"])["steps"]
            _check([s["instruction"] for s in steps] == [EDITED_STEP, DRAFT_CORRECTION],
                   "the correction rewrote the section's last step and kept the edit")
        self.state["draft"] = corrected

    def step_m9_publication(self):
        draft = self.state["draft"]
        preview = self.state["owner"].call("GET", self.manual_url("/draft/preview"), expect=200).json()
        _check(preview["content"] == draft["content"], "the worker preview shows the draft content")
        open_ids = [i["id"] for i in draft["issues"] if i["status"] == "OPEN"]
        refused = self.owner_write("POST", "/draft/publication", {
            "expectedVersionId": draft["versionId"], "expectedRevision": draft["revision"], "confirmed": True,
            "acknowledgedIssueIds": open_ids[1:]}, 409)
        _check(refused.json()["code"] == "MANUAL_REVIEW_REQUIRED", "every open issue must be acknowledged", refused)
        published = self.publish(draft)
        _check(published["versionId"] == draft["versionId"] and published["versionNumber"] == 1, "version 1")
        _check(published["content"] == draft["content"], "the published content is the draft")
        state = self.state["owner"].call("GET", self.manual_url(), expect=200).json()
        _check(state["currentPublishedVersionId"] == draft["versionId"] and state["draftVersionId"] is None,
               "the store points at the published version")
        target = {"type": "MANUAL", "storeId": self.state["store_id"]}
        # Every worker with a valid grant: REGULAR (A, C) and TEMPORARY only (D); never B.
        for name in ("worker-a", "worker-c", "worker-d"):
            self.expect_notification(name, "MANUAL_PUBLISHED", target)
        _check(all(n["type"] != "MANUAL_PUBLISHED" for n in self.notifications("worker-b")),
               "a worker without access is not notified")
        self.state["version"] = draft["versionId"]

    def step_m10_worker_reading(self):
        version, store_id = self.state["version"], self.state["store_id"]
        for name in ("worker-a", "worker-c", "worker-d"):
            worker = self.state[name]
            stores = worker.call("GET", "/api/users/me/stores", expect=200).json()["items"]
            mine = [s for s in stores if s["store"]["id"] == store_id]
            _check(len(mine) == 1 and mine[0]["publishedVersionId"] == version, f"{name} sees the published version")
            listing = worker.call("GET", self.manual_url("/published"), expect=200).json()
            _check(listing["versionId"] == version and self.state["pos_section"] in
                   [s["id"] for s in listing["sections"]], f"{name} lists the sections")
            detail = worker.call("GET", self.manual_url(f"/published/sections/{self.state['pos_section']}"),
                                 params={"expectedVersionId": version}, expect=200).json()
            _check([p["mediaId"] for p in detail["section"]["photos"]] == [self.state["photo"]],
                   f"{name} sees the section photo")
            photo = worker.call("GET", self.manual_url(f"/media/{self.state['photo']}/content"), expect=200)
            _check(photo.headers["content-type"].startswith("image/") and len(photo.content) > 0,
                   f"{name} reads the photo bytes")
        recording = self.state["worker-a"].call("GET", self.manual_url(f"/media/{self.state['recording']}/content"),
                                                expect=404)
        _check(recording.json()["code"] == "MANUAL_RESOURCE_NOT_FOUND", "the owner's recording is never served")
        hidden = self.state["worker-b"].call("GET", self.manual_url("/published"), expect=404)
        _check(hidden.json()["code"] == "STORE_NOT_FOUND", "a worker without access sees no store", hidden)

    def step_m14_new_version(self):
        """A second interview publishes version 2; reading with version 1's id is a version change."""
        old = self.state["version"]
        self.start_interview()
        self.run_interview()
        draft = self.complete_interview()
        _check(draft["versionNumber"] == 2, "the second draft is version 2")
        listing = self.state["worker-a"].call("GET", self.manual_url("/published"), expect=200).json()
        _check(listing["versionId"] == old, "readers keep version 1 while version 2 is drafted")
        published = self.publish(draft)
        _check(published["versionNumber"] == 2, "version 2 is published")
        changed = self.state["worker-a"].call(
            "GET", self.manual_url(f"/published/sections/{self.pos_section(published['content']['sections'])['id']}"),
            params={"expectedVersionId": old}, expect=409)
        _check(changed.json()["code"] == "MANUAL_VERSION_CHANGED", "an old version id is told to reload", changed)
        target = {"type": "MANUAL", "storeId": self.state["store_id"]}
        for name in self.state.get("manual_readers_after_m13", ("worker-a", "worker-d")):
            self.expect_notification(name, "MANUAL_PUBLISHED", target)
        self.state["version"] = published["versionId"]

    # -- lookups -------------------------------------------------------------------------------

    def intent_key(self, intent_id: str) -> str:
        return {i["id"]: i["key"] for i in self.session()["intents"]}[intent_id]

    def intent_id(self, key: str) -> str:
        return {i["key"]: i["id"] for i in self.session()["intents"]}[key]

    def pos_section(self, sections: list[dict]) -> dict:
        """The 포스 section (fake) or, live, the section the photo was hung on / the first common task."""
        if "pos_section" in self.state:
            found = [s for s in sections if s["id"] == self.state["pos_section"]]
            if found:
                return found[0]
        named = [s for s in sections if s["title"] == ai_scenario.POS_SECTION]
        if named:
            return named[0]
        _check(self.live and sections, "a section to work with")
        return sections[0]


def speech(text: str) -> bytes:
    """Korean speech as AAC/MP4 from macOS `say` (voice Yuna) and `afconvert`."""
    if shutil.which("say") is None or shutil.which("afconvert") is None:
        raise RuntimeError("live mode needs macOS say/afconvert to record the spoken answer")
    with tempfile.TemporaryDirectory() as directory:
        aiff, m4a = Path(directory) / "speech.aiff", Path(directory) / "speech.m4a"
        subprocess.run(["say", "-v", "Yuna", "-o", str(aiff), text], check=True)
        subprocess.run(["afconvert", "-f", "mp4f", "-d", "aac", str(aiff), str(m4a)], check=True)
        return m4a.read_bytes()

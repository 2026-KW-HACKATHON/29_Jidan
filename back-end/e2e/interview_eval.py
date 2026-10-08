"""Manual interview evaluation: a scripted owner runs a whole interview against a real server.

    cd back-end
    APP_ENV=local DB_HOST=... DB_NAME=jidan_e2e_test DB_USER=... DB_PASSWORD=... \
        JIDAN_E2E_OPENAI=1 OPENAI_API_KEY=... python -m e2e.interview_eval --ai live

It starts `e2e.serve` (the real app.main under uvicorn, background task runner on), signs up and
approves an owner the way the demo scenario does, then answers every interview question with the
cafe persona (`e2e.owner_persona`: deterministic, no LLM), waits for the six understanding reviews,
corrects one review (revise_structure), completes the interview and waits for the draft.

AI: `--ai live` sends `--ai-live-ops` (default `interview`: judge_sufficiency via Decisions,
generate_question, summarize_intent, revise_structure, compose_draft) to OpenAI, capped by
`--ai-call-limit`; `--ai fake` runs the same flow on the fake with the persona's Jev (no key).
`--voice` records COMMON_TASKS depth 1 with macOS say/Yuna in live mode, uploads it and submits
the READY transcription as VOICE. Fake mode uses silent WAV and a fixed persona transcript.
It also checks committed rows against API re-reads, source action anchors and the 23:00 review
correction. These checks are necessary evidence, not a substitute for a semantic audit.

Report: Markdown at `--report` / `E2E_REPORT_PATH`, default `back-end/.e2e-reports/` (gitignored):
per intent the questions, the owner's answers, Jev's judgement (sufficient, probability, missing
aspects) and the depth reached; the reviews, the correction, the final draft, grounding counts,
latency, live-call counts and token usage per operation. It is written even when a step fails.
Never: keys, prompts, provider output beyond what the public API returns.
"""
import argparse
import hashlib
import json
import os
import re
import sys
import tempfile
import time
from datetime import UTC, datetime
from pathlib import Path

BACK_END = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACK_END))

from app import demo_seed
from e2e import ai_scenario, interview_report, owner_persona
from e2e.demo_scenario import Scenario, server_env
from e2e.mailbox import Mailbox

REPORT_DIR = BACK_END / ".e2e-reports"
DEFAULT_LIVE_OPS = "interview"
# 6 intents probed to depth 5 (72) + 6 summaries + 1 correction + 1 draft = 80: the worst case.
DEFAULT_CALL_LIMIT = owner_persona.worst_case_calls(6)
# Whitelisted settings shown in the report (never the key).
CONFIG_NAMES = ("OPENAI_MODEL", "OPENAI_FALLBACK_MODEL", "OPENAI_JUDGE_BACKEND", "OPENAI_JUDGE_ASPECT_THRESHOLD",
                "OPENAI_JUDGE_NOT_APPLICABLE_THRESHOLD", "OPENAI_REASONING_EFFORT",
                "OPENAI_QUESTION_REASONING_EFFORT", "OPENAI_WRITING_REASONING_EFFORT", "OPENAI_TIMEOUT_SECONDS",
                "OPENAI_WRITING_TIMEOUT_SECONDS", "OPENAI_TRANSCRIBE_MODEL", "OPENAI_TRANSCRIBE_TIMEOUT_SECONDS")
VOICE_INTENT, VOICE_DEPTH = "COMMON_TASKS", 1
VOICE_TEXT = owner_persona.answer(VOICE_INTENT, VOICE_DEPTH)
# Lexical evidence only: accept wording variants; never compare generated prose to a script.
ACTION_ANCHORS = {
    "POS 주문·결제": (("포스", "pos"), ("메뉴", "주문"), ("결제",)),
    "레시피 카드": (("레시피", "조리법", "제조법"), ("카드",)),
    "픽업·주문 번호": (("픽업", "수령"), ("번호",)),
    "테이블 정리·닦기": (("테이블", "탁자"), ("컵",), ("쓰레기",), ("행주", "천"), ("닦",)),
}


def action_anchors(text: str) -> dict[str, bool]:
    normalized = re.sub(r"\s+", "", text).lower()
    return {name: all(any(word in normalized for word in alternatives) for alternatives in groups)
            for name, groups in ACTION_ANCHORS.items()}


def check_voice_actions(content: dict, source: str) -> dict:
    """Presence/quantity checks on COMMON_TASK steps; semantic entailment remains a human audit."""
    instructions = "\n".join(step["instruction"] for section in content.get("sections", [])
                             if section["category"] == "COMMON_TASK" for step in section["steps"])
    anchors = action_anchors(instructions)
    _check(all(anchors.values()), f"voice actions missing from structured steps: {anchors}")
    # The COMMON_TASKS dialogue supplies no quantities. Catch introduced numeric quantities;
    # natural-language additions and paraphrase correctness still require the semantic audit.
    quantities = re.findall(r"\d+(?:[.,]\d+)?\s*(?:그램|밀리리터|리터|초|분|시간|회|번|개|도|g\b|ml\b)",
                            instructions, flags=re.IGNORECASE)
    _check(all(re.sub(r"\s+", "", q) in re.sub(r"\s+", "", source) for q in quantities),
           f"structured COMMON_TASK steps introduced quantities: {quantities}")
    return {"actionAnchors": anchors, "numericQuantities": quantities, "semanticAudit": "PENDING"}


def install_voice_fake(fake):
    """Only the optional evaluation's external fake responses; all real validation still runs."""
    from app.ai.contracts import StructureSnapshot
    from app.ai.fake import structure_to_raw

    def summarize(data):
        key = data["intent"]["key"]
        # The real provider removes dialogue when frozen evidence is available. Build fake
        # steps from those actual submitted utterance chunks, including the actual STT text.
        texts = [e["text"] for e in data.get("evidence", []) if e["intent_key"] == key]
        if not texts:
            texts = [t["answer"] for t in data.get("dialogue", [])]
        source = "\n".join(texts)
        _check(bool(source.strip()), f"the persona fake summary requires submitted {key} evidence")
        shifts, sections, missing = [], [], []
        if key == "WORK_STRUCTURE":
            _check(all(word in source for word in ("오픈조", "7시", "마감조", "30분")),
                   "the fake work shifts require the submitted persona's concrete times")
            shifts = [ai_scenario._shift("new-1", "오픈조", "07:00", "15:00"),
                      ai_scenario._shift("new-2", "마감조", "15:00", "22:30")]
        elif key == "RULES":
            # '해당 없음' must not become the demo scenario's invented apron rule.
            _check("해당 없음" in source, "the persona explicitly supplied no store rules")
        elif key == "EXCEPTIONS" and data["needs_detail"]:
            missing = [{"target": "MANUAL", "target_ref": None, "field": "sections",
                        "description": "돌발 상황의 구체적인 대응 방법을 확인해야 해요."}]
        elif key == "SHIFT_TASKS":
            for index, (name, starts) in enumerate((("오픈조", ("오픈조는 7시", "첫 샷")),
                                                    ("마감조", ("마감조는 밤", "정산 영수증")))):
                shift = next(s for s in data["available_shifts"] if s["name"] == name)
                steps = [t for t in texts if t.startswith(starts)]
                _check(bool(steps), f"{name}: the fake uses actual shift-task source chunks")
                sections.append(ai_scenario._section(f"new-{index * 10 + 1}", f"{name} 업무", steps,
                                                      category="SHIFT_TASK", shift_ref=shift["id"]))
        else:
            concrete = [t for t in texts if t != owner_persona.answer(key, 0)]
            category = "EQUIPMENT" if key == "EQUIPMENT" else "COMMON_TASK"
            sections = [ai_scenario._section("new-1", key, concrete, category=category)]
        return {"summary": source, "structure": {"shifts": shifts, "sections": sections,
                                                 "missing_information": missing}}

    def revise(data):
        if data["instruction"] != owner_persona.REVIEW_CORRECTION:
            return ai_scenario.revise(data)
        raw = structure_to_raw(StructureSnapshot.model_validate(data["current"]))
        for shift in raw["shifts"]:
            if "마감" in shift["name"]:
                shift["end_time"] = "23:00"
        return {"outcome": "APPLIED", "summary": data.get("summary"), "structure": raw}

    return fake.on("transcribe", lambda _request: VOICE_TEXT).on("summarize_intent", summarize).on(
        "revise_structure", revise)


def _check(condition, message, response=None):
    from e2e.demo_scenario import check

    check(condition, message, response)


class InterviewEval(Scenario):
    """Owner sign-up and approval from the demo scenario, then the persona's interview."""

    EVAL_STEPS = (
        ("health", "step_health"),
        ("owner sign-up", "step_owner_signup"),
        ("admin approval", "step_admin_approval"),
        ("E1 interview start", "step_e1_start"),
        ("E2 persona answers every question", "step_e2_answers"),
        ("E3 understanding reviews", "step_e3_reviews"),
        ("E4 review correction", "step_e4_correction"),
        ("E5 draft generation", "step_e5_draft"),
    )

    def __init__(self, base_url: str, origin: str, admin_password: str, *, ai: str = "fake",
                 skip_depth_five: bool = False, fake_kakao: bool = True, voice: bool = False) -> None:
        super().__init__(base_url, origin, admin_password, fake_kakao, None, realtime_expiry=False, ai=ai)
        self.skip_depth_five = skip_depth_five
        self.voice = voice
        self.audio_path: Path | None = None
        self.media_root: str | None = None
        self.read_engine = None  # Optional file-backed test DB; CLI uses its own process's engine.
        self.record: dict = {"run": self.run, "mode": ai, "skip_depth_five": skip_depth_five, "intents": [],
                             "turns": [], "reviews": {}, "correction": None, "draft": None,
                             "voice_enabled": voice, "voice": None, "persistence": {}}

    def steps(self) -> list[tuple[str, str]]:
        return list(self.EVAL_STEPS)

    # -- steps ---------------------------------------------------------------------------------

    def step_e1_start(self):
        started = self.start_interview()
        _check(started["status"] == "IN_PROGRESS" and len(started["intents"]) >= 1, "the interview is in progress")
        state = self.wait_session(lambda s: s["phase"] in ("COLLECTING", "ERROR"), "the first question")
        _check(state["phase"] == "COLLECTING", f"the first question is asked: {state.get('error')}")
        self.record["intents"] = [{"id": i["id"], "key": i["key"]} for i in state["intents"]]
        [question] = state["questions"]
        _check((question["kind"], question["depth"]) == ("BASE", 0), "a BASE question at depth 0 opens")

    def step_e2_answers(self):
        """Answer the one open question at a time with the persona until READY_TO_GENERATE."""
        keys = {i["id"]: i["key"] for i in self.record["intents"]}
        order = [i["key"] for i in self.record["intents"]]
        limit = len(keys) * (owner_persona.MAX_DEPTH + 1)
        for _ in range(limit + 1):
            state = self.wait_session(lambda s: s["phase"] in ("COLLECTING", "READY_TO_GENERATE", "ERROR"),
                                      "the interview waits for an answer")
            _check(state["phase"] != "ERROR", f"interview error: {state.get('error')}")
            if state["phase"] == "READY_TO_GENERATE":
                break
            open_questions = [q for q in state["questions"] if not q["answered"]]
            _check(len(open_questions) == 1, f"exactly one open question: {len(open_questions)}")
            question = open_questions[0]
            key = keys[question["intentId"]]
            self._check_progress(key, question, order)
            text = owner_persona.answer(key, question["depth"], skip_depth_five=self.skip_depth_five)
            entry = {
                "intent": key, "kind": question["kind"], "depth": question["depth"], "question": question["text"],
                "questionId": question["id"], "sessionId": self.state["interview"],
                "guidance": question.get("guidance"), "answer": text, "inputMethod": "TEXT"}
            answer_input = {"method": "TEXT", "text": text}
            if self.voice and (key, question["depth"]) == (VOICE_INTENT, VOICE_DEPTH):
                transcript = self._record_voice(question)
                entry.update(answer=transcript["text"], inputMethod="VOICE", transcriptionId=transcript["id"])
                answer_input = {"method": "VOICE", "transcriptionId": transcript["id"]}
            self.record["turns"].append(entry)
            self.owner_write("POST", f"/interviews/{self.state['interview']}/answers", {
                "expectedRevision": state["revision"], "questionId": question["id"],
                "input": answer_input}, 202)
            if entry["inputMethod"] == "VOICE":
                self._verify_voice_answer(question)
        else:
            _check(False, f"the interview asked more than {limit} questions")
        asked = {t["intent"] for t in self.record["turns"]}
        _check(asked == set(order), f"every intent was asked: {sorted(set(order) - asked)}")
        if self.voice:
            _check(self.record["voice"] is not None,
                   "COMMON_TASKS depth 1 was not asked; this run has no voice verification")
        if not self.live:  # the fake Jev agrees with the persona: exact depths
            reached = {k: max(t["depth"] for t in self.record["turns"] if t["intent"] == k) for k in order}
            expected = {k: owner_persona.expected_depth(k, skip_depth_five=self.skip_depth_five) for k in order}
            _check(reached == expected, f"depths {reached} != persona {expected}")

    def _check_progress(self, key: str, question: dict, order: list[str]) -> None:
        """Structure every run must keep: intents in order, depth 0 BASE then PROBEs +1 up to 5."""
        turns = self.record["turns"]
        previous = turns[-1] if turns else None
        if previous is None or previous["intent"] != key:
            _check((question["kind"], question["depth"]) == ("BASE", 0), f"{key} opens with a BASE question")
            done = [t["intent"] for t in turns]
            _check(key not in done, f"{key} is not asked again after it ended")
            _check(order.index(key) == len(dict.fromkeys(done)), f"{key} is asked in question set order")
        else:
            _check(question["kind"] == "PROBE" and question["depth"] == previous["depth"] + 1,
                   f"{key}: a PROBE one level deeper ({question['kind']} {question['depth']})")
        _check(question["depth"] <= owner_persona.MAX_DEPTH, f"{key}: depth stays within 5")

    def step_e3_reviews(self):
        sid = self.state["interview"]
        count = len(self.record["intents"])
        listing = self.poll(self.state["owner"], self.manual_url(f"/interviews/{sid}/reviews"),
                            lambda r: len(r["items"]) == count and all(i["status"] != "PROCESSING"
                                                                      for i in r["items"]),
                            "every review summarized")
        keys = {i["id"]: i["key"] for i in self.record["intents"]}
        for item in listing["items"]:
            self.record["reviews"][keys[item["intentId"]]] = {
                "intentId": item["intentId"], "status": item["status"], "revision": item["revision"],
                "content": item.get("content"), "error": item.get("error")}
        self.state["review_listing"] = listing
        bad = {k: r["error"] for k, r in self.record["reviews"].items() if r["status"] != "READY"}
        _check(not bad, f"every review READY: {bad}")
        for key, review in self.record["reviews"].items():
            content = review["content"]
            reached = max(t["depth"] for t in self.record["turns"] if t["intent"] == key)
            judged = [e for e in self._trace() if e.get("op") == "judge_sufficiency" and e.get("intent") == key
                      and e.get("depth") == reached and e.get("outcome") == "ok"]
            if judged:  # needsDetail is exactly "Jev still found it short at depth 5"
                expected = reached == owner_persona.MAX_DEPTH and not judged[-1]["sufficient"]
                _check(content["needsDetail"] is expected, f"{key} needsDetail={content['needsDetail']}")
        if not self.live and not self.skip_depth_five:
            _check(self.record["reviews"][owner_persona.NEEDS_DETAIL]["content"]["needsDetail"] is True,
                   "the vague intent ends NEEDS_DETAIL")
        if self.voice:
            for key, review in self.record["reviews"].items():
                self._verify_review(key, review)
            source = "\n".join(t["answer"] for t in self.record["turns"] if t["intent"] == VOICE_INTENT)
            observed = check_voice_actions(self.record["reviews"][VOICE_INTENT]["content"], source)
            self.record["persistence"]["voiceReviewAnchors"] = observed

    def step_e4_correction(self):
        """The owner corrects WORK_STRUCTURE's review; the review comes back READY, newer."""
        key = "WORK_STRUCTURE"
        review = self.record["reviews"].get(key)
        _check(review is not None, "a WORK_STRUCTURE review exists")
        url = f"/interviews/{self.state['interview']}/intents/{review['intentId']}/review"
        self.record["correction"] = {"intent": key, "instruction": owner_persona.REVIEW_CORRECTION,
                                     "before": (review["content"] or {}).get("shifts") or [], "status": "PROCESSING"}
        self.owner_write("POST", f"{url}/corrections", {
            "expectedRevision": review["revision"],
            "input": {"method": "TEXT", "text": owner_persona.REVIEW_CORRECTION}}, 202)
        corrected = self.poll(self.state["owner"], self.manual_url(url), lambda r: r["status"] in ("READY", "ERROR"),
                              "review correction")
        self.record["correction"].update(status=corrected["status"], after=(corrected.get("content") or {}).get(
            "shifts") or [], error=corrected.get("error"))
        _check(corrected["status"] == "READY" and corrected["revision"] > review["revision"],
               f"the correction applied: {corrected.get('error')}")
        self.record["reviews"][key].update(revision=corrected["revision"], content=corrected["content"])
        if self.voice:
            self._verify_review(key, self.record["reviews"][key])
            self._check_closing_shift(corrected["content"]["shifts"])

    def step_e5_draft(self):
        draft = self.complete_interview()
        self.record["draft"] = {k: draft.get(k) for k in ("generationStatus", "revision", "content", "issues",
                                                         "versionId", "error")}
        _check(draft["generationStatus"] == "READY", f"the draft is READY: {draft.get('error')}")
        content = draft["content"]
        _check(bool(content["shifts"] or content["sections"]), "the draft has shifts or sections")
        steps = sum(len(s["steps"]) for s in content["sections"])
        _check(steps >= 1, "the draft has at least one step")
        shift_ids = {s["id"] for s in content["shifts"]}
        _check(all(s["shiftId"] is None or s["shiftId"] in shift_ids for s in content["sections"]),
               "every section's shift is one of the draft's shifts")
        needs = [k for k, r in self.record["reviews"].items() if (r["content"] or {}).get("needsDetail")]
        if needs:
            ids = {i["key"]: i["id"] for i in self.record["intents"]}
            _check(all(any(i.get("intentId") == ids[k] for i in draft["issues"]) for k in needs),
                   f"every NEEDS_DETAIL intent is a draft issue: {needs}")
        if self.voice:
            self._verify_draft(draft)
            source = "\n".join(t["answer"] for t in self.record["turns"] if t["intent"] == VOICE_INTENT)
            self.record["persistence"]["voiceDraftAnchors"] = check_voice_actions(content, source)
            self._check_closing_shift(content["shifts"])
            question = self.record["voice"]["question"]
            self._verify_voice_answer(question)  # Immutable transcription/answer still match after generation.

    # -- independent committed reads (opt-in voice verification) --------------------------------

    def _db(self):
        from sqlalchemy.orm import Session

        from app.db import get_engine

        # This runs in the driver, never the server's request transaction/identity map.
        return Session(self.read_engine if self.read_engine is not None else get_engine())

    def _record_voice(self, question: dict) -> dict:
        from e2e.manual_scenario import speech
        from tests import media_samples

        data, suffix, mime = ((speech(VOICE_TEXT), ".m4a", "audio/mp4") if self.live else
                              (media_samples.wav_seconds(3), ".wav", "audio/wav"))
        _check(self.audio_path is not None, "the voice recording has an artifact destination")
        path = self.audio_path.with_suffix(suffix).resolve()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        self.record["voice"] = {"speechText": VOICE_TEXT, "audioFile": str(path),
                                "audioSha256": hashlib.sha256(data).hexdigest(), "audioBytes": len(data),
                                "audioSource": "macOS say/Yuna" if self.live else "silent WAV + fixed fake STT",
                                "question": dict(question), "sessionId": self.state["interview"]}
        uploaded = self.upload_manual(data, "INTERVIEW_AUDIO", path.name, mime).json()
        self.record["voice"]["media"] = uploaded
        _check((uploaded["storeId"], uploaded["purpose"], uploaded["mimeType"], uploaded["sizeBytes"]) ==
               (self.state["store_id"], "INTERVIEW_AUDIO", mime, len(data)), "the recording upload matches")
        started = self.owner_write("POST", "/transcriptions", {"mediaId": uploaded["id"]}, 202).json()
        ready = self.poll(self.state["owner"], self.manual_url(f"/transcriptions/{started['id']}"),
                          lambda t: t["status"] in ("READY", "ERROR"), "voice transcription")
        self.record["voice"]["transcription"] = ready
        _check(ready["status"] == "READY" and isinstance(ready.get("text"), str) and ready["text"].strip(),
               f"voice transcription is nonempty READY: {ready.get('error')}")
        _check(ready["id"] == started["id"] and ready["mediaId"] == uploaded["id"],
               "the READY transcription belongs to the uploaded recording")
        anchors = action_anchors(ready["text"])
        self.record["voice"]["transcriptAnchors"] = anchors
        _check(all(anchors.values()), f"transcription lost source actions: {anchors}")
        reused = self.owner_write("POST", "/transcriptions", {"mediaId": uploaded["id"]}, 200).json()
        _check(reused == ready, "a new request reuses the immutable READY transcription")
        return ready

    def _verify_voice_answer(self, question: dict):
        from sqlalchemy import select

        from app.db.models import (
            InterviewSession,
            InterviewTurn,
            ManualMedia,
            MediaTranscription,
            Store,
        )

        voice = self.record["voice"]
        transcript = voice["transcription"]
        reread = self.state["owner"].call(
            "GET", self.manual_url(f"/transcriptions/{transcript['id']}"), expect=200).json()
        _check(reread == transcript, "the transcription API re-read preserves the READY result")
        owner_id = self.state["owner"].call("GET", "/api/auth/session", expect=200).json()["user"]["id"]
        with self._db() as db:
            session = db.get(InterviewSession, self.state["interview"])
            store = db.get(Store, self.state["store_id"])
            media = db.get(ManualMedia, voice["media"]["id"])
            stored = db.get(MediaTranscription, transcript["id"])
            _check(session is not None and store is not None and media is not None and stored is not None,
                   "voice resources are committed and independently readable")
            _check(store.owner_id == session.owner_id == media.uploaded_by_owner_id == owner_id,
                   "the media and interview belong to the authenticated store owner")
            _check(stored.store_id == media.store_id == store.id and stored.manual_media_id == media.id
                   and stored.qa_media_id is None and media.kind == "AUDIO" and media.deleted_at is None
                   and media.mime_type == voice["media"]["mimeType"] and media.byte_size == voice["audioBytes"],
                   "the committed transcription/media/store associations match the upload")
            _check(stored.status == "READY" and stored.text == transcript["text"] and stored.completed_at is not None
                   and stored.error_code is None, "the committed READY transcription matches the API text")
            from app.media.storage import LocalMediaStorage

            _check(self.media_root is not None, "the driver knows the isolated server media directory")
            saved_audio = LocalMediaStorage(self.media_root).read(media.object_key)
            _check(hashlib.sha256(saved_audio).hexdigest() == voice["audioSha256"],
                   "committed media bytes match the recorded audio artifact")
            source_question = db.get(InterviewTurn, question["id"])
            _check(source_question is not None and source_question.session_id == session.id
                   and source_question.intent_id == question["intentId"] and source_question.depth == VOICE_DEPTH
                   and source_question.content == question["text"] and source_question.turn_kind == "QUESTION",
                   "the submitted question is the committed depth 1 question in this session")
            answers = list(db.scalars(select(InterviewTurn).where(
                InterviewTurn.reply_to_question_turn_id == question["id"])))
            _check(len(answers) == 1, "exactly one committed answer replies to the voice question")
            answer = answers[0]
            _check(answer.session_id == session.id and answer.intent_id == question["intentId"]
                   and answer.depth == VOICE_DEPTH and answer.speaker == "OWNER" and answer.turn_kind == "ANSWER"
                   and answer.input_method == "VOICE" and answer.transcription_id == stored.id
                   and answer.content == stored.text, "the committed VOICE answer copies its immutable transcription")
            snapshot = {"answerId": answer.id, "sessionId": session.id, "questionId": question["id"],
                        "intentId": answer.intent_id, "depth": answer.depth, "inputMethod": answer.input_method,
                        "transcriptionId": stored.id, "mediaId": media.id, "storeId": store.id,
                        "ownerId": owner_id, "transcriptionStatus": stored.status, "content": answer.content}
            dialogue_db = [(t.id, t.turn_no, t.reply_to_question_turn_id, t.input_method, t.content)
                           for t in db.scalars(select(InterviewTurn).where(InterviewTurn.session_id == session.id)
                                               .order_by(InterviewTurn.turn_no))]
            completed = session.status == "COMPLETED"
        turns = self.state["owner"].call("GET", self.manual_url(f"/interviews/{self.state['interview']}/turns"),
                                          params={"size": 100}, expect=200).json()["items"]
        dialogue_api = [(t["id"], t["sequence"], t["replyToQuestionId"], t["inputMethod"], t["content"])
                        for t in turns]
        # A background task may append the next question between these independent reads.
        # All prior turns are immutable; after COMPLETED the entire dialogue must match.
        _check(sorted(dialogue_api) == sorted(dialogue_db) if completed else
               all(t in dialogue_api for t in dialogue_db),
               "source dialogue API re-read preserves independently committed turns")
        self.record["sourceDialogue"] = turns
        self.record["sourceDialogueComplete"] = completed
        public = [t for t in turns if t["id"] == snapshot["answerId"]]
        _check(len(public) == 1 and public[0]["inputMethod"] == "VOICE"
               and public[0]["replyToQuestionId"] == question["id"] and public[0]["content"] == transcript["text"]
               and public[0]["intentId"] == question["intentId"] and public[0]["depth"] == VOICE_DEPTH,
               "the turns API re-read matches the committed VOICE answer")
        voice["answer"] = public[0]
        self.record["persistence"]["voiceAnswer"] = snapshot

    def _verify_review(self, key: str, review: dict):
        from app.db.models import InterviewIntentReview

        with self._db() as db:
            stored = db.get(InterviewIntentReview, (self.state["interview"], review["intentId"]))
            _check(stored is not None and stored.status == "READY" and stored.revision == review["revision"]
                   and stored.ready_content == review["content"], f"{key}: committed READY review matches the API")
        reread = self.state["owner"].call("GET", self.manual_url(
            f"/interviews/{self.state['interview']}/intents/{review['intentId']}/review"), expect=200).json()
        _check(reread["status"] == "READY" and reread["revision"] == review["revision"]
               and reread["content"] == review["content"], f"{key}: review API re-read matches committed content")
        self.record["persistence"].setdefault("reviews", {})[key] = {
            "intentId": review["intentId"], "revision": review["revision"], "status": "READY", "apiMatchesDb": True}

    @staticmethod
    def _check_closing_shift(shifts: list[dict]):
        closing = [s for s in shifts if "마감" in s["name"]]
        _check(len(closing) == 1 and closing[0]["endTime"] == "23:00" and closing[0]["endsNextDay"] is False,
               "the owner's reviewed closing-shift correction is retained at 23:00")

    def _verify_draft(self, draft: dict):
        from sqlalchemy import select

        from app.db.models import (
            InterviewSession,
            ManualSection,
            ManualShift,
            ManualStep,
            ManualVersion,
            StoreManual,
        )
        from app.manual_content import content_body

        with self._db() as db:
            version = db.get(ManualVersion, draft["versionId"])
            session = db.get(InterviewSession, self.state["interview"])
            _check(version is not None and session is not None and version.id == session.manual_version_id
                   and version.status == "DRAFT" and version.generation_status == "READY"
                   and version.revision == draft["revision"] and session.status == "COMPLETED"
                   and session.completed_at is not None, "the READY draft and completed interview are committed")
            manual = db.get(StoreManual, version.manual_id)
            _check(manual is not None and manual.store_id == self.state["store_id"], "the draft belongs to this store")
            # Explicit normalized-row comparison also detects missing/extra rows and moved IDs.
            shifts = list(db.scalars(select(ManualShift).where(ManualShift.version_id == version.id)
                                     .order_by(ManualShift.sort_order)))
            sections = list(db.scalars(select(ManualSection).where(ManualSection.version_id == version.id)
                                       .order_by(ManualSection.sort_order)))
            _check([s.id for s in shifts] == [s["id"] for s in draft["content"]["shifts"]]
                   and [s.id for s in sections] == [s["id"] for s in draft["content"]["sections"]],
                   "normalized draft shift/section IDs and order match the API")
            _check([{"id": s.id, "name": s.name,
                     "startTime": s.start_time.strftime("%H:%M") if s.start_time is not None else None,
                     "endTime": s.end_time.strftime("%H:%M") if s.end_time is not None else None,
                     "endsNextDay": s.ends_next_day} for s in shifts] == draft["content"]["shifts"],
                   "normalized shift names and reviewed times match the API")
            step_count = 0
            for row, section in zip(sections, draft["content"]["sections"], strict=True):
                _check((row.category, row.title, row.shift_id) ==
                       (section["category"], section["title"], section["shiftId"]), "stored section fields match")
                steps = list(db.scalars(select(ManualStep).where(ManualStep.section_id == row.id)
                                        .order_by(ManualStep.sort_order)))
                _check([(s.id, s.instruction, s.checklist_item) for s in steps] ==
                       [(s["id"], s["instruction"], s["checklistItem"]) for s in section["steps"]],
                       "normalized draft steps, text and checklist fields match the API")
                step_count += len(steps)
            persisted = content_body(db, version.id)
            _check(persisted == draft["content"], "all committed draft content matches the API")
            frozen = version.generation_input_snapshot
            _check(isinstance(frozen, dict), "draft generation has a committed input snapshot")
            for key, review in self.record["reviews"].items():
                selected = [r for r in frozen["reviews"] if r["intentId"] == review["intentId"]]
                _check(len(selected) == 1 and selected[0]["revision"] == review["revision"]
                       and selected[0]["content"] == review["content"], f"{key}: generation froze the reviewed content")
            evidence = frozen.get("evidence") or []
            answer_id = self.record["persistence"]["voiceAnswer"]["answerId"]
            voice_chunks = [e for e in evidence if e["id"].startswith(f"{answer_id}#")]
            source = re.sub(r"\s+", "", self.record["voice"]["transcription"]["text"])
            _check(voice_chunks and all(re.sub(r"\s+", "", e["text"]) in source for e in voice_chunks)
                   and all(action_anchors(" ".join(e["text"] for e in voice_chunks)).values()),
                   "draft generation's committed evidence keeps the actual voice turn's action chunks")
            snapshot = {"versionId": version.id, "generationStatus": version.generation_status,
                        "sessionStatus": session.status, "revision": version.revision,
                        "shiftRows": len(shifts), "sectionRows": len(sections), "stepRows": step_count,
                        "content": persisted, "generationInput": frozen, "apiMatchesDb": True}
        reread = self.draft()
        _check(reread["versionId"] == draft["versionId"] and reread["revision"] == draft["revision"]
               and reread["content"] == persisted and reread["issues"] == draft["issues"],
               "draft API re-read preserves committed content and issues")
        self.record["persistence"]["draft"] = snapshot

    # -- trace ---------------------------------------------------------------------------------

    trace_path: str | None = None

    def _trace(self) -> list[dict]:
        return ai_scenario.read_trace(self.trace_path) if self.trace_path else []


def default_report_path(run: str) -> Path:
    return REPORT_DIR / f"interview-eval-{datetime.now(UTC):%Y%m%dT%H%M%SZ}-{run}.md"


def price_from_env() -> tuple[float, float] | None:
    """`E2E_AI_PRICE_PER_1M=<input USD>,<output USD>` per million tokens (optional, for the report)."""
    raw = os.getenv("E2E_AI_PRICE_PER_1M", "").strip()
    if not raw:
        return None
    try:
        values = tuple(float(part) for part in raw.split(","))
    except ValueError:
        return None
    return values if len(values) == 2 and all(v >= 0 for v in values) else None


def build_record(scenario: InterviewEval, *, trace: list[dict], calls: dict, live_ops: list[str], call_limit: int,
                 started_at: str, wall_seconds: float) -> dict:
    record = dict(scenario.record)
    record.update(
        trace=trace, calls=calls, live_ops=live_ops, call_limit=call_limit, started_at=started_at,
        wall_seconds=wall_seconds, results=list(scenario.results), price_per_1m=price_from_env(),
        config={name: os.environ[name] for name in CONFIG_NAMES if os.getenv(name, "").strip()},
    )
    return record


def load_env_file(path: str, *, key_only: bool = False) -> list[str]:
    """Put the `OPENAI_*` assignments of a dotenv-style file (KEY=VALUE, optional `export`,
    quotes, `#` comments) into this process's environment, which the server inherits. Other
    names are ignored and nothing is printed; returns the names that were set. With key_only,
    load only OPENAI_API_KEY (OPENAI_KEY is an alias), preserving the executor's settings."""
    loaded, assignments = [], {}
    for line in Path(path).expanduser().read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        name, value = line.removeprefix("export ").split("=", 1)
        name, value = name.strip(), value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "'\"":
            value = value[1:-1]
        if name.startswith("OPENAI_") and name.replace("_", "").isalnum() and value:
            assignments[name] = value
    # Some local key files call the credential OPENAI_KEY. Keep one canonical variable and
    # prefer an explicitly named API key if both occur, regardless of line order.
    if "OPENAI_API_KEY" not in assignments and "OPENAI_KEY" in assignments:
        assignments["OPENAI_API_KEY"] = assignments["OPENAI_KEY"]
    assignments.pop("OPENAI_KEY", None)
    for name, value in assignments.items():
        if not key_only or name == "OPENAI_API_KEY":
            os.environ[name] = value
            loaded.append(name)
    return loaded


def ensure_question_set() -> None:
    """Question set v1 is seeded by migration 0040; a test database whose tables were emptied
    (the MySQL test fixtures do that) gets the same rows back, else the interview cannot start."""
    from app.db import session_scope
    from tests.interview_factories import ensure_question_set as ensure

    with session_scope() as db:
        ensure(db)


def start_server(port: int, origin: str, smtp_port: int, env_extra: dict[str, str]):
    import subprocess

    import httpx

    extra, password = server_env(origin, smtp_port, ai=env_extra.pop("E2E_AI"),
                                 media_root=env_extra.pop("MEDIA_ROOT"))
    process = subprocess.Popen([sys.executable, "-m", "e2e.serve", "--port", str(port)], cwd=BACK_END,
                               env={**os.environ, **extra, **env_extra})
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        try:
            if httpx.get(f"http://127.0.0.1:{port}/api/health", timeout=1).status_code == 200:
                return process, password
        except httpx.HTTPError:
            pass
        if process.poll() is not None:
            raise SystemExit("the server exited during start-up")
        time.sleep(0.3)
    process.terminate()
    raise SystemExit("the server did not become healthy in 30 seconds")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--ai", choices=("fake", "live"), default="fake")
    parser.add_argument("--ai-live-ops", default=os.getenv("E2E_AI_LIVE_OPS") or DEFAULT_LIVE_OPS,
                        help="operations or presets (default, interview, all) sent to OpenAI")
    parser.add_argument("--ai-call-limit", type=int, default=None)
    parser.add_argument("--skip-depth5", action="store_true",
                        default=os.getenv("E2E_SKIP_DEPTH5") == "1",
                        help="the vague intent turns specific at depth 1 (about 8 fewer live calls)")
    parser.add_argument("--voice", action="store_true",
                        help="verify COMMON_TASKS depth 1 by VOICE, committed DB rows and API re-reads")
    parser.add_argument("--report", default=os.getenv("E2E_REPORT_PATH") or None)
    parser.add_argument("--port", type=int, default=8766)
    parser.add_argument("--origin", default="http://localhost:5173")
    parser.add_argument("--env-file", default=None,
                        help="dotenv OPENAI_* settings; --voice reads only OPENAI_API_KEY/OPENAI_KEY, silently")
    args = parser.parse_args(argv)
    if args.ai_call_limit is None:
        args.ai_call_limit = int(os.getenv("E2E_AI_CALL_LIMIT") or (DEFAULT_CALL_LIMIT + int(args.voice)))
    if args.ai_call_limit < 1:
        print("--ai-call-limit must be positive", file=sys.stderr)
        return 2
    if args.env_file:
        try:
            load_env_file(args.env_file, key_only=args.voice)
        except OSError as error:
            print(f"--env-file cannot be read: {type(error).__name__}", file=sys.stderr)
            return 2
    if args.ai == "live" and (os.getenv("JIDAN_E2E_OPENAI") != "1" or not os.getenv("OPENAI_API_KEY", "").strip()):
        print("--ai live needs JIDAN_E2E_OPENAI=1 and OPENAI_API_KEY (it is billed)", file=sys.stderr)
        return 2
    try:
        demo_seed.check_target()
    except demo_seed.UnsafeTarget as error:
        print(f"refused: {error}", file=sys.stderr)
        return 2
    live_ops = list(ai_scenario.resolve_live_ops(args.ai_live_ops)) if args.ai == "live" else []
    unknown = set(live_ops) - set(ai_scenario.OPERATIONS)
    if unknown:
        print(f"unknown AI operations: {sorted(unknown)}", file=sys.stderr)
        return 2
    if args.voice and args.ai == "live":
        if not set(ai_scenario.INTERVIEW_OPS).issubset(live_ops):
            print("--voice --ai live requires every interview operation to be live", file=sys.stderr)
            return 2
        if "transcribe" not in live_ops:
            live_ops.append("transcribe")
    ensure_question_set()
    mailbox = Mailbox().start()
    workdir = tempfile.TemporaryDirectory(prefix="jidan-interview-eval-")
    counter, trace = os.path.join(workdir.name, "ai-calls.json"), os.path.join(workdir.name, "ai-trace.jsonl")
    env = {"E2E_AI": args.ai, "MEDIA_ROOT": os.path.join(workdir.name, "media"), "E2E_AI_SCRIPT": "persona",
           "E2E_AI_TRACE_FILE": trace, "E2E_INTERVIEW_EVAL_VOICE": "1" if args.voice else "0"}
    media_root = env["MEDIA_ROOT"]
    if args.ai == "live":
        env.update(E2E_AI_LIVE_OPS=",".join(live_ops), E2E_AI_CALL_LIMIT=str(args.ai_call_limit),
                   E2E_AI_COUNTER_FILE=counter)
        expected = owner_persona.expected_calls(list(owner_persona.ANSWERS), skip_depth_five=args.skip_depth5)
        if args.voice:
            expected["transcribe"] = 1
        print(f"live AI: {', '.join(live_ops)}; about {sum(expected.values())} calls if Jev agrees with the "
              f"persona, at most {args.ai_call_limit}")
    started_at, started = datetime.now(UTC).isoformat(timespec="seconds"), time.monotonic()
    process = None
    scenario = None
    try:
        process, password = start_server(args.port, args.origin, mailbox.port, env)
        scenario = InterviewEval(f"http://127.0.0.1:{args.port}", args.origin, password, ai=args.ai,
                                 skip_depth_five=args.skip_depth5,
                                 fake_kakao=not os.getenv("KAKAO_REST_API_KEY", "").strip(), voice=args.voice)
        report_path = Path(args.report) if args.report else default_report_path(scenario.run)
        scenario.audio_path = report_path.with_suffix(".voice.m4a")
        scenario.media_root = media_root
        scenario.trace_path = trace
        ok = scenario.execute()
        scenario.report()
        return 0 if ok else 1
    finally:
        if process is not None:
            process.terminate()
            process.wait(timeout=10)
        mailbox.stop()
        if scenario is not None:
            calls = json.loads(Path(counter).read_text()) if os.path.exists(counter) else {}
            record = build_record(scenario, trace=ai_scenario.read_trace(trace), calls=calls, live_ops=live_ops,
                                  call_limit=args.ai_call_limit, started_at=started_at,
                                  wall_seconds=time.monotonic() - started)
            path = report_path
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(interview_report.render(record), encoding="utf-8")
            if args.voice:
                path.with_suffix(".json").write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
            live = [e for e in record["trace"] if e.get("live")]
            print(f"  live AI calls: {len(live)} (limit {args.ai_call_limit}); report: {path}")
        workdir.cleanup()


if __name__ == "__main__":
    raise SystemExit(main())

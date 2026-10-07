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
Live assertions are structural only (phases progress, every review READY, a READY draft, every
response valid against OpenAPI): the model's wording is what the report is for.

Report: Markdown at `--report` / `E2E_REPORT_PATH`, default `back-end/.e2e-reports/` (gitignored):
per intent the questions, the owner's answers, Jev's judgement (sufficient, probability, missing
aspects) and the depth reached; the reviews, the correction, the final draft, grounding counts,
latency, live-call counts and token usage per operation. It is written even when a step fails.
Never: keys, prompts, provider output beyond what the public API returns.
"""
import argparse
import os
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
                "OPENAI_WRITING_TIMEOUT_SECONDS")


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
                 skip_depth_five: bool = False, fake_kakao: bool = True) -> None:
        super().__init__(base_url, origin, admin_password, fake_kakao, None, realtime_expiry=False, ai=ai)
        self.skip_depth_five = skip_depth_five
        self.record: dict = {"run": self.run, "mode": ai, "skip_depth_five": skip_depth_five, "intents": [],
                             "turns": [], "reviews": {}, "correction": None, "draft": None}

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
            self.record["turns"].append({
                "intent": key, "kind": question["kind"], "depth": question["depth"], "question": question["text"],
                "guidance": question.get("guidance"), "answer": text})
            self.owner_write("POST", f"/interviews/{self.state['interview']}/answers", {
                "expectedRevision": state["revision"], "questionId": question["id"],
                "input": {"method": "TEXT", "text": text}}, 202)
        else:
            _check(False, f"the interview asked more than {limit} questions")
        asked = {t["intent"] for t in self.record["turns"]}
        _check(asked == set(order), f"every intent was asked: {sorted(set(order) - asked)}")
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


def load_env_file(path: str) -> list[str]:
    """Put the `OPENAI_*` assignments of a dotenv-style file (KEY=VALUE, optional `export`,
    quotes, `#` comments) into this process's environment, which the server inherits. Other
    names are ignored and nothing is printed; returns the names that were set."""
    loaded = []
    for line in Path(path).expanduser().read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        name, value = line.removeprefix("export ").split("=", 1)
        name, value = name.strip(), value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "'\"":
            value = value[1:-1]
        if name.startswith("OPENAI_") and name.replace("_", "").isalnum() and value:
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
    parser.add_argument("--ai-call-limit", type=int,
                        default=int(os.getenv("E2E_AI_CALL_LIMIT") or DEFAULT_CALL_LIMIT))
    parser.add_argument("--skip-depth5", action="store_true",
                        default=os.getenv("E2E_SKIP_DEPTH5") == "1",
                        help="the vague intent turns specific at depth 1 (about 8 fewer live calls)")
    parser.add_argument("--report", default=os.getenv("E2E_REPORT_PATH") or None)
    parser.add_argument("--port", type=int, default=8766)
    parser.add_argument("--origin", default="http://localhost:5173")
    parser.add_argument("--env-file", default=None,
                        help="dotenv file whose OPENAI_* lines (the key) are loaded; nothing is printed")
    args = parser.parse_args(argv)
    if args.env_file:
        try:
            load_env_file(args.env_file)
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
    ensure_question_set()
    mailbox = Mailbox().start()
    workdir = tempfile.TemporaryDirectory(prefix="jidan-interview-eval-")
    counter, trace = os.path.join(workdir.name, "ai-calls.json"), os.path.join(workdir.name, "ai-trace.jsonl")
    env = {"E2E_AI": args.ai, "MEDIA_ROOT": os.path.join(workdir.name, "media"), "E2E_AI_SCRIPT": "persona",
           "E2E_AI_TRACE_FILE": trace}
    if args.ai == "live":
        env.update(E2E_AI_LIVE_OPS=",".join(live_ops), E2E_AI_CALL_LIMIT=str(args.ai_call_limit),
                   E2E_AI_COUNTER_FILE=counter)
        expected = owner_persona.expected_calls(list(owner_persona.ANSWERS), skip_depth_five=args.skip_depth5)
        print(f"live AI: {', '.join(live_ops)}; about {sum(expected.values())} calls if Jev agrees with the "
              f"persona, at most {args.ai_call_limit}")
    started_at, started = datetime.now(UTC).isoformat(timespec="seconds"), time.monotonic()
    process = None
    scenario = None
    try:
        process, password = start_server(args.port, args.origin, mailbox.port, env)
        scenario = InterviewEval(f"http://127.0.0.1:{args.port}", args.origin, password, ai=args.ai,
                                 skip_depth_five=args.skip_depth5,
                                 fake_kakao=not os.getenv("KAKAO_REST_API_KEY", "").strip())
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
            import json

            calls = json.loads(Path(counter).read_text()) if os.path.exists(counter) else {}
            record = build_record(scenario, trace=ai_scenario.read_trace(trace), calls=calls, live_ops=live_ops,
                                  call_limit=args.ai_call_limit, started_at=started_at,
                                  wall_seconds=time.monotonic() - started)
            path = Path(args.report) if args.report else default_report_path(scenario.run)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(interview_report.render(record), encoding="utf-8")
            live = [e for e in record["trace"] if e.get("live")]
            print(f"  live AI calls: {len(live)} (limit {args.ai_call_limit}); report: {path}")
        workdir.cleanup()


if __name__ == "__main__":
    raise SystemExit(main())

"""The AI side of the demo E2E, installed inside the server process by `e2e.serve`.

Fake mode (default): a `FakeAiProvider` with deterministic answers, so every manual step can be
asserted exactly. Its outputs still go through the real parsing and server-side validation.

* generate_question: the base question as written (a PROBE asks about the first missing aspect),
  with fixed guidance on BASE questions and one example item ("첫 번째 할 일") on every question.
* judge_sufficiency: an answer containing FAIL_ONCE fails its first evaluation (REFUSED);
  COMMON_TASKS is short of detail once (one PROBE at depth 1); EQUIPMENT is
  never detailed enough (PROBEs up to depth 5, then NEEDS_DETAIL); every other answer suffices.
* summarize_intent: two shifts, a "포스 마감" common task, an opening task on the first shift, a
  rule, an equipment section and an exception section (`SUMMARIES`).
* revise_structure: the correction text replaces the last step of the first section (or, with
  shifts only, moves the first shift to 08:30); a review correction also sets the summary.
* answer_question: a question about "포스" is ANSWERED citing the "포스 마감" section and all of
  its steps; anything else is NEEDS_OWNER.
* transcribe: the fixed default ("테스트 전사 결과예요.").

Live mode (`E2E_AI=live`): operations in `E2E_AI_LIVE_OPS` go to the real provider built from the
environment (OPENAI_*), the rest stay on the fake, and the live calls are capped by
`E2E_AI_CALL_LIMIT`: beyond it a call fails as NOT_CONFIGURED (not retried). Calls are counted
in `E2E_AI_COUNTER_FILE` (JSON per operation) so the runner can report them. The default live
set is everything whose output the manual is made of, but not the question loop, so one run is
a fixed number of calls (see `DEFAULT_LIVE_OPS`).

`E2E_AI_LIVE_OPS` takes operation names and the presets in `LIVE_OPS_PRESETS` (`default`,
`interview` = every interview operation including the question loop, `all`), comma separated.

`E2E_AI_TRACE_FILE` (optional): one JSON line per AI call (live or fake) with the operation, the
intent key / depth / kind it was for, outcome code, wall time, the Jev judgement (sufficient,
probability, missing aspect labels), how many evidence chunks a writing call got, the grounding
counts the provider logs (`dropped_steps`, `cleared_shifts`) and the provider's token usage
numbers. Never prompts, answers or provider output text: the interview evaluation report
(`e2e.interview_eval`) is built from it and from the public API.

`E2E_AI_SCRIPT=persona`: the fake judges by the scripted owner persona (`e2e.owner_persona`)
instead of the demo's fixed COMMON_TASKS/EQUIPMENT rule, so a fake evaluation run walks the path
the persona is written for.
"""
import json
import logging
import os
import threading
import time
from pathlib import Path

from app.ai.contracts import StructureSnapshot
from app.ai.errors import AiError, AiErrorCode
from app.ai.fake import OPERATIONS, FakeAiProvider, structure_to_raw
from app.ai.provider import AiProvider

POS_SECTION = "포스 마감"
POS_ANSWER = "포스 화면에서 마감 정산을 누르고, 출력한 영수증을 금고에 넣어요."
PROBED_ONCE = "COMMON_TASKS"
NEEDS_DETAIL = "EQUIPMENT"
SHIFT_08_30 = "08:30"
QUESTION_GUIDANCE = "처음 일하는 근무자도 따라 할 수 있게 알려주세요."
EXAMPLE_LABEL = "첫 번째 할 일"
EXAMPLE_DESCRIPTION = "순서와 끝났다고 판단하는 기준"
FAIL_ONCE = "(평가 실패 시험)"
# summarize_intent x6 + compose_draft + revise_structure x2 + answer_question x4 + transcribe x2 = 15.
DEFAULT_LIVE_OPS = ("summarize_intent", "compose_draft", "revise_structure", "answer_question", "transcribe")
# Every operation an interview makes: questions, Jev, reviews and their corrections, the draft.
INTERVIEW_OPS = ("judge_sufficiency", "generate_question", "summarize_intent", "revise_structure", "compose_draft")
LIVE_OPS_PRESETS = {"default": DEFAULT_LIVE_OPS, "interview": INTERVIEW_OPS, "all": OPERATIONS}


def resolve_live_ops(spec: str | None) -> tuple[str, ...]:
    """Operation names and preset names (comma separated) -> operations, first-seen order.
    Empty or None is the default set; unknown names are kept for RoutedAiProvider to refuse."""
    ops: list[str] = []
    for token in (spec or "").split(","):
        token = token.strip()
        if token:
            ops.extend(LIVE_OPS_PRESETS.get(token.lower(), (token,)))
    return tuple(dict.fromkeys(ops)) or DEFAULT_LIVE_OPS


def _shift(ref, name, start, end):
    return {"ref": ref, "name": name, "start_time": start, "end_time": end, "ends_next_day": False}


def _section(ref, title, steps, category="COMMON_TASK", shift_ref=None):
    return {"ref": ref, "category": category, "shift_ref": shift_ref, "title": title,
            "steps": [{"ref": f"new-{int(ref.removeprefix('new-')) + index}", "instruction": text,
                       "checklist_item": False} for index, text in enumerate(steps, 1)]}


SUMMARIES = {
    "WORK_STRUCTURE": ("오픈조와 마감조로 나뉘어요.", [_shift("new-1", "오픈조", "09:00", "15:00"),
                                                    _shift("new-2", "마감조", "15:00", "23:00")], []),
    "COMMON_TASKS": ("포스 마감을 모두 함께 해요.", [], [
        _section("new-1", POS_SECTION, ["포스 화면에서 마감 정산을 눌러요.", "출력한 영수증을 금고에 넣어요."])]),
    "RULES": ("근무 중에는 앞치마를 착용해요.", [], [_section("new-1", "매장 규칙", ["앞치마를 착용해요."], category="RULE")]),
    "EQUIPMENT": ("커피 머신 세척 방법은 아직 정하지 못했어요.", [], [
        _section("new-1", "설비", ["커피 머신 전원을 확인해요."], category="EQUIPMENT")]),
    "EXCEPTIONS": ("손님 불만은 점주에게 바로 연락해요.", [], [
        _section("new-1", "예외 상황", ["손님 불만은 점주에게 연락해요."])]),
}


_failed_once: set[str] = set()


def judge(data):
    # An answer carrying FAIL_ONCE fails its first evaluation (not retryable), so the scenario can
    # watch ERROR, the kept question and the retry on the real server; the retry then succeeds.
    answer = data["dialogue"][-1]["answer"]
    if FAIL_ONCE in answer and answer not in _failed_once:
        _failed_once.add(answer)
        raise AiError(AiErrorCode.REFUSED, detail="e2e_fail_once")
    # Jev is not shown the probe counter; the latest turn of the dialogue carries the depth.
    key, depth = data["intent"]["key"], data["dialogue"][-1]["depth"]
    if key == NEEDS_DETAIL or (key == PROBED_ONCE and depth == 0):
        return {"sufficient": False, "probability": 0.2, "missing_aspects": ["마감 순서"]}
    return {"sufficient": True, "probability": 0.9, "missing_aspects": []}


def summarize(data):
    key = data["intent"]["key"]
    if key == "SHIFT_TASKS":
        shift = data["available_shifts"][0]["id"] if data["available_shifts"] else None
        sections = [_section("new-1", "오픈 준비", ["불을 켜고 커피 머신을 예열해요."],
                             category="SHIFT_TASK" if shift else "COMMON_TASK", shift_ref=shift)]
        summary, shifts = "오픈조는 매장 문을 열어요.", []
    else:
        summary, shifts, sections = SUMMARIES[key]
    return {"summary": summary, "structure": {"shifts": shifts, "sections": sections, "missing_information": []}}


def revise(data):
    raw = structure_to_raw(StructureSnapshot.model_validate(data["current"]))
    instruction = data["instruction"][:3000]
    if raw["sections"] and raw["sections"][0]["steps"]:
        raw["sections"][0]["steps"][-1]["instruction"] = instruction
    elif raw["shifts"]:
        raw["shifts"][0]["start_time"] = SHIFT_08_30
    summary = instruction[:10000] if data.get("summary") is not None else None
    return {"outcome": "APPLIED", "summary": summary, "structure": raw}


def answer(data):
    if "포스" not in data["question"]:
        return {"outcome": "NEEDS_OWNER", "answer": "매뉴얼에 없는 내용이라 점주 확인이 필요해요.", "citations": []}
    section = next(s for s in data["manual"]["sections"] if s["title"] == POS_SECTION)
    return {"outcome": "ANSWERED", "answer": POS_ANSWER,
            "citations": [{"section_id": section["id"], "step_ids": [step["id"] for step in section["steps"]]}]}


def question(data):
    """The intent's question as written, with fixed guidance and one example per question kind."""
    if data["kind"] == "BASE":
        return {"question": data["intent"]["base_question"], "guidance": QUESTION_GUIDANCE,
                "examples": [{"label": EXAMPLE_LABEL, "description": EXAMPLE_DESCRIPTION}]}
    return {"question": f"{data['missing_aspects'][0]}에 대해 조금 더 자세히 알려 주세요.", "guidance": None,
            "examples": [{"label": EXAMPLE_LABEL, "description": None}]}


def install(fake: FakeAiProvider) -> FakeAiProvider:
    return (fake.on("judge_sufficiency", judge).on("generate_question", question)
            .on("summarize_intent", summarize).on("revise_structure", revise).on("answer_question", answer))


class CallBudget:
    """Counts live calls per operation (persisted for the runner) and refuses past `limit`."""

    def __init__(self, limit: int, path: str | None) -> None:
        self.limit, self.path = limit, Path(path) if path else None
        self.counts: dict[str, int] = {}
        self.refused = 0
        self._lock = threading.Lock()
        self._write()

    def take(self, operation: str) -> None:
        with self._lock:
            if sum(self.counts.values()) >= self.limit:
                self.refused += 1
                self._write()
                raise AiError(AiErrorCode.NOT_CONFIGURED, detail="e2e_call_budget")
            self.counts[operation] = self.counts.get(operation, 0) + 1
            self._write()

    def _write(self) -> None:
        if self.path is not None:
            self.path.write_text(json.dumps({"limit": self.limit, "calls": self.counts, "refused": self.refused}))


_current = threading.local()  # the trace record of the call running on this thread


class _GroundingCounts(logging.Handler):
    """Adds the provider's `ai grounding` log counts to the running call's trace record."""

    def emit(self, record: logging.LogRecord) -> None:
        entry = getattr(_current, "entry", None)
        args = record.args if isinstance(record.args, tuple) else ()
        if entry is None or not str(record.msg).startswith("ai grounding") or len(args) not in (3, 4):
            return
        # (op, dropped, cleared) or (op, dropped, dropped_in_kept_sections, cleared)
        _op, dropped, *partial, cleared = args
        entry["dropped_steps"] = entry.get("dropped_steps", 0) + int(dropped)
        if partial:
            entry["dropped_in_kept_sections"] = entry.get("dropped_in_kept_sections", 0) + int(partial[0])
        entry["cleared_shifts"] = entry.get("cleared_shifts", 0) + int(cleared)


_grounding_handler = _GroundingCounts()


def usage_numbers(usage) -> dict[str, int]:
    """Token counts of a Responses / Decisions usage object or dict: numbers only, one level of
    `*_details` flattened as `<details>.<name>`."""
    if usage is None:
        return {}
    if not isinstance(usage, dict):
        dump = getattr(usage, "model_dump", None)
        usage = dump() if callable(dump) else {}
    numbers: dict[str, int] = {}
    for name, value in usage.items():
        if isinstance(value, int) and not isinstance(value, bool):
            numbers[name] = value
        elif isinstance(value, dict):
            for sub, inner in value.items():
                if isinstance(inner, int) and not isinstance(inner, bool):
                    numbers[f"{name}.{sub}"] = inner
    return numbers


def _add_usage(usage) -> None:
    entry = getattr(_current, "entry", None)
    if entry is None:
        return
    total = entry.setdefault("usage", {})
    for name, value in usage_numbers(usage).items():
        total[name] = total.get(name, 0) + value


class _UsageResponses:
    def __init__(self, responses) -> None:
        self._responses = responses

    def create(self, *args, **kwargs):
        response = self._responses.create(*args, **kwargs)
        _add_usage(getattr(response, "usage", None))
        return response


class _UsageClient:
    """Wraps an OpenAI client: records the token usage of `responses.create` and of the Decisions
    `post`, and passes everything else through. No text is read or kept."""

    def __init__(self, client) -> None:
        self._client = client
        self.responses = _UsageResponses(client.responses)

    def post(self, *args, **kwargs):
        response = self._client.post(*args, **kwargs)
        try:
            body = json.loads(response.content)
            _add_usage(body.get("usage") if isinstance(body, dict) else None)
        except (AttributeError, TypeError, ValueError):
            pass
        return response

    def __getattr__(self, name):
        return getattr(self._client, name)

    def __repr__(self) -> str:  # never show the wrapped client (it holds the key)
        return "_UsageClient()"


def count_usage(provider: AiProvider) -> None:
    """Record the token usage of `provider`'s OpenAI client (and its fallback's), if it has one."""
    for backend in (provider, getattr(provider, "primary", None), getattr(provider, "fallback", None)):
        client = getattr(backend, "_client", None)
        if client is not None and not isinstance(client, _UsageClient) and hasattr(client, "responses"):
            backend._client = _UsageClient(client)


def _request_facts(operation: str, request) -> dict:
    """Which intent / depth / kind a request is for, and how much evidence it carries."""
    facts: dict = {}
    intent = getattr(request, "intent", None)
    if intent is not None:
        facts["intent"] = intent.key
    for name in ("depth", "kind", "needs_detail"):
        value = getattr(request, name, None)
        if value is not None:
            facts[name] = value
    evidence = getattr(request, "evidence", None)
    if evidence is not None:
        facts["evidence"] = len(evidence)
    if operation == "revise_structure":
        facts["target"] = request.target.kind
        facts["review"] = request.summary is not None
    return facts


def _result_facts(operation: str, result) -> dict:
    meta = getattr(result, "meta", None)
    facts = {"config": meta.config_version} if meta is not None else {}
    if operation == "judge_sufficiency":
        facts.update(sufficient=result.sufficient, probability=round(result.probability, 4),
                     missing_aspects=list(result.missing_aspects))
    return facts


class CallTrace:
    """Appends one JSON line per AI call to `path` (fields: see the module docstring)."""

    def __init__(self, path: str | None) -> None:
        self.path = Path(path) if path else None
        self._lock = threading.Lock()
        if self.path is not None:
            self.path.write_text("", encoding="utf-8")
            ai_logger = logging.getLogger("jidan.ai")
            if _grounding_handler not in ai_logger.handlers:
                ai_logger.addHandler(_grounding_handler)
            if ai_logger.getEffectiveLevel() > logging.INFO:
                ai_logger.setLevel(logging.INFO)

    def run(self, operation: str, request, live: bool, call):
        if self.path is None:
            return call()
        entry = {"op": operation, "live": live, **_request_facts(operation, request)}
        _current.entry = entry
        started = time.monotonic()
        try:
            result = call()
            entry.update(outcome="ok", **_result_facts(operation, result))
            return result
        except AiError as error:
            entry["outcome"] = error.code.value
            raise
        except Exception as error:  # an unexpected failure is still one traced call
            entry["outcome"] = type(error).__name__
            raise
        finally:
            _current.entry = None
            entry["ms"] = int((time.monotonic() - started) * 1000)
            with self._lock, self.path.open("a", encoding="utf-8") as out:
                out.write(json.dumps(entry, ensure_ascii=False) + "\n")


def read_trace(path) -> list[dict]:
    """The records `CallTrace` wrote to `path` (none when it does not exist)."""
    path = Path(path)
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


class RoutedAiProvider(AiProvider):
    """`live_ops` go to `live` (counted against `budget`), the rest to `fake`; every call is
    recorded by `trace` when it has a file."""

    def __init__(self, fake: AiProvider, live: AiProvider, live_ops, budget: CallBudget,
                 trace: CallTrace | None = None) -> None:
        unknown = set(live_ops) - set(OPERATIONS)
        if unknown:
            raise ValueError(f"unknown AI operations: {sorted(unknown)}")
        self.fake, self.live, self.live_ops, self.budget = fake, live, frozenset(live_ops), budget
        self.trace = trace or CallTrace(None)
        self.provider_name, self.model, self.transcribe_model = live.provider_name, live.model, live.transcribe_model

    @property
    def max_call_seconds(self) -> float:
        return max(self.fake.max_call_seconds, self.live.max_call_seconds)

    def judge_meta(self):
        provider = self.live if "judge_sufficiency" in self.live_ops else self.fake
        return provider.judge_meta()

    def reset_judge_meta(self):
        provider = self.live if "judge_sufficiency" in self.live_ops else self.fake
        provider.reset_judge_meta()

    def _complete(self, *_args, **_kwargs):  # pragma: no cover - operations are delegated
        raise NotImplementedError

    def _transcribe(self, _request):  # pragma: no cover - operations are delegated
        raise NotImplementedError

    def _call(self, operation: str, request):
        if operation not in self.live_ops:
            return self.trace.run(operation, request, False, lambda: getattr(self.fake, operation)(request))
        self.budget.take(operation)
        return self.trace.run(operation, request, True, lambda: getattr(self.live, operation)(request))

    def judge_sufficiency(self, request):
        return self._call("judge_sufficiency", request)

    def generate_question(self, request):
        return self._call("generate_question", request)

    def summarize_intent(self, request):
        return self._call("summarize_intent", request)

    def revise_structure(self, request):
        return self._call("revise_structure", request)

    def compose_draft(self, request):
        return self._call("compose_draft", request)

    def answer_question(self, request):
        return self._call("answer_question", request)

    def transcribe(self, request):
        return self._call("transcribe", request)

    def write_section_from_media(self, request):
        return self._call("write_section_from_media", request)


def build_from_env() -> AiProvider | None:
    """The provider `e2e.serve` installs, or None when E2E_AI is unset (the app's own choice)."""
    mode = os.getenv("E2E_AI", "").strip().lower()
    if not mode:
        return None
    timeout = float(os.getenv("OPENAI_TIMEOUT_SECONDS", "").strip() or 60)
    if mode not in ("fake", "live"):
        raise ValueError("E2E_AI must be fake or live")
    fake = install(FakeAiProvider(timeout_seconds=timeout))
    script = os.getenv("E2E_AI_SCRIPT", "").strip().lower()
    if script == "persona":
        from e2e import owner_persona

        owner_persona.install_fake(fake)
        if os.getenv("E2E_INTERVIEW_EVAL_VOICE") == "1":
            from e2e.interview_eval import install_voice_fake

            install_voice_fake(fake)
    elif script:
        raise ValueError("E2E_AI_SCRIPT must be persona or unset")
    trace = CallTrace(os.getenv("E2E_AI_TRACE_FILE") or None)
    if mode == "fake":
        # Traced fake calls let the evaluation report run without a key.
        return fake if trace.path is None else RoutedAiProvider(fake, fake, (), CallBudget(0, None), trace)
    from app.ai import build_provider_from_env

    live = build_provider_from_env()  # AI_PROVIDER=openai with OPENAI_API_KEY from the environment
    if trace.path is not None:
        count_usage(live)
    ops = resolve_live_ops(os.getenv("E2E_AI_LIVE_OPS"))
    budget = CallBudget(int(os.getenv("E2E_AI_CALL_LIMIT", "20")), os.getenv("E2E_AI_COUNTER_FILE"))
    return RoutedAiProvider(fake, live, ops, budget, trace)

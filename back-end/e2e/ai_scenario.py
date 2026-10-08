"""The AI side of the demo E2E, installed inside the server process by `e2e.serve`.

Fake mode (default): a `FakeAiProvider` with deterministic answers, so every manual step can be
asserted exactly. Its outputs still go through the real parsing and server-side validation.

* judge_sufficiency: COMMON_TASKS is short of detail once (one PROBE at depth 1); EQUIPMENT is
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
"""
import json
import os
import threading
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
# summarize_intent x6 + compose_draft + revise_structure x2 + answer_question x4 + transcribe x2 = 15.
DEFAULT_LIVE_OPS = ("summarize_intent", "compose_draft", "revise_structure", "answer_question", "transcribe")


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


def judge(data):
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


def install(fake: FakeAiProvider) -> FakeAiProvider:
    return (fake.on("judge_sufficiency", judge).on("summarize_intent", summarize)
            .on("revise_structure", revise).on("answer_question", answer))


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


class RoutedAiProvider(AiProvider):
    """`live_ops` go to `live` (counted against `budget`), the rest to `fake`."""

    def __init__(self, fake: AiProvider, live: AiProvider, live_ops, budget: CallBudget) -> None:
        unknown = set(live_ops) - set(OPERATIONS)
        if unknown:
            raise ValueError(f"unknown AI operations: {sorted(unknown)}")
        self.fake, self.live, self.live_ops, self.budget = fake, live, frozenset(live_ops), budget
        self.provider_name, self.model, self.transcribe_model = live.provider_name, live.model, live.transcribe_model

    @property
    def max_call_seconds(self) -> float:
        return max(self.fake.max_call_seconds, self.live.max_call_seconds)

    def _complete(self, *_args, **_kwargs):  # pragma: no cover - operations are delegated
        raise NotImplementedError

    def _transcribe(self, _request):  # pragma: no cover - operations are delegated
        raise NotImplementedError

    def _call(self, operation: str, request):
        if operation not in self.live_ops:
            return getattr(self.fake, operation)(request)
        self.budget.take(operation)
        return getattr(self.live, operation)(request)

    def judge_sufficiency(self, request):
        return self._call("judge_sufficiency", request)

    def generate_question(self, request):
        return self._call("generate_question", request)

    def summarize_intent(self, request):
        return self._call("summarize_intent", request)

    def suggest_review_photos(self, request):
        return self._call("suggest_review_photos", request)

    def revise_structure(self, request):
        return self._call("revise_structure", request)

    def compose_draft(self, request):
        return self._call("compose_draft", request)

    def answer_question(self, request):
        return self._call("answer_question", request)

    def transcribe(self, request):
        return self._call("transcribe", request)


def build_from_env() -> AiProvider | None:
    """The provider `e2e.serve` installs, or None when E2E_AI is unset (the app's own choice)."""
    mode = os.getenv("E2E_AI", "").strip().lower()
    if not mode:
        return None
    timeout = float(os.getenv("OPENAI_TIMEOUT_SECONDS", "").strip() or 60)
    fake = install(FakeAiProvider(timeout_seconds=timeout))
    if mode == "fake":
        return fake
    if mode != "live":
        raise ValueError("E2E_AI must be fake or live")
    from app.ai import build_provider_from_env

    live = build_provider_from_env()  # AI_PROVIDER=openai with OPENAI_API_KEY from the environment
    ops = [op.strip() for op in os.getenv("E2E_AI_LIVE_OPS", ",".join(DEFAULT_LIVE_OPS)).split(",") if op.strip()]
    budget = CallBudget(int(os.getenv("E2E_AI_CALL_LIMIT", "20")), os.getenv("E2E_AI_COUNTER_FILE"))
    return RoutedAiProvider(fake, live, ops, budget)

"""Deterministic, network-free AI backend for tests and local development.

It plugs in under `AiProvider`, so scripted raw output goes through the same JSON parsing,
schema validation and server-side re-validation as real OpenAI output.

    fake = FakeAiProvider()
    fake.script("judge_sufficiency", FakeOutcome.ok({"sufficient": False, "probability": 0.2,
                                                     "missing_aspects": ["마감 순서"]}))
    fake.script("generate_question", FakeOutcome.fail(AiErrorCode.TIMEOUT))
    fake.script("summarize_intent", FakeOutcome.raw('{"summary": '))    # broken JSON
    fake.script("transcribe", FakeOutcome.ok(""))                        # silence -> EMPTY_TRANSCRIPT
    fake.script("answer_question", FakeOutcome.delay(5.0))                # >= timeout -> TIMEOUT

Scripted outcomes are consumed in order per operation; when the queue is empty the operation's
default responder answers (`on(...)` replaces it). `calls` records every call with the parsed
data document so tests can assert what the model was shown.

Jev has two backends (`app.ai.provider`), and the fake picks one per call from what is scripted:

    fake.script("judge_sufficiency", FakeOutcome.predicates(aspect_2=0.3))   # Decisions response
    fake.script("judge_sufficiency", FakeOutcome.decision({"answers": []}))  # raw Decisions JSON
    fake.script("judge_sufficiency", FakeOutcome.ok({"sufficient": ...}))    # Responses judgement

`decision`/`predicates` outcomes go through the Decisions path (request body, answer
re-validation, thresholds); `ok`/`raw`/`candidates` through the structured-output path, as before.
With nothing scripted (or a `fail`), the Decisions path answers with every aspect covered, unless
`on("judge_sufficiency", ...)` replaced the Responses default. `FakeAiProvider(judge_backend=
"responses")` always uses the structured-output path.
"""

import json
import threading
import time
from collections import deque
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel

from app.ai.contracts import ImageInput, StructureSnapshot, SufficiencyRequest, TranscriptionRequest
from app.ai.decisions import NOT_APPLICABLE
from app.ai.errors import AiError, AiErrorCode
from app.ai.provider import JUDGE_BACKENDS, AiProvider

OPERATIONS = (
    "judge_sufficiency", "generate_question", "summarize_intent", "revise_structure",
    "compose_draft", "answer_question", "transcribe",
)


@dataclass(frozen=True)
class FakeOutcome:
    kind: str  # ok | raw | fail | delay | candidates | decision
    value: Any = None
    seconds: float = 0.0
    then: "FakeOutcome | None" = None

    @staticmethod
    def ok(value: Any) -> "FakeOutcome":
        """A model answer: a dict/pydantic model (serialized to JSON) or, for transcribe, text."""
        return FakeOutcome("ok", value)

    @staticmethod
    def raw(text: str) -> "FakeOutcome":
        """Exactly this raw output text (use for invalid JSON / schema violations)."""
        return FakeOutcome("raw", text)

    @staticmethod
    def candidates(*texts: str) -> "FakeOutcome":
        """Several message items, like gpt-6-luna occasionally returns."""
        return FakeOutcome("candidates", texts)

    @staticmethod
    def decision(response: Any) -> "FakeOutcome":
        """A raw Decisions API response (any JSON value, to test re-validation), or a function
        body -> response built from the request (question names depend on the intent)."""
        return FakeOutcome("decision", response)

    @staticmethod
    def predicates(default: float = 0.95, *, not_applicable: float = 0.05,
                   refuse: Sequence[str] = (), **by_name: float) -> "FakeOutcome":
        """A well-formed Decisions response: `by_name` (aspect_1, aspect_2, ...) overrides the
        default aspect probability, `refuse` names questions answered with a refusal."""
        def respond(body):
            answers = []
            for question in body["questions"]:
                name = question["name"]
                if name in refuse:
                    answers.append({"type": "refusal", "name": name})
                    continue
                fallback = not_applicable if name == NOT_APPLICABLE else default
                answers.append({"type": "predicate", "name": name, "probability": by_name.get(name, fallback)})
            return {"model": body["model"], "answers": answers, "usage": {}}
        return FakeOutcome("decision", respond)

    @staticmethod
    def fail(code: AiErrorCode | str) -> "FakeOutcome":
        return FakeOutcome("fail", AiErrorCode(code))

    @staticmethod
    def delay(seconds: float, then: "FakeOutcome | None" = None) -> "FakeOutcome":
        """Take `seconds`; at or beyond the fake's timeout this becomes a TIMEOUT failure."""
        return FakeOutcome("delay", seconds=seconds, then=then)


@dataclass
class FakeCall:
    operation: str
    data: dict[str, Any] | None
    instructions: str | None = None
    image_count: int = 0
    extra: dict[str, Any] = field(default_factory=dict)


def structure_to_raw(snapshot: StructureSnapshot) -> dict[str, Any]:
    """A StructureSnapshot as model output that keeps every existing ID."""
    return {
        "shifts": [
            {"ref": s.id, "name": s.name, "start_time": s.start_time, "end_time": s.end_time,
             "ends_next_day": s.ends_next_day}
            for s in snapshot.shifts
        ],
        "sections": [
            {"ref": s.id, "category": s.category, "shift_ref": s.shift_id, "title": s.title,
             "steps": [{"ref": t.id, "instruction": t.instruction, "checklist_item": t.checklist_item}
                       for t in s.steps]}
            for s in snapshot.sections
        ],
        "missing_information": [
            {"target": m.target, "target_ref": m.target_id, "field": m.field, "description": m.description}
            for m in snapshot.missing_information
        ],
    }


def _default_judge(_data):
    return {"sufficient": True, "probability": 0.9, "missing_aspects": []}


def _default_decision(body):
    return FakeOutcome.predicates().value(body)


def _default_question(data):
    if data["kind"] == "BASE":
        return {"question": data["intent"]["base_question"]}
    return {"question": f"{data['missing_aspects'][0]}에 대해 조금 더 자세히 알려 주세요."}


def _default_summary(data):
    answers = [turn["answer"] for turn in data["dialogue"]]
    title = data["intent"]["key"][:100]
    return {
        "summary": ("정리한 내용이에요: " + " ".join(answers))[:10000],
        "structure": {
            "shifts": [],
            "sections": [{
                "ref": "new-1", "category": "COMMON_TASK", "shift_ref": None, "title": title,
                "steps": [
                    {"ref": f"new-{index + 2}", "instruction": answer[:3000], "checklist_item": False}
                    for index, answer in enumerate(answers)
                ],
            }],
            "missing_information": [],
        },
    }


def _default_revision(data):
    current = StructureSnapshot.model_validate(data["current"])
    return {"outcome": "NO_CHANGE", "summary": None, "structure": structure_to_raw(current)}


def _default_draft(data):
    merged = {"shifts": [], "sections": [], "missing_information": []}
    for review in data["reviews"]:
        raw = structure_to_raw(StructureSnapshot.model_validate(review["structure"]))
        for key, items in merged.items():
            items.extend(raw[key])
    if not merged["shifts"]:
        merged["missing_information"].append({
            "target": "MANUAL", "target_ref": None, "field": "shifts",
            "description": "근무조 정보가 아직 정해지지 않았어요.",
        })
    if not merged["sections"]:
        merged["missing_information"].append({
            "target": "MANUAL", "target_ref": None, "field": "sections",
            "description": "업무 정보가 아직 정해지지 않았어요.",
        })
    return {"structure": merged}


def _default_answer(_data):
    return {"outcome": "NEEDS_OWNER", "answer": "매뉴얼에 없는 내용이라 점주 확인이 필요해요.", "citations": []}


def _default_transcript(_request):
    return "테스트 전사 결과예요."


DEFAULTS: dict[str, Callable[[Any], Any]] = {
    "judge_sufficiency": _default_judge,
    "generate_question": _default_question,
    "summarize_intent": _default_summary,
    "revise_structure": _default_revision,
    "compose_draft": _default_draft,
    "answer_question": _default_answer,
    "transcribe": _default_transcript,
}


def _parse_data(message: str) -> dict[str, Any] | None:
    start, end = message.find("<data>\n"), message.rfind("\n</data>")
    if start < 0 or end < 0:
        return None
    return json.loads(message[start + len("<data>\n"):end])


class FakeAiProvider(AiProvider):
    provider_name = "fake"
    model = "fake-llm"
    transcribe_model = "fake-stt"

    def __init__(self, *, timeout_seconds: float = 1.0, sleep: Callable[[float], None] = time.sleep,
                 judge_backend: str = "decisions"):
        if judge_backend not in JUDGE_BACKENDS:
            raise ValueError("judge_backend must be decisions or responses")
        self.timeout_seconds = timeout_seconds
        self.judge_backend = judge_backend
        self._sleep = sleep
        self._lock = threading.Lock()
        self._queues: dict[str, deque[FakeOutcome]] = {op: deque() for op in OPERATIONS}
        self._handlers: dict[str, Callable[[Any], Any]] = dict(DEFAULTS)
        self.calls: list[FakeCall] = []

    @property
    def max_call_seconds(self) -> float:
        return self.timeout_seconds

    def script(self, operation: str, *outcomes: FakeOutcome) -> "FakeAiProvider":
        if operation not in OPERATIONS:
            raise ValueError(f"unknown operation {operation!r}")
        with self._lock:
            self._queues[operation].extend(outcomes)
        return self

    def on(self, operation: str, handler: Callable[[Any], Any]) -> "FakeAiProvider":
        """Replace the default responder: handler(data dict or TranscriptionRequest) -> output."""
        if operation not in OPERATIONS:
            raise ValueError(f"unknown operation {operation!r}")
        self._handlers[operation] = handler
        return self

    def pending(self, operation: str) -> int:
        with self._lock:
            return len(self._queues[operation])

    def calls_for(self, operation: str) -> list[FakeCall]:
        return [call for call in self.calls if call.operation == operation]

    def _next(self, operation: str) -> FakeOutcome | None:
        with self._lock:
            queue = self._queues[operation]
            return queue.popleft() if queue else None

    def _resolve(self, operation: str, outcome: FakeOutcome | None, argument: Any,
                 default: Callable[[Any], Any] | None = None) -> Any:
        if outcome is None:
            return (default or self._handlers[operation])(argument)
        if outcome.kind == "fail":
            raise AiError(outcome.value)
        if outcome.kind == "delay":
            if outcome.seconds >= self.timeout_seconds:
                self._sleep(self.timeout_seconds)
                raise AiError(AiErrorCode.TIMEOUT)
            self._sleep(outcome.seconds)
            return self._resolve(operation, outcome.then, argument, default)
        return outcome

    def _judge_backend(self, request: SufficiencyRequest) -> str:
        if self.judge_backend == "responses":
            return "responses"
        with self._lock:
            queue = self._queues["judge_sufficiency"]
            head = queue[0] if queue else None
        while head is not None and head.kind == "delay":
            head = head.then
        if head is not None and head.kind in ("ok", "raw", "candidates"):
            return "responses"
        if head is None and self._handlers["judge_sufficiency"] is not _default_judge:
            return "responses"
        return "decisions"

    def _decide(self, body: dict[str, Any]) -> Any:
        data = _parse_data(body["input"][0]["content"][0]["text"])
        with self._lock:
            self.calls.append(FakeCall(
                "judge_sufficiency", data, "\n\n".join(q["instructions"] for q in body["questions"]),
                extra={"backend": "decisions", "body": body},
            ))
        result = self._resolve("judge_sufficiency", self._next("judge_sufficiency"), body, _default_decision)
        if isinstance(result, FakeOutcome):
            if result.kind != "decision":
                raise ValueError("a Decisions call needs FakeOutcome.decision/predicates")
            result = result.value(body) if callable(result.value) else result.value
        return json.loads(json.dumps(result))  # what a JSON response body decodes to

    def _complete(self, operation: str, instructions: str, message: str,
                  images: Sequence[ImageInput]) -> list[str]:
        data = _parse_data(message)
        with self._lock:
            self.calls.append(FakeCall(operation, data, instructions, len(images)))
        result = self._resolve(operation, self._next(operation), data)
        if isinstance(result, FakeOutcome):
            if result.kind == "raw":
                return [result.value]
            if result.kind == "candidates":
                return list(result.value)
            result = result.value
        if isinstance(result, BaseModel):
            result = result.model_dump(mode="json")
        return [result if isinstance(result, str) else json.dumps(result, ensure_ascii=False)]

    def _transcribe(self, request: TranscriptionRequest) -> str:
        with self._lock:
            self.calls.append(FakeCall(
                "transcribe", None, extra={"mime_type": request.mime_type, "size": len(request.audio)},
            ))
        result = self._resolve("transcribe", self._next("transcribe"), request)
        if isinstance(result, FakeOutcome):
            result = result.value
        return result

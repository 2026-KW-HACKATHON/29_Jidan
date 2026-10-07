"""Replay captured outputs through production parsing, then explicit gold checks."""
import argparse
import hashlib
import json
import math
import subprocess
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, JsonValue, model_validator

from app.ai.contracts import (
    DraftRequest,
    IntentSummaryRequest,
    QaRequest,
    QuestionRequest,
    StructureRevisionRequest,
    SufficiencyRequest,
)
from app.ai.errors import AiError, AiErrorCode
from app.ai.provider import AiProvider

REQUESTS = {
    "judge_sufficiency": SufficiencyRequest, "generate_question": QuestionRequest,
    "summarize_intent": IntentSummaryRequest, "revise_structure": StructureRevisionRequest,
    "compose_draft": DraftRequest, "answer_question": QaRequest,
}
Operation = Literal[
    "judge_sufficiency", "generate_question", "summarize_intent", "revise_structure",
    "compose_draft", "answer_question",
]


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, allow_inf_nan=False)


class Check(Strict):
    id: str = Field(min_length=1)
    kind: Literal["equals", "contains_item", "probability_consistent", "preserve_scope"]
    path: str | None = None
    value: JsonValue = None
    critical: bool = True

    @model_validator(mode="after")
    def valid_contract(self):
        if self.kind in ("equals", "contains_item"):
            if not self.path or "value" not in self.model_fields_set:
                raise ValueError("value and nonempty path required")
        elif self.path is not None or "value" in self.model_fields_set:
            raise ValueError("special checks take no path/value")
        return self


class Expected(Strict):
    checks: list[Check] = Field(min_length=1)
    human_review_required: bool = True

    @model_validator(mode="after")
    def unique_checks(self):
        if len({c.id for c in self.checks}) != len(self.checks):
            raise ValueError("duplicate check id")
        return self


class Case(Strict):
    id: str = Field(min_length=1)
    operation: Operation
    industry: Literal["CAFE", "RESTAURANT", "CONVENIENCE_STORE"]
    request: dict[str, Any]
    expected: Expected
    provenance: Literal["synthetic"]
    dataset_version: str = Field(min_length=1)

    @model_validator(mode="after")
    def valid_request(self):
        REQUESTS[self.operation].model_validate(self.request)
        if (any(c.kind == "probability_consistent" for c in self.expected.checks)
                and self.operation != "judge_sufficiency"):
            raise ValueError("probability check requires judge_sufficiency")
        if (any(c.kind == "preserve_scope" for c in self.expected.checks)
                and self.operation != "revise_structure"):
            raise ValueError("scope check requires revise_structure")
        return self


class Capture(Strict):
    provider: str = Field(min_length=1)
    model: str = Field(min_length=1)
    prompt_version: str = Field(min_length=1)
    reasoning_effort: str | None
    provenance: Literal["synthetic", "live"]


class Usage(Strict):
    input_tokens: int = Field(ge=0)
    output_tokens: int = Field(ge=0)
    cached_tokens: int | None = Field(default=None, ge=0)
    reasoning_tokens: int | None = Field(default=None, ge=0)


class Response(Strict):
    case_id: str = Field(min_length=1)
    raw_output: str | None = None
    error: str | None = None
    captured: Capture
    elapsed_ms: float | None = Field(ge=0)
    usage: Usage | None

    @model_validator(mode="after")
    def exactly_one(self):
        if (self.raw_output is None) == (self.error is None):
            raise ValueError("exactly one of raw_output/error required")
        if self.error is not None:
            AiErrorCode(self.error)
        return self


class ReplayProvider(AiProvider):
    provider_name = "offline-replay"
    model = "captured-output"

    def __init__(self, response: Response):
        self.response = response

    def _complete(self, operation, instructions, message, images):
        if self.response.error is not None:
            raise AiError(AiErrorCode(self.response.error))
        return [self.response.raw_output]

    def _transcribe(self, request):
        raise RuntimeError("transcription is outside the six-operation replay contract")


def load_jsonl(path: Path, schema, key: str):
    rows = []
    seen = set()
    for line_no, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            row = schema.model_validate(json.loads(line))
        except (ValueError, TypeError):
            # Never print validation input (may contain captured private text).
            raise ValueError(f"invalid {schema.__name__} at line {line_no}") from None
        identifier = getattr(row, key)
        if identifier in seen:
            raise ValueError(f"duplicate {schema.__name__} identifier at line {line_no}")
        seen.add(identifier)
        rows.append(row)
    if not rows:
        raise ValueError(f"empty {schema.__name__} file")
    return rows


def at_path(value, path):
    for part in path.split("."):
        value = value[int(part)] if isinstance(value, list) else value[part]
    return value


def gold_check(check: Check, result: dict, request):
    if check.kind == "probability_consistent":
        return result["sufficient"] == (result["probability"] >= 0.5)
    if check.kind == "preserve_scope":
        # Stronger than production MANUAL-target check: supplied fixtures expect only target delta.
        before = request.current.model_dump(mode="json")
        after = result.get("structure")
        if after is None:
            return result["outcome"] != "APPLIED"
        for category in ("shifts", "sections"):
            changed = {item["id"]: item for item in after[category]}
            for item in before[category]:
                if ((request.target.kind == "SHIFT" and category == "shifts"
                        or request.target.kind == "SECTION" and category == "sections")
                        and item["id"] == request.target.target_id):
                    continue
                if changed.get(item["id"]) != item:
                    return False
        return True
    value = at_path(result, check.path)
    if check.kind == "equals":
        # JSON booleans must not match integer gold values accidentally.
        return type(value) is type(check.value) and value == check.value
    return isinstance(value, list) and any(
        type(item) is type(check.value) and item == check.value for item in value
    )


def latency(values):
    values = sorted(values)
    if not values:
        return {"n": 0, "p50_ms": None, "p95_ms": None}
    return {"n": len(values), "p50_ms": values[math.ceil(len(values)*0.5)-1],
            "p95_ms": values[math.ceil(len(values)*0.95)-1]}


def evaluate(dataset: Path, responses: Path):
    cases = load_jsonl(dataset, Case, "id")
    captures = load_jsonl(responses, Response, "case_id")
    lookup = {r.case_id: r for r in captures}
    if set(lookup) - {c.id for c in cases}:
        raise ValueError("responses contain unknown case identifiers")
    rows = []
    for case in cases:
        response = lookup.get(case.id)
        row = {"case_id": case.id, "operation": case.operation, "industry": case.industry,
               "structure": "missing_response", "gold_status": "skipped_missing_response",
               "critical_gold_failures": [],
               "noncritical_gold_failures": [], "human_review": "pending"
               if case.expected.human_review_required else "not_required"}
        if response is not None:
            row["captured"] = response.captured.model_dump(mode="json")
            request = REQUESTS[case.operation].model_validate(case.request)
            try:
                result = getattr(ReplayProvider(response), case.operation)(request).model_dump(mode="json")
                row["structure"] = "pass"
            except AiError as error:
                row["structure"] = "fail"
                row["structure_error_code"] = error.code.value
            except (ValueError, TypeError):
                row["structure"] = "fail"
                row["structure_error_code"] = "invalid_output"
            row["gold_status"] = "skipped_structure_failure"
            if row["structure"] == "pass":
                row["gold_status"] = "pass"
                for check in case.expected.checks:
                    try:
                        passed = gold_check(check, result, request)
                    except (KeyError, IndexError, TypeError, ValueError):
                        passed = False
                    if not passed:
                        key = "critical_gold_failures" if check.critical else "noncritical_gold_failures"
                        row[key].append(check.id)
                        row["gold_status"] = "fail"
        rows.append(row)
    try:
        revision = subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=Path(__file__).resolve().parents[2],
            text=True, stderr=subprocess.DEVNULL,
        ).strip()
    except (OSError, subprocess.CalledProcessError):
        revision = None
    stats = {}
    for operation in REQUESTS:
        relevant = [lookup[c.id] for c in cases if c.operation == operation and c.id in lookup]
        stats[operation] = {
            "captured_latency": {p: latency([r.elapsed_ms for r in relevant
                if r.captured.provenance == p and r.elapsed_ms is not None])
                for p in ("synthetic", "live")},
            "usage_provided_n": sum(r.usage is not None for r in relevant),
            "usage_missing_n": sum(r.usage is None for r in relevant),
            "usage_totals": {field: sum(getattr(r.usage, field) for r in relevant
                if r.usage is not None and getattr(r.usage, field) is not None)
                if any(r.usage is not None and getattr(r.usage, field) is not None for r in relevant)
                else None for field in Usage.model_fields},
        }
    failed = any(r["structure"] != "pass" or r["critical_gold_failures"] for r in rows)
    return {
        "schema_version": "1", "code_sha": revision,
        "code_files_sha256": {str(p.relative_to(Path(__file__).resolve().parents[1])):
            hashlib.sha256(p.read_bytes()).hexdigest() for p in [
                Path(__file__).resolve(),
                *sorted((Path(__file__).resolve().parents[1] / "app" / "ai").glob("*.py"))
            ]},
        "dataset_sha256": hashlib.sha256(dataset.read_bytes()).hexdigest(),
        "responses_sha256": hashlib.sha256(responses.read_bytes()).hexdigest(),
        "model_quality_verified": False,
        "quality_notice": "Replay gold checks verify explicit fixture properties only; human semantic review remains pending.",
        "n": len(rows), "structure_failure_n": sum(r["structure"] == "fail" for r in rows),
        "missing_response_n": sum(r["structure"] == "missing_response" for r in rows),
        "critical_gold_failure_n": sum(bool(r["critical_gold_failures"]) for r in rows),
        "human_review_pending_n": sum(r["human_review"] == "pending" for r in rows),
        "failed": failed, "operations": stats, "cases": rows,
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", required=True, type=Path)
    parser.add_argument("--responses", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args(argv)
    try:
        report = evaluate(args.dataset, args.responses)
    except (OSError, ValueError):
        # No raw validation details, prompts, answers, or environment variables.
        parser.exit(2, "Invalid evaluation input; check JSONL contract and identifiers.\n")
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2)+"\n", encoding="utf-8")
    return 1 if report["failed"] else 0


if __name__ == "__main__":
    raise SystemExit(main())

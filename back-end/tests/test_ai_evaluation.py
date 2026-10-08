"""Offline evaluator invariants; fixtures are not evidence of model quality."""
import json
import socket
import subprocess
from pathlib import Path

import pytest
from pydantic import ValidationError

from evals.runner import Case, Response, evaluate, load_jsonl, main

FIXTURES = Path(__file__).resolve().parents[1] / "evals" / "fixtures"


def checkout_revision() -> str | None:
    """HEAD of the repository the runner lives in; None outside a git checkout (Docker test stage)."""
    try:
        return subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=FIXTURES.parents[2],
                                       text=True, stderr=subprocess.DEVNULL).strip()
    except (OSError, subprocess.CalledProcessError):
        return None


def rows(name):
    return [json.loads(line) for line in (FIXTURES / name).read_text().splitlines()]


def write_rows(tmp_path, name, values):
    path = tmp_path / name
    path.write_text("".join(json.dumps(value, ensure_ascii=False) + "\n" for value in values))
    return path


def test_correct_replay_is_offline_and_not_a_quality_approval(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("offline evaluation attempted a network call")

    monkeypatch.setattr(socket, "create_connection", forbidden)
    report = evaluate(FIXTURES / "gold.jsonl", FIXTURES / "correct.jsonl")
    assert report["n"] >= 12
    assert len(report["operations"]) == 7
    assert {case["industry"] for case in report["cases"]} == {
        "CAFE", "RESTAURANT", "CONVENIENCE_STORE",
    }
    assert report["failed"] is False
    assert report["model_quality_verified"] is False
    assert report["human_review_pending_n"] == report["n"]
    # The revision is recorded when there is one to record; the file hashes identify the code anyway.
    assert report["code_sha"] == checkout_revision() and report["code_files_sha256"]
    assert len(report["dataset_sha256"]) == len(report["responses_sha256"]) == 64
    for stats in report["operations"].values():
        assert stats["usage_provided_n"] == 0
        assert all(value is None for value in stats["usage_totals"].values())
        assert stats["captured_latency"]["synthetic"] == {
            "n": 0, "p50_ms": None, "p95_ms": None,
        }


def test_mutants_separate_structure_failures_and_critical_gold_failures():
    report = evaluate(FIXTURES / "gold.jsonl", FIXTURES / "mutants.jsonl")
    indexed = {case["case_id"]: case for case in report["cases"]}
    # Root's production probability invariant rejects this before gold grading.
    assert indexed["jev-unknown"]["structure"] == "fail"
    assert indexed["jev-unknown"]["structure_error_code"] == "invalid_output"
    for case_id, check_id in [
        ("qa-valid-citation-wrong-time", "exact-spoken-time-and-count"),
        ("revision-ambiguous", "correction-outcome"),
        ("summary-cafe", "spoken-step"),
        ("draft-restaurant", "spoken-step"),
    ]:
        assert indexed[case_id]["structure"] == "pass"
        assert check_id in indexed[case_id]["critical_gold_failures"]
    assert indexed["revision-specific"]["structure"] == "fail"
    assert report["structure_failure_n"] > 0
    assert report["critical_gold_failure_n"] > 0
    assert report["failed"] is True


@pytest.mark.parametrize("mutation", [
    lambda case: case["expected"].update(unrecognised=True),
    lambda case: case["expected"]["checks"][0].update(unrecognised=True),
    lambda case: case["expected"]["checks"][0].update(kind="regex_semantic_approval"),
    lambda case: case.update(operation="unknown_operation"),
    lambda case: case["expected"].update(checks=[]),
    lambda case: case["expected"]["checks"][0].pop("value"),
])
def test_gold_contract_rejects_unknown_or_incomplete_checks(mutation):
    case = rows("gold.jsonl")[0]
    mutation(case)
    with pytest.raises(ValidationError):
        Case.model_validate(case)


def test_duplicate_case_and_response_ids_are_rejected(tmp_path):
    for filename, schema, key in [
        ("gold.jsonl", Case, "id"), ("correct.jsonl", Response, "case_id"),
    ]:
        value = rows(filename)[0]
        path = write_rows(tmp_path, filename, [value, value])
        with pytest.raises(ValueError, match="duplicate"):
            load_jsonl(path, schema, key)


def test_invalid_output_is_structure_failure_without_raw_text(tmp_path):
    captures = rows("correct.jsonl")
    secret = "secret-captured-private-answer"
    captures[0]["raw_output"] = secret
    report = evaluate(FIXTURES / "gold.jsonl", write_rows(tmp_path, "bad.jsonl", captures))
    assert report["structure_failure_n"] == 1
    assert secret not in json.dumps(report)
    assert "raw_output" not in json.dumps(report)


def test_missing_response_and_mutant_have_nonzero_cli_exit(tmp_path):
    captures = rows("correct.jsonl")[:-1]
    missing = write_rows(tmp_path, "missing.jsonl", captures)
    output = tmp_path / "report.json"
    base = ["--dataset", str(FIXTURES / "gold.jsonl"), "--output", str(output)]
    assert main([*base, "--responses", str(missing)]) == 1
    assert json.loads(output.read_text())["missing_response_n"] == 1
    assert main([*base, "--responses", str(FIXTURES / "mutants.jsonl")]) == 1
    assert main([*base, "--responses", str(FIXTURES / "correct.jsonl")]) == 0


def test_unknown_response_id_is_input_error(tmp_path):
    captures = rows("correct.jsonl")
    captures[0]["case_id"] = "unknown"
    with pytest.raises(ValueError, match="unknown case"):
        evaluate(FIXTURES / "gold.jsonl", write_rows(tmp_path, "unknown.jsonl", captures))


def test_capture_error_and_nullable_usage_are_explicit(tmp_path):
    captures = rows("correct.jsonl")
    first = captures[0]
    first.pop("raw_output")
    first["error"] = "timeout"
    report = evaluate(FIXTURES / "gold.jsonl", write_rows(tmp_path, "error.jsonl", captures))
    assert report["cases"][0]["structure_error_code"] == "timeout"
    first["raw_output"] = "{}"
    with pytest.raises(ValidationError):
        Response.model_validate(first)


def test_capture_statistics_separate_live_provenance_and_keep_missing_null(tmp_path):
    captures = rows("correct.jsonl")
    captures[0].update(elapsed_ms=10.0, usage={"input_tokens": 5, "output_tokens": 2})
    captures[1]["elapsed_ms"] = 50.0
    captures[1]["captured"]["provenance"] = "live"
    report = evaluate(FIXTURES / "gold.jsonl", write_rows(tmp_path, "stats.jsonl", captures))
    stats = report["operations"]["judge_sufficiency"]
    assert stats["captured_latency"]["synthetic"] == {
        "n": 1, "p50_ms": 10.0, "p95_ms": 10.0,
    }
    assert stats["captured_latency"]["live"] == {
        "n": 1, "p50_ms": 50.0, "p95_ms": 50.0,
    }
    assert stats["usage_totals"]["input_tokens"] == 5
    assert stats["usage_totals"]["cached_tokens"] is None
    assert report["model_quality_verified"] is False


def test_expected_unknown_time_is_not_silently_filled(tmp_path):
    captures = rows("correct.jsonl")
    response = next(row for row in captures if row["case_id"] == "summary-cafe")
    raw = json.loads(response["raw_output"])
    raw["structure"]["shifts"][0]["end_time"] = "06:00"
    raw["structure"]["missing_information"] = []
    response["raw_output"] = json.dumps(raw)
    report = evaluate(FIXTURES / "gold.jsonl", write_rows(tmp_path, "filled.jsonl", captures))
    result = next(case for case in report["cases"] if case["case_id"] == "summary-cafe")
    assert result["structure"] == "pass"
    assert "unknown-time" in result["critical_gold_failures"]


def test_unknown_citation_is_rejected_by_production_validator(tmp_path):
    captures = rows("correct.jsonl")
    response = next(row for row in captures if row["case_id"] == "qa-valid-citation-wrong-time")
    raw = json.loads(response["raw_output"])
    raw["citations"][0]["step_ids"] = ["unknown-step"]
    response["raw_output"] = json.dumps(raw)
    report = evaluate(FIXTURES / "gold.jsonl", write_rows(tmp_path, "citation.jsonl", captures))
    result = next(case for case in report["cases"] if case["case_id"] == response["case_id"])
    assert result["structure"] == "fail"
    assert result["gold_status"] == "skipped_structure_failure"
    assert result["critical_gold_failures"] == []


def test_noncritical_failure_is_reported_without_critical_cli_failure(tmp_path):
    gold = rows("gold.jsonl")
    gold[0]["expected"]["checks"][0].update(value=True, critical=False)
    report = evaluate(write_rows(tmp_path, "noncritical.jsonl", gold), FIXTURES / "correct.jsonl")
    assert report["cases"][0]["noncritical_gold_failures"] == ["outcome"]
    assert report["cases"][0]["gold_status"] == "fail"
    assert report["critical_gold_failure_n"] == 0
    assert report["failed"] is False


def test_guidance_semantic_fixtures_detect_grounding_mutants_offline():
    correct = evaluate(FIXTURES / "guidance-gold.jsonl", FIXTURES / "guidance-correct.jsonl")
    mutants = evaluate(FIXTURES / "guidance-gold.jsonl", FIXTURES / "guidance-mutants.jsonl")
    assert correct["n"] == 10 and correct["failed"] is False
    assert correct["model_quality_verified"] is False
    assert correct["human_review_pending_n"] == 10
    assert mutants["failed"] is True
    indexed = {case["case_id"]: case for case in mutants["cases"]}
    expected_mutants = {
        "cards-examples-only", "cards-two-real-tasks", "cards-partial-detail",
        "cards-list-question-no-current", "photos-unrelated", "photos-unnecessary",
        "photos-distorted-section", "summary-depth-limit", "summary-no-shifts",
        "revision-rename-preserves-id",
    }
    assert set(indexed) == expected_mutants
    # An invented photo section now fails production reference validation before gold.
    # The remaining nine still require their specific meaning failure; a generic parser
    # failure would hide a broken oracle (especially the rename identity case below).
    distorted = indexed["photos-distorted-section"]
    assert distorted["structure"] == "fail"
    assert distorted["structure_error_code"] == "invalid_output"
    assert distorted["gold_status"] == "skipped_structure_failure"
    assert distorted["critical_gold_failures"] == []
    for case_id in expected_mutants - {"photos-distorted-section"}:
        case = indexed[case_id]
        assert case["structure"] == "pass", case_id
        assert case["gold_status"] == "fail", case_id
        assert case["critical_gold_failures"] == ["grounded-meaning"], case_id
    assert mutants["structure_failure_n"] == 1
    assert mutants["critical_gold_failure_n"] == 9
    assert mutants["structure_failure_n"] + mutants["critical_gold_failure_n"] == len(expected_mutants)


def test_rename_identity_mutant_fails_the_existing_id_gold_check():
    case_id = "revision-rename-preserves-id"
    gold = next(case for case in rows("guidance-gold.jsonl") if case["id"] == case_id)
    check = gold["expected"]["checks"][0]
    original_id = gold["request"]["current"]["sections"][0]["id"]
    assert check["id"] == "grounded-meaning"
    assert check["kind"] == "equals" and check["path"] == "structure.sections.0.id"
    assert check["value"] == original_id
    mutant = next(case for case in rows("guidance-mutants.jsonl") if case["case_id"] == case_id)
    assert json.loads(mutant["raw_output"])["structure"]["sections"][0]["ref"] == "new-1"
    report = evaluate(FIXTURES / "guidance-gold.jsonl", FIXTURES / "guidance-mutants.jsonl")
    result = next(case for case in report["cases"] if case["case_id"] == case_id)
    # A new-item ref is structurally valid. Replacing the renamed item's ID violates
    # this instruction's meaning, caught by gold, not by a deletion-command grammar.
    assert result["structure"] == "pass"
    assert result["gold_status"] == "fail"
    assert result["critical_gold_failures"] == [check["id"]]

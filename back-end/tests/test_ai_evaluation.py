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
    assert len(report["operations"]) == 6
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
        ("summary-cafe", "cites-evidence"),  # the invented step had no citation
        ("summary-restaurant", "cites-evidence"),
    ]:
        assert indexed[case_id]["structure"] == "pass"
        assert check_id in indexed[case_id]["critical_gold_failures"]
    # A same-ID uncited mutation keeps the reviewed original in a draft, even in old
    # empty-evidence captures; a valid citation's semantics are checked separately. A correction
    # cannot report a restored original as applied, so there it is a structure failure (retried).
    assert indexed["draft-restaurant"]["structure"] == "pass"
    assert indexed["draft-restaurant"]["gold_status"] == "pass"
    assert indexed["draft-restaurant"]["critical_gold_failures"] == []
    assert indexed["revision-specific"]["structure"] == "fail"
    assert indexed["revision-specific"]["structure_error_code"] == "invalid_output"
    assert report["structure_failure_n"] > 0
    assert report["critical_gold_failure_n"] > 0
    assert report["failed"] is True


@pytest.mark.parametrize("citation", ["owner#1", "unknown#1"])
def test_draft_citation_validity_and_semantic_gold_are_independent(tmp_path, citation):
    case = next(row for row in rows("gold.jsonl") if row["id"] == "draft-restaurant")
    case["request"]["evidence"] = [{"id": "owner#1", "intent_key": "EQUIPMENT",
                                  "text": "그릇을 선반에 종류별로 쌓아요."}]
    captured = next(row for row in rows("mutants.jsonl") if row["case_id"] == case["id"])
    raw = json.loads(captured["raw_output"])
    raw["structure"]["sections"][0]["steps"][0]["evidence_ids"] = [citation]
    captured["raw_output"] = json.dumps(raw, ensure_ascii=False)
    report = evaluate(write_rows(tmp_path, "gold.jsonl", [case]),
                      write_rows(tmp_path, "captured.jsonl", [captured]))
    [result] = report["cases"]
    if citation == "owner#1":
        assert result["structure"] == "pass"
        assert result["critical_gold_failures"] == ["spoken-step"]  # real ID, wrong factual action
    else:
        assert result["structure"] == "fail" and result["structure_error_code"] == "invalid_output"
        assert result["gold_status"] == "skipped_structure_failure"
    assert report["failed"] and report["model_quality_verified"] is False
    assert result["human_review"] == "pending"


def test_citation_does_not_permit_a_revision_outside_the_selected_target(tmp_path):
    case = next(row for row in rows("gold.jsonl") if row["id"] == "revision-specific")
    case["request"]["evidence"] = [{"id": "owner#1", "intent_key": "WORK_STRUCTURE",
                                  "text": case["request"]["instruction"]}]
    captured = next(row for row in rows("mutants.jsonl") if row["case_id"] == case["id"])
    raw = json.loads(captured["raw_output"])
    raw["structure"]["shifts"][0]["evidence_ids"] = ["owner#1"]
    raw["structure"]["sections"][0]["steps"][0]["evidence_ids"] = ["owner#1"]
    captured["raw_output"] = json.dumps(raw, ensure_ascii=False)
    report = evaluate(write_rows(tmp_path, "gold.jsonl", [case]),
                      write_rows(tmp_path, "captured.jsonl", [captured]))
    [result] = report["cases"]
    assert result["structure"] == "fail" and result["structure_error_code"] == "invalid_output"
    assert result["gold_status"] == "skipped_structure_failure" and report["failed"]


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


def test_evidence_check_needs_a_grounded_case_with_evidence():
    grounded = next(case for case in rows("gold.jsonl") if case["id"] == "summary-cafe")
    Case.model_validate(grounded)
    without = json.loads(json.dumps(grounded))
    without["request"].pop("evidence")
    jev = rows("gold.jsonl")[0]
    jev["expected"]["checks"].append({"id": "cites", "kind": "evidence_cited"})
    bad_value = json.loads(json.dumps(grounded))
    bad_value["expected"]["checks"][-1]["value"] = "t1#1"
    for case in (without, jev, bad_value):
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

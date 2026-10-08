"""The strict output schemas stay within OpenAI's documented strict-mode subset and agree with
the pydantic parsers that re-validate model output."""

import pytest
from jsonschema import Draft202012Validator
from pydantic import ValidationError

from app.ai.schemas import OUTPUTS

ALLOWED_KEYWORDS = {"type", "properties", "required", "additionalProperties", "items", "enum", "description"}


def walk(schema, path="$"):
    yield path, schema
    for name, child in schema.get("properties", {}).items():
        yield from walk(child, f"{path}.{name}")
    if "items" in schema:
        yield from walk(schema["items"], f"{path}[]")


@pytest.mark.parametrize("operation", sorted(OUTPUTS))
def test_schema_is_strict_mode_compatible(operation):
    _name, schema, _parser = OUTPUTS[operation]
    assert schema["type"] == "object"  # the root must be an object
    for path, node in walk(schema):
        assert set(node) <= ALLOWED_KEYWORDS, (path, set(node) - ALLOWED_KEYWORDS)
        if node.get("type") == "object":
            assert node["additionalProperties"] is False, path
            assert node["required"] == list(node["properties"]), path


SAMPLES = {
    "judge_sufficiency": {"sufficient": False, "probability": 0.3, "missing_aspects": ["마감 순서"],
                          "not_applicable_probability": None, "not_applicable_confirmed_probability": None},
    "generate_question": {"question": "기계는 어떤 순서로 닦나요?", "guidance": None,
                          "examples": [{"label": "커피 머신", "description": None}]},
    "summarize_intent": {"summary": "요약", "structure": {
        "shifts": [{"ref": "new-1", "name": "오전", "start_time": "09:00", "end_time": None,
                    "ends_next_day": None, "evidence_ids": ["t1#1"]}],
        "sections": [{"ref": "new-2", "category": "SHIFT_TASK", "shift_ref": "new-1", "title": "오픈",
                      "steps": [{"ref": "new-3", "instruction": "불을 켜요", "checklist_item": True,
                                 "evidence_ids": ["t1#2"]}]}],
        "missing_information": [{"target": "SHIFT", "target_ref": "new-1", "field": "endTime",
                                 "description": "종료 시간 미정"}],
    }},
    "revise_structure": {"outcome": "NO_CHANGE", "summary": None, "structure": {
        "shifts": [], "sections": [], "missing_information": []}},
    "compose_draft": {"structure": {"shifts": [], "sections": [], "missing_information": []}},
    "answer_question": {"outcome": "NEEDS_OWNER", "answer": "점주 확인이 필요해요.", "citations": []},
    "write_section_from_media": {"outcome": "APPLIED", "structure": {
        "shifts": [], "sections": [{"ref": "s1", "category": "COMMON_TASK", "shift_ref": None, "title": "재고",
                                    "steps": [{"ref": "new-1", "instruction": "먼저 들어온 우유를 앞에 둬요",
                                               "checklist_item": False,
                                               "evidence_ids": ["media:00000000-0000-4000-8000-000000000001"]}]}],
        "missing_information": []},
        "removed_steps": [{"ref": "t1", "evidence_ids": ["media:00000000-0000-4000-8000-000000000001#transcript"]}]},
}


@pytest.mark.parametrize("operation", sorted(OUTPUTS))
def test_schema_and_parser_accept_the_same_valid_output(operation):
    _name, schema, parser = OUTPUTS[operation]
    Draft202012Validator(schema).validate(SAMPLES[operation])
    parser.model_validate(SAMPLES[operation])


@pytest.mark.parametrize("operation", sorted(OUTPUTS))
def test_parser_rejects_extra_and_missing_fields(operation):
    _name, _schema, parser = OUTPUTS[operation]
    with pytest.raises(ValidationError):
        parser.model_validate({**SAMPLES[operation], "injected": "x"})
    first = next(iter(SAMPLES[operation]))
    with pytest.raises(ValidationError):
        parser.model_validate({k: v for k, v in SAMPLES[operation].items() if k != first})


@pytest.mark.parametrize("field,value", [
    ("question", ""), ("question", "가" * 2001),
    ("examples", [{"label": "x", "description": None}] * 51),  # a broken output, not a long one
])
def test_parser_enforces_lengths_the_schema_cannot_express(field, value):
    _name, _schema, parser = OUTPUTS["generate_question"]
    parser.model_validate(SAMPLES["generate_question"])  # the base sample is valid
    with pytest.raises(ValidationError):
        parser.model_validate({**SAMPLES["generate_question"], field: value})


@pytest.mark.parametrize("field,value", [
    ("guidance", "가" * 2001), ("guidance", ""),
    ("examples", [{"label": "", "description": None}]),
    ("examples", [{"label": "가" * 201, "description": "나" * 1001}]),
    ("examples", [{"label": "x", "description": None}] * 50),
])
def test_parser_leaves_guidance_limits_to_the_provider(field, value):
    """Guidance is decoration: oversized parts are dropped by the provider, not a parse failure."""
    _name, _schema, parser = OUTPUTS["generate_question"]
    parser.model_validate({**SAMPLES["generate_question"], field: value})


def test_parser_rejects_out_of_range_probability_and_long_aspects():
    _name, _schema, parser = OUTPUTS["judge_sufficiency"]
    for bad in ({"probability": 1.01}, {"probability": -0.1}, {"missing_aspects": ["가" * 201]},
                {"missing_aspects": ["a"] * 6}):
        with pytest.raises(ValidationError):
            parser.model_validate({**SAMPLES["judge_sufficiency"], **bad})

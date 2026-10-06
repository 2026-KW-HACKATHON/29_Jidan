# Offline AI replay 평가

이 도구는 DB와 네트워크 없이 캡처된 raw JSON을 `AiProvider`의 운영 parser와 validator에
통과시키고, 별도의 합성 정답 검사를 수행한다. 실제 모델을 생성하거나 호출하는 live mode는 없다.
`AI_PROVIDER`, API 키, DB 환경변수를 읽지 않는다. 2026-10-06 AI 기능 점검에서 지적된 평가 공백
(F01~F07)을 재현 가능한 계약으로 구체화한다.

후보 코드의 충분성 probability 불일치와 타깃 밖 정정은 운영 validator에서 구조 실패로 분류한다.
유효 인용을 붙인 틀린 시각, 모호한 지시의 임의 APPLIED, 요약·초안의 사실 누락은 구조 검증이
통과하더라도 critical gold 검사에서 실패한다. 구조 검사 통과는 답변 의미의 충실도 보장이 아니다.

## 실행

`back-end/`에서 저장소의 Python 환경을 사용한다. 아래 실행은 캡처 replay만 수행한다.

```sh
python -m evals.runner --dataset evals/fixtures/gold.jsonl --responses evals/fixtures/correct.jsonl --output /tmp/jidan-ai-correct.json
python -m evals.runner --dataset evals/fixtures/gold.jsonl --responses evals/fixtures/mutants.jsonl --output /tmp/jidan-ai-mutants.json
python -m pytest tests/test_ai_evaluation.py
```

정상 fixture는 exit 0, 변이 fixture는 exit 1을 기대한다. 구조 실패·critical gold 실패·누락 응답은
exit 1이고 JSONL 계약 오류·중복 ID·알 수 없는 operation/응답 case ID는 exit 2다.
비critical 검사 실패는 보고하되 exit 1 조건에 포함하지 않는다. 원문 오류 설명을 CLI에 출력하지 않는다.
`tests/test_ai_evaluation.py`가 정상·변이 fixture의 exit code와 위 분류를 회귀 시험으로 고정한다.

## 데이터 및 grading 계약

`evals/fixtures/`에는 카페·음식점·편의점의 합성 사례 14개, 6개 operation이 있다.
`gold.jsonl`, `correct.jsonl`, `mutants.jsonl`은 개발용 회귀 fixture이며 실제 모델 캡처가 아니다.
Notion/Figma 최신 원문을 사용했다고 주장하지 않는다.

Case는 `id`, `operation`, `industry`, 운영 request 모델의 JSON 입력 `request`, `expected`,
`provenance=synthetic`, `dataset_version`을 가진다. `expected`는 비어 있지 않은 `checks`와
`human_review_required`로 구성한다. unknown field와 unknown check kind를 거절한다.
각 check의 `id`는 한 case 안에서 고유하고 `critical` 기본값은 true다.

| kind | 입력 | 판정 |
| --- | --- | --- |
| equals | path, value 필수 | JSON 타입과 값의 완전 일치 |
| contains_item | path, value 필수 | JSON 배열 안에 타입·값이 같은 원소 존재 |
| probability_consistent | path/value 금지 | 충분성 bool과 probability >= 0.5 일치 |
| preserve_scope | path/value 금지 | 정정 target 외 기존 shift/section 내용 보존; MANUAL은 기존 전체 보존 |

`path`는 결과 모델의 snake_case 필드에 점으로 접근하고 배열은 숫자 인덱스를 쓴다.
예: `structure.shifts.0.end_time`, `citations.0.step_ids.0`.
없어진 값·잘못된 경로는 해당 gold 실패다. null은 명시 `value: null`로 검사한다.
운영 validator가 만든 새 UUID는 임의이므로 summary의 신규 ID를 exact gold로 비교하지 않는다.
초안/정정의 기존 ID와 QA 인용 ID는 정확하게 비교한다. `preserve_scope`는 신규 항목 전체의
의미를 승인하지 않는다. MANUAL 전체 변경이 필요한 gold는 명시 delta/equals로 별도 설계한다.

각 response는 `case_id`, `raw_output`(JSON 문자열) 또는 `error`(운영 AiErrorCode) 중 정확히 하나,
`captured`의 provider/model/prompt_version/reasoning_effort/provenance(synthetic 또는 live),
`elapsed_ms`(null 가능), `usage`(null 가능)를 가진다. usage 제공 시 input/output token은 필수,
cached/reasoning token은 null 가능하며 음수·NaN·무한대를 거절한다.
provenance=live는 기존 캡처의 출처 표시에 불과하며 이 runner가 호출하지 않는다.

## 의미와 보고서의 한계

전체 문장 exact gold는 지정 fixture의 수치·절차·결과 보존을 확인하는 회귀 oracle이다.
다른 올바른 표현도 exact 검사에서 실패할 수 있다. regex나 단어 포함만으로 일반 semantic 품질을
승인하지 않으며 결과는 항상 `model_quality_verified=false`다. 모든 제공 사례의 사람 검토는
pending으로 남긴다. 실제 의미 평가는 source turn의 원자 사실/근거, 부정/조건/순서/예외,
인용의 관련성과 함의, 모호성·미확정 보존을 사람이 검토해야 한다. 합성 fixture 통과를 현재 모델의
품질·실제 지연·비용으로 쓰지 않는다. 향후 실제 캡처 평가도 표본 수/업종/모델/prompt와
holdout·반복 조건을 명시해야 한다.

보고서는 Git code SHA, 실제 runner 및 `app/ai/*.py` 파일 SHA-256(미커밋 코드 재현용),
dataset/response byte hash, 사례 수, 구조 실패·누락·critical gold 실패·사람검토 pending 수를 포함한다.
변경하지 않은 파일과 전체 request/raw output/prompt/환경변수/키는 출력하지 않는다.
캡처 config는 출처 추적용 메타데이터이므로 개인정보나 키를 넣지 않는다.
실패 사례에는 case/check ID와 분류만 저장하고 원문을 기본 저장하지 않는다.

연산별 latency는 캡처 elapsed_ms가 있는 표본만 N과 nearest-rank p50/p95를 계산하고
synthetic/live를 분리한다. runner 자체 실행 속도가 모델 지연으로 둔갑하지 않는다.
usage 합계는 제공 값만 합치며 전혀 제공되지 않은 필드는 null, 누락 N을 별도 보고한다.
일부 usage만 있을 때 합계는 부분 합계이며 전체 비용이 아니다. 가격 환산은 하지 않는다.

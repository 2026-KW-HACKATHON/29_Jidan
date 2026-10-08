# 복구된 인터뷰 구현을 기준으로 한 원자 작업 계획

현재 빌드에서 확인된 계약 위반의 복구와, 승인된 Structured Output 원자 작업 및 최종 검증 상태를 구분한다.
사용자가 제공한 출력 타입·실행 흐름·OpenAPI·ERD가 기준이며, 기존 AI 내부 구조의 존속이 기준이 아니다.
이 문서는 이전 35개 작업과 프론트 5개 확인 목록을 대체한다. 과거 체크박스를 실행 기준으로 재사용하지 않는다.

**사용자의 실행 승인에 따라 T1~T7 구현과 검증 코드 작성을 마쳤다.**
검증 결과는 아래 실행 기록에 남긴다. 후속 승인에 따라 원자 커밋을 푸시하고 back-end/dev로 병합한다. 사용자는 CI를 로컬에서 실행하고 원격 CI를 생략하도록 지시했다. 자동 dev 배포도 같은 워크플로에 있어 함께 생략된다.

## 1. 현재 복구 상태

기준 워크트리는 `worktrees/interview-structured-output-20261008`이다.
복구 전 통합 지점은 `49f6712`, 최초 구현 비교 지점은 `d35c26d`다.
아래 완료 표시는 확인된 복구만 뜻하며 전체 AI 품질이나 신규 사진 추천의 완료를 뜻하지 않는다.
이미 실행한 검사는 중간 기록이며 전체 완료 판정이 아니다. 이후 검사는 모든 구현과 필요한 검증 코드 작성을 마친 뒤 수행한다.

| 확인된 문제 | 복구 결과 | 근거와 한계 |
| --- | --- | --- |
| 정정 삭제를 한정된 한국어 정규식 문구로만 허용 | `eeaf19a`: 자유로운 명시적 정정의 문구 제한 제거 | ID·사진·무관한 내용 보존 검증은 유지. 실제 모델의 모든 자연어 이해를 입증한 것은 아님 |
| 과거 A/B 카드와 최신 합성 C의 귀속을 모두 후보로 삼아 동일 C에 계속 새 UUID 부여 | `13af2c6`: 항목의 최신 저장 카드 귀속을 우선하여 동일 C의 ID 유지 | 합성 후 재생성·재조회에서 카드/항목 ID 유지 회귀 포함 |
| 답변 처리 중 과거 질문 스냅샷이 현재 안내 flag에 따라 달라짐 | `632ec03`: 답변 접수 당시 질문 안내 스냅샷 보존 | 기존 ANSWER의 nullable 안내 필드에 당시 공개값을 복사하고 원 QUESTION 기본 필드와 결합. legacy는 당시 설정을 추정하지 않고 optional 필드 생략 |

앞의 두 복구에 대해 단위 81건, 카드 18건, 실제 HTTP·격리 MySQL 7건(46.207초), SQL 정렬 보강 후 영향 HTTP 1건 재실행(4.29초)의 통과 기록이 있다.
세 번째 복구는 단위 23건(MySQL 9건, skip 0), 관련 HTTP 6건(80.97초), 최종 보완 HTTP 4건(77.08초, skip 0)이 통과했다.
새 모델·컬럼·migration·task payload를 추가하지 않았다. 독립 `storage_contract_audit`가 원 QUESTION 보존, 재시작·ERROR·retry·replay·대용량·rollback과 최종 XML을 검토하여 차단 사항이 없음을 확인했다.
증거는 `.local/test-results/`의 `contract-repair-http.xml`, `contract-review-order.xml`, `snapshot-before.xml`(복구 전 실패), `snapshot-unit.xml`, `snapshot-http.xml`, `snapshot-http-final.xml`이다.
`c9f959e`는 R01 정정 복구에 맞춘 검증 분류 보정이다. 기존 evaluation oracle가 정규식 gate의 의미 오류를 구조 오류로 세던 기대값을 `test_ai_evaluation.py`에서 수정하고, 10개 mutant가 각각 의미 gold에서 실패함을 확인했다. 추가 API 위반 복구로 세지 않는다. 비DB 평가 모듈 19건(skip 0)과 독립 저장 계약 검토가 완료됐다.
제공된 `run-e2e.sh` 전체 검사는 사용자 지시로 중단되어 완료 결과가 없다. 중단 전 `632ec03` 이미지에서 위 분류 보정 전의 알려진 1건 실패가 확인됐으며 전체 통과 여부는 판정하지 않는다. 중간 산출물은 `.local/test-results/contract-repair-full`, 로그는 `.local/contract-repair/full-e2e.log`다.

미통합 0044와 사진 추천 WIP는 복구된 기준 코드나 완료 근거에 포함하지 않는다.
PR #175의 변경·종료·병합·리뷰 조치는 이 계획의 작업이 아니다.

## 2. 이미 제공된 기준과 재사용 범위

문서 경로와 구현 경로는 별도 표기가 없으면 `back-end/` 기준이다.

- [원본 아키텍처](지단manual_architecture.md): §1 책임 분리, §2 QuestionOutput/QuestionCard, §3 AnswerEvaluation/ReviewOutput, §4 PhotoSuggestionOutput, §5 사진 보존.
- [OpenAPI](../openapi.yaml): ManualInterviewQuestion, ManualGuidanceCard, ManualGuidancePhotoTarget, ManualInterviewReview, ManualContent와 기존 인터뷰·검토·초안 operation.
- [ERD](erd/manual.md): 테이블과 제약, 인텐트 검토와 최종 게시, 평가와 장애 복구의 실행 제약.
- [실행 흐름](erd/ai-interview-flow.md): 질문별 평가, depth 0~5, 같은 단위 재시도, 실패 시 진행·저장 보존.
- [원래 API 설계](manual-interview-design.md): 1~209행의 정정·ID·사진·질문 스냅샷 계약. 이번 작업에서 추가된 212행 이후 설명을 원본 요구의 근거로 역인용하지 않는다.
- [재평가](interview-contract-reassessment.md): 이전 계획의 오류를 설명하는 평가 기록이며 새로운 타입·관계·흐름의 원천이 아니다.

`RawStructure`, `StructureSnapshot`, `new-N`의 존재만으로 API 위반이라고 판정하지 않는다.
반대로 이미 존재한다는 이유로 보존을 요구하지도 않는다. 아래 operation에서 제공된 출력과 기존 API/DB 표현을 따르도록 필요한 AI 코드를 교체한다.
별도 대응표·콘텐츠 중간 스키마·InterviewSubject·새 흐름을 만드는 선행 작업은 없다.
테이블·열·migration 추가 또는 API 계약 변경을 미리 결정한 작업도 없다.
기존 계약으로 구현이 불가능하다는 구체적 사례가 발견되면 그 사례를 먼저 보고하며 저장 설계를 임의로 확대하지 않는다.

다음은 이미 계약을 만족하는 구현을 재사용하고, 변경 영향이 있는 경로에서 회귀를 확인할 조건이다.

- 인증·점주/매장/세션 귀속, revision·멱등성, 잠금·원자 저장·늦은 결과 차단.
- 질문별 평가, depth 상한, 같은 작업의 재시도와 fallback, 마지막 성공 콘텐츠 보존.
- ready_content, 확인 이력, generation_input_snapshot과 기존 사진 연결·이름·설명·순서 보존.
- 기존 업로드·검토 photos·초안·게시 API. 사진 없이 계속하기는 삭제 명령이 아니며 초안은 자동 게시하지 않음.

이 조건을 별도 신규 구현 태스크로 재작성하지 않는다. 실제 위반이 발견된 경우에만 해당 operation의 최소 수정에 포함한다.
사진 추천은 **실제 다음 미답변 질문이 있을 때만 전달하고 마지막 인텐트는 기존 수동 첨부를 사용한다**는 승인된 한정 범위를 유지한다.

## 3. 승인된 원자 작업

각 작업은 한 operation의 구체적인 동작과 필요한 검증 코드의 원자 경계다. 아래 합격·HTTP/DB 항목은 모든 작업 완성 뒤 실행할 검증 명세이며 작업마다 검사하지 않는다.
구현과 검증 코드는 operation별로 분리하되, 최종 검사·독립 재점검을 마친 뒤 해당 구현과 검증 코드를 같은 원자 커밋으로 확정한다. 작업 중 검사 완료를 전제로 커밋하지 않는다.
아래 경로는 예상 영향 범위이며 전부 수정하라는 뜻이 아니다. 이미 충족한 항목은 근거를 남겨 제외하고 같은 기능을 다시 만들지 않는다.
공통 파일을 건드려도 다른 operation의 변경을 묶지 않는다. API/ERD·프론트·배포 변경을 섞지 않는다.
각 operation은 제공된 출력 타입·API·ERD만을 기준으로 판정한다. 독립적인 중간 콘텐츠 계약의 보존은 합격 조건이 아니다.
그 기준과 어긋나는 AI 필드·참조 규칙·정책은 제거·대체할 수 있으며, 이를 대신할 임의 타입이나 스키마를 새로 설계하지 않는다.
T2/T4/T5의 기존 평가·정정·초안 기반이 계약을 이미 충족하면 재사용으로 종료한다. 미충족 동작이 확인된 부분만 코드 변경/커밋 대상으로 삼는다.

### T1. 질문 생성에서 실제 업무와 예시의 의미 유지

- 근거: 원본 §2; OpenAPI ManualInterviewQuestion.guidanceCards; 원래 설계의 질문 안내 카드.
- 코드 결과: QuestionOutput을 현재 인텐트·누적 점주 답변·평가·같은 카드 이력으로 생성하고 서버 검증을 거쳐 기존 질문 표현으로 저장한다. 일반 예시를 업무로 확정하거나 답변 접수만으로 COMPLETED로 만들지 않는다.
- 영향 파일: `app/ai/{schemas,prompts,openai_provider}.py`, `app/interview/{flow,cards,tasks}.py`; 관련 `tests/test_ai_guidance.py`, `tests/test_interview_cards.py`, `tests/test_interview_guidance.py`.
- 의존: 현재 복구 완료. T2~T7과 별개로 리뷰 가능.
- 합격: 제공된 AI QuestionCard의 LIST 항목은 status=null이며 공개 API ManualGuidanceList 항목에는 기존 additionalProperties:false 계약대로 status 필드를 보내지 않는다. 진행 카드와 CURRENT 각각 최대 하나, 전체 목록 질문의 CURRENT 없음, 같은 업무의 순서·표현 변경/재등장 ID 유지, 다른 세션 또는 허용된 이력 밖의 item ID 거부를 검증하며 카드 간 정상 합성은 허용한다. 카드만 오류이면 제외하고 유효 질문은 보존한다.
- HTTP·DB: 질문 재조회와 답변 접수/평가 ERROR에서 질문·카드·접수 스냅샷 일치 및 실패 시 보존을 확인한다.
- 커밋 경계: 질문 operation의 입력·출력·검증·저장 변경과 해당 회귀만 포함.

### T2. 답변 평가의 부족 정보를 다음 처리에 전달

- 근거: 원본 §3 AnswerEvaluation; 실행 흐름의 질문별 평가와 depth 한도; ERD 평가 입력/시도 이력.
- 코드 결과: 기존 sufficient/probability/missing_aspects 결과를 같은 평가 입력에 귀속시키고, 후속 질문과 한도 종료 요약에 부족 정보를 빠뜨리지 않고 전달한다.
- 영향 파일: `app/ai/{prompts,openai_provider}.py`, `app/interview/{flow,tasks}.py`; `tests/test_ai_provider.py`, `tests/test_interview_api.py`, `e2e/test_interview_evaluation_http.py`.
- 의존: 현재 복구 완료. T1의 카드 표현을 변경하는 커밋과 분리.
- 합격: 충분한 답변은 다음 인텐트, 부족한 답변은 같은 인텐트의 한 질문, depth 5 부족은 NEEDS_DETAIL과 부족 정보 보존으로 이어진다. 재시도는 저장된 같은 답변을 사용한다.
- HTTP·DB: 실제 서버에서 depth 경계·평가 실패/재시도를 확인하고 별도 DB 연결로 기존 답변·평가 시도·진행 단일 적용을 확인한다.
- 커밋 경계: 평가 정보 전달의 미충족 부분과 회귀만 포함. 이미 맞는 진행 엔진·재시도 기반은 재구현하지 않는다.

### T3. 이해 요약을 제공된 ReviewOutput으로 완성

- 근거: 원본 §3 ReviewOutput와 기존 Shift/Section/MissingInformation 재사용; OpenAPI ManualInterviewReview; ERD ready_content.
- 코드 결과: summary와 structure.shifts/sections/missing_information을 기존 콘텐츠 표현으로 검증·저장하고, 모르는 값은 null/빈 배열과 대상별 미확정 정보로 보존한다.
- 영향 파일: `app/ai/{schemas,prompts,openai_provider,validation}.py`, `app/interview/{tasks,content}.py`; `tests/test_ai_schemas.py`, `tests/test_interview_reviews.py`.
- 의존: T2의 부족 정보 전달. 별도 콘텐츠 스키마나 매핑 문서 생성은 의존성이 아니다.
- 합격: 확보하지 못한 시간·근무조·섹션·절차를 추측하지 않는다. 누락 표시와 실제 null/빈 값의 대상이 일치하며 잘못된 참조·중복 ID를 거절한다.
- HTTP·DB: 최초 요약 및 재시도 후 GET 검토와 ready_content의 내용·ID가 일치하고 실패 중 마지막 READY 내용·사진이 유지된다.
- 커밋 경계: 요약 operation에 필요한 AI 교체/변환과 회귀만 포함. 기존 정정·초안 경로는 호환 회귀로 보호한다.

### T4. 자연어 정정을 기존 내용의 제한된 변경으로 적용

- 근거: 원본 §5; 원래 설계의 생성 완료 초안 음성 정정; ERD 검토 정정·사진 보존.
- 코드 결과: 복구된 자유 발화 정정 경로에서 대상·수정 내용만 바꾸고 기존 ID·무관한 내용·사진을 유지하도록 AI 정정 출력을 맞춘다. 명시적 섹션 삭제와 모호한 발화를 구분한다.
- 영향 파일: `app/ai/{prompts,openai_provider,validation}.py`, `app/interview/{tasks,content}.py`, `app/manual_corrections.py`; `tests/test_ai_revision_identity.py`, `tests/test_interview_reviews.py`, `e2e/test_correction_freeform_http.py`.
- 의존: T3. eeaf19a의 문구 제한 제거를 새 구현으로 다시 세지 않는다.
- 합격: 표현이 다른 명시적 삭제를 처리한다. 검토 정정의 적용 불가 결과는 현재 내용·확인을 유지하는 기존 처리, 실행 실패는 기존 검토 ERROR/AI_PROCESSING_FAILED를 따른다. 초안 정정의 불명확한 대상/의도는 CORRECTION_CLARIFICATION_REQUIRED, 연결 충돌은 MANUAL_REFERENCE_CONFLICT를 따른다.
- 이름·절차 정정에서 동일 섹션 ID와 사진을 유지하며 삭제한 섹션 사진을 다른 섹션으로 이동하지 않는다. 검토 API에 초안 전용 오류를 새로 노출하지 않는다.
- HTTP·DB: 검토 정정/초안 정정의 성공·실패·같은 내용 재시도를 검사하고 마지막 저장본·다른 참조의 사진 보존을 독립 조회한다.
- 커밋 경계: 정정 operation의 AI 의미·범위 검증과 회귀. 검토/초안의 기존 트랜잭션 기반은 그대로 사용한다.

### T5. 선택된 검토 내용으로 최종 초안 생성

- 근거: 원본 §1 초안 생성·§5; OpenAPI ManualContent; ERD generation_input_snapshot과 사진 승계.
- 코드 결과: 선택된 검토 revision의 불변 입력만으로 최종 콘텐츠를 생성하고 같은 근무조·섹션 ID, 미확정 정보와 사진을 기존 초안에 이어받는다.
- 영향 파일: `app/ai/{schemas,prompts,openai_provider,validation}.py`, `app/interview/drafting.py`; `tests/test_interview_reviews.py`, 기존 초안 생성 테스트.
- 의존: T3, T4. 사진 추천 T6/T7은 초안 생성의 선행 조건이 아니다.
- 합격: 생성 이후 수정된 검토를 섞지 않고, 같은 섹션 사진의 mediaId/title/caption/순서를 유지한다. 검토에서 없는 사실을 초안에 추가하지 않으며 부족 정보를 임의 해소하지 않는다.
- HTTP·DB: completion → 초안 조회에서 선택 revision과 저장 콘텐츠·사진을 비교하고 실패/재시도의 마지막 성공본 보존을 확인한다. DRAFT이며 자동 게시되지 않는다.
- 커밋 경계: 초안 operation의 AI 출력·검증 변경과 회귀. 기존 snapshot 예약/게시 로직을 새로 만들지 않는다.

### T6. 저장된 요약을 입력으로 사진 추천 생성

- 근거: 원본 §4 PhotoSuggestionOutput; OpenAPI ManualGuidancePhotos/ManualGuidancePhotoTarget.
- 코드 결과: 저장 완료된 요약·섹션으로 suggestions를 생성하고 해당 검토의 실제 sectionId만 통과시킨다. 추천 없음은 빈 배열이며 별도 helpful이나 새로운 콘텐츠 구조를 추가하지 않는다.
- 영향 파일: `app/ai/{schemas,contracts,prompts,provider,openai_provider}.py`; `tests/test_ai_photo_suggestions.py`.
- 의존: T3. 미통합 사진 WIP/0044의 반입은 의존성이 아니다.
- 합격: 유효한 같은 검토 섹션·추천 없음·다른 세션/삭제/미존재 섹션·잘못된 출력/timeout을 검증한다. 추천에 사진 바이트·저장소 경로를 입력하지 않는다.
- 최종 검사 경계: T6의 provider 시험은 추천 생성/검증까지만 입증한다. 모든 구현 완료 뒤 같은 최종 검사 단계에서 T7의 실제 HTTP 전달·DB 보존을 별도로 검증하며 provider 통과만으로 사용자 흐름 완료를 판정하지 않는다.
- 커밋 경계: 사진 추천 AI operation과 테스트만 포함. 질문 생성의 두 카드 유형에 PHOTO_SUGGESTIONS를 섞지 않는다.

### T7. 사진 추천을 기존 질문 카드와 첨부 API에 연결

- 근거: 원본 §4~5; OpenAPI guidanceCards/attachmentTarget; 원래 설계의 사진 추천과 답변 처리 중 질문 복원; 승인된 다음 실제 질문 한정.
- 코드 결과: 유효한 추천에 서버 ID와 기존 attachmentTarget을 부여해 실제 다음 미답변 질문의 카드로 저장·조회한다. 대상별로 카드를 분리하고 마지막 인텐트는 기존 수동 첨부를 사용한다.
- 영향 파일: `app/interview/{tasks,flow,cards,common}.py` 및 기존 검토 조회/사진 연결 처리부; `tests/test_interview_guidance.py`, `tests/test_interview_reviews.py`.
- 의존: T1, T3, T6 및 현재 질문 스냅샷 복구 완료.
- 합격: 대상 없음·추천 실패·늦은 결과는 필수 진행을 막지 않는다. 답변한 질문/접수 스냅샷에 추천을 뒤늦게 끼워 넣지 않고, 같은 revision의 재조회는 동일하며, 대상 삭제/정정 시 현재 유효성을 검사한다.
- HTTP·DB: 다음 질문 있음/없음, 추천 실패, 답변 접수와 추천 완료 경합, 정정과 추천 완료 경합을 검사한다. 업로드 후 연결 실패는 같은 mediaId 재사용, 사진 없이 계속은 기존 사진 보존을 확인한다.
- 커밋 경계: 기존 표현 안의 추천 전달·검증과 실제 HTTP/DB 회귀. 새 task payload 모델·테이블·열·phase를 선행 설계로 추가하지 않는다.

## 4. 완료 판정의 두 단계

**검사 시점:** 전체 작업 구현과 필요한 검증 코드 작성을 완료한 뒤 검사를 한 번에 수행한다. 이어서 독립 누락 재점검·보완을 거쳐 결과를 판정한다. 각 T의 검증 항목을 작업 도중 실행하지 않는다.
최종 검사에서 결함이나 누락이 발견되면 보완 후 필요한 영향 범위만 재검사한다. 기존 중간 통과 기록으로 최종 검사를 대체하지 않는다.

**계약 정확성:** 최종 검사에서는 단위 검사뿐 아니라 해당 사용자 경로의 실제 서버 HTTP·격리 테스트 DB로 검증한다.
응답과 별도 DB 연결의 commit 결과·API 재조회를 함께 확인하고 실패/거부에서는 부분 저장과 기존 데이터 훼손이 없어야 한다.
`AGENTS.md`에 따라 테스트 작성에 참여하지 않은 독립 에이전트가 누락·우회·skip을 확인한다.
최종 검사와 독립 보완을 마친 뒤 operation별 구현·검증 코드를 같은 원자 커밋으로 확정한다. 모든 operation을 한 커밋에 합치라는 뜻은 아니다. lint나 합성 출력 통과만으로 완료하지 않는다.

**실제 모델 의미 품질:** 모든 구현을 완성한 뒤의 최종 검사 단계에서 계약 검증 통과를 확인하고 실제 provider로 업무/예시 구분, 충분성, 같은 업무 ID, 모호한/명시적 정정, 사실 보존, 사진 추천의 유용성을 평가한다.
입력·실제 출력·모델/설정·기대 판단·사람 검토 결과를 남기고 원본 계약에 대한 실패를 operation별로 수정한다.
사용자가 지정한 환경파일을 자식 프로세스 환경에만 연결하여 실제 모델 검증을 수행한다. 키나 환경파일은 저장소에 복사하지 않는다. scripted/fake/synthetic 통과를 실제 모델 품질 통과로 바꾸어 보고하지 않는다.
모델 검증이 남으면 계약 구현 통과와 의미 품질 미검증을 분리하고 전체 완료를 선언하지 않는다.

## 5. 기존 프론트 연동과 범위 종료

기존 프론트는 버전을 기록하고 수정하지 않은 상태에서 질문·요약·정정·사진·초안의 읽기 전용 연동을 확인한다.
백엔드 계약 위반은 해당 원자 작업에서 고친다. 계약에 맞는 응답을 UI가 잘못 처리하면 유효 응답·재현·버전·기대/실제를 정리해 기존 담당자에게 인계할 항목으로 남긴다.
프론트 구현·테스트 수정·신규 PR과 PR #175는 이 계획의 작업 또는 선행 의존성이 아니다.
화면 표시 순서 등 실제 UI를 확인하지 못한 항목은 미확인으로 기록하며 백엔드 통과로 대신하지 않는다.

후속 실행 시에도 계약·모델·기존 UI의 검증 범위와 실패/미검증을 구분한다.
후속 승인에 따라 백엔드 PR을 통해 back-end/dev로 병합한다. 기존 프론트 PR #175는 이번 병합에 포함하지 않는다.


## 6. 최종 실행 기록 (2026-10-08)

T1~T7의 구현·검증 코드 작성을 모두 마친 뒤 최종 검사를 시작했다. 최종 검사에서 발견한 결함은 보완하고 영향 검사를 다시 수행한다. 구현 작업자는 GPT-6 Astra Low이며, 별도 비작성자가 계약·검증 근거를 독립 검토한다.

| 작업 | 반영 내용 | 현재 근거 |
| --- | --- | --- |
| T1 | 같은 인텐트에서 실제 답변된 과거 질문 카드의 ID 이력 전달, 분할·재등장 ID 유지, 업무와 예시·항목별 상태 구분 | 단위/HTTP 회귀 작성, 실제 모델 사례 기록 |
| T2 | 기존 평가·depth·재시도 엔진 재사용. 확보한 절차를 다시 부족으로 묻는 모델 동작 보완 | depth 0~5·재시도 HTTP 회귀, 최종 프롬프트 충분/불충분/핵심 미확정 각 3회 실모델 |
| T3 | 알려진 일부 절차 보존, 남은 부족 측면 요약, 미확인 근무조 귀속 추측 방지 | provider 회귀 및 실제 부분절차·미확정 요약 |
| T4 | 기존 자유 정정 경로 재사용, 수정 대상 밖 기존 부족정보 설명 보존 | provider 회귀 및 실제 자연어 삭제 |
| T5 | 고정된 검토 입력의 시간·분류·근무조 연결·미확정 상태 보존 검사 | provider 및 실패/재시도 HTTP 회귀, 실제 초안 결합 |
| T6 | 저장된 검토에 없는 섹션 추천 거절 | provider 회귀 및 실제 사진 추천 |
| T7 | 질문/요약의 기본 작업 commit 이후 선택적 추천, 실제 다음 미답변 질문에 기존 PHOTO_SUGGESTIONS/attachmentTarget 저장 | 단위·경합 HTTP 회귀, 수정 없는 기존 프론트 연동 |

사진 추천은 기본 작업의 성공 저장과 잠금 해제 뒤 실행한다. 외부 호출 동안 DB 트랜잭션을 유지하지 않으며, 적용 직전에 현재 질문·검토 revision·섹션을 재확인한다. 중복 호출은 가능하지만 같은 대상의 추천은 한 번만 표시한다. 답변 접수 또는 정정 이후 도착한 오래된 결과는 버린다. 새 task kind·phase·payload 모델·DB 열·migration은 추가하지 않는다.

추천은 best effort다. 기본 작업 commit 뒤 프로세스가 종료되면 추천이 유실될 수 있으며 별도 영속 재시도는 없다. 기존 수동 첨부는 유지된다. 추천 호출은 기존 worker를 제한된 timeout 동안 사용한다. 카드가 최대 5개이면 질문 카드를 우선하며 마지막 인텐트에는 새 질문을 만들지 않는다.

### 확인된 검증 범위

- 최종 영향 검사: 319 passed, 120 skipped. 로컬 DB 미설정 및 SQLite 잠금 대응 skip은 MySQL 통과로 세지 않는다. `.local/final-interview/targeted.xml`.
- 표준 전체 Python 검사: 6,435 passed, 1 failed, 16 skipped, 22 deselected. 유일 실패는 당시 `.3` 프롬프트의 해시 등록 누락이었다. 최종 `.4` 해시 등록 후 같은 runner에서 해당 모듈 10 passed(MySQL 1 포함), tooling 62 passed, 전체 실제 HTTP 209 passed/0 skipped로 마무리했다. 첫 전체 검사의 MySQL 2,359건은 모두 통과/skip 0이다. SQLite에서 MySQL 전용 동작을 제외한 16 skip과 실모델 opt-in 제외를 분리하며, 실패한 최초 run을 전체 통과로 덮어쓰지 않는다. 증거: `.local/final-interview/full/{python,tooling}.xml`, `final-http/{python,tooling,http-e2e}.xml`. 최종 영향 재검사와 실모델 검증으로 `.3` 이후 보완 범위를 확인했다.
- 실제 모델: `gpt-6-luna`. 원 응답·입력·판정은 `.local/final-interview/live-architecture-results.json`, `live-content-after.json`, `live-judge-final.json`. 초회/중간 실패 기록도 보존하며 최종 결과와 섞지 않는다. 최종 충분성 프롬프트는 `2026-10-08.4`; 9회 모두 별도 비작성 에이전트의 의미 검토를 통과했다(`live-judge-final-independent-review.json`). 이는 제한된 합성 표본이며 일반 실패율을 보장하지 않는다. 명시된 공통 업무의 빈 절차·미확정 보존은 `live-known-scope-unknown-steps-v4.json` 2회에서도 독립 의미 검토를 통과했다.
- 실제 전사: `gpt-transcribe`, 3 passed/0 skipped (`live-stt.xml`). 합성 한국어 음성과 업로드→전사→READY를 확인했다. 실제 마이크 또는 TCP/MySQL 전사 전체 경로의 증거는 아니다.
- 기존 프론트: `origin/front-end/dev`의 `460edd598f972a5dc6806edd9c93f5d794867615`, 추적 파일 400개 원본 byte 일치, PR #175 제외. 브라우저→실제 app.main→격리 MySQL에서 요약 우선, 이전 요약의 사진 대상, 첨부/복귀/건너뛰기, 초안·미리보기·격리 게시까지 확인했다. 독립 DB 저장 및 보호 사진 GET200/비인증401, 원본 사진 hash 일치를 확인했다. frontend build 통과. 증거: `.local/existing-ui-evidence/REPORT.md`.
- 프론트 연동 답변은 독립 HTTP TEXT 제출, AI는 원시 fixture였다. 실제 마이크·카메라/앨범·시스템 파일창 취소·운영 배포는 이 검증의 범위가 아니다. 프론트 구현이나 테스트는 수정하지 않았다.


### 병합 범위와 실행 설정

`origin/back-end/dev`의 `d35c26d`를 기준으로 한 전체 PR에는 이전에 통합한 `0042`(질문 안내 저장)·`0043`(평가 부족 측면 저장) migration이 포함된다. T1~T7 보완 자체는 이 저장 구조를 사용하며 추가 migration이 없다. 미통합 `0044`는 포함하지 않는다. 전체 PR에서도 공개 OpenAPI·ERD·프론트 파일은 변경하지 않는다.

기존 호환 스위치 `INTERVIEW_GUIDANCE_RESPONSES=on`에서 안내 카드와 사진 추천을 반환한다. 기본값 `off`는 기존 호환 동작을 유지한다. 개발자가 병합본으로 기능을 시험할 때 해당 설정과 `alembic upgrade head`를 적용해야 한다. CI 생략 병합만으로 서버 코드·설정·DB가 배포되지는 않는다.


### 로컬 CI와 확정 커밋

- 로컬 Linux ARM64에서 `deploy/backend/Dockerfile`의 test 및 runtime target 빌드 성공. 명세 lint·계약 검사·Swagger build·Ruff가 포함된다.
- 배포 스크립트 bash 구문 검사 통과. macOS 직접 실행은 flock 부재로 실패했으며, Linux 컨테이너에서 68건 중 66 passed/2 skipped. 컨테이너에 Docker CLI가 없어 제외된 Compose 2건은 호스트에서 2 passed로 확인했다. 해당 환경 실패도 로그에 보존했다.
- Nginx dev/production 구문·OAuth callback query 비노출·업로드 크기 검사 통과.
- 독립 비작성 검토는 최종 HTTP 209건의 실패·경합·스냅샷 보존, MySQL 동시성 대응, 프롬프트 해시 등록을 확인했고 통합 차단 사항을 발견하지 못했다.
- 원격 CI는 사용자 지시로 `[skip ci]` 처리한다. 원격 검사·자동 배포를 통과했다고 보고하지 않는다.

| 작업 | 확정 커밋 |
| --- | --- |
| T1 질문 이력·카드 ID | `8b68cdf` |
| T2 충분성·부족 측면 | `9c58bdf` |
| T3 부분 절차·미확정 요약 | `591854f` |
| T4 정정 범위 보존 | `dfe2d46` |
| T5 초안 사실 보존 | `c06f8d4` |
| T6 추천 섹션 검증 | `900e914` |
| T7 선택적 사진 카드 연동 | `3e21270` |

최종 작업 트리의 제품 코드·검증 코드는 위 검사 대상과 동일하다. 원자 커밋의 중간 프롬프트에는 별도 버전/해시를 부여했고, 통합 결과는 실제 최종 모델 검증과 같은 `2026-10-08.4` 및 동일한 프롬프트 본문이다.

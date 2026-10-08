# Structured Output과 인터뷰 흐름 구현 태스크

AI가 질문과 안내 카드를 함께 생성하고, 서버가 검증·ID 부여·저장·진행을 담당하도록 인터뷰를 확장한다. 이해 요약이 저장된 뒤 사진을 추천하며, 연결한 사진은 음성 정정과 초안 생성 이후에도 같은 섹션에 유지한다.

총 40개 태스크다. 백엔드 계약·구현·검증·통합 35개와 별도 프론트엔드 연동 5개로 나눴다. 각 태스크는 하나의 결과를 만들고 독립적으로 검증·리뷰·되돌릴 수 있는 커밋 경계다. 아래 체크박스는 앞으로 실행할 작업이며, 이 문서 작성으로 구현 완료를 표시하지 않는다.

## 작업 기준

| 항목 | 기준 |
| --- | --- |
| 작업 입력 | 2026년 10월 8일 제공된 Structured Output과 인터뷰 흐름 설계 초안 |
| 작업 워크트리 | `worktrees/interview-structured-output-20261008` |
| 현재 체크아웃 | `back-end/dev` |
| 기준 커밋 | `d35c26d54aac54fcd1eda495644af1a22266ad1f` |
| 원격 대조 | 워크트리 생성 시 fetch한 `origin/back-end/dev`와 동일 |
| 공개 API 기준 | `back-end/openapi.yaml` 0.11.0 |
| 마이그레이션 기준 | `0041_processing_attempt_not_null.py`까지 존재. 새 번호는 실제 구현 직전 head를 확인해 배정 |
| 이번 산출물 | 로컬 실행 계획. 애플리케이션 코드·기존 API 명세 변경, 이슈 발급, 커밋·푸시·PR·배포는 수행하지 않음 |

현재 `back-end/dev`에 바로 구현 커밋을 쌓지 않는다. 구현을 시작할 때 [협업 방법](../../COLLABORATING.md)과 [브랜치 전략](../../BRANCHING.md)에 따라 이슈·담당자를 정하고 `back-end/feat/<이슈번호>-interview-structured-output` 등 작업 브랜치를 만든다. PR 대상은 `back-end/dev`다. 프론트 작업은 `front-end/dev`에서 분기한다. 이 계획은 원격 이슈로 등록된 태스크 목록이 아니다.

다른 AI 작업 브랜치의 코드는 기준 브랜치에 포함된 것으로 취급하지 않는다. 구현 착수 전에 새로 병합된 변경과 이 목록을 대조하고, 충족된 태스크만 검증 근거와 함께 제외한다. 기존 기능 브랜치 전체를 선행 조건으로 병합하거나 변경 이력을 임의로 가져오지 않는다.

## 현재 구현과 필요한 변경

| 영역 | 기준 커밋의 실제 상태 | 필요한 작업 |
| --- | --- | --- |
| 질문 AI 출력 | `QUESTION_SCHEMA`·`RawQuestion`은 `question`만, `GeneratedQuestion`은 `text`·`meta`만 보유 | 안내·카드 출력, 부분 오류 복구, 검증·ID 부여 추가 |
| 질문 저장과 응답 | `InterviewTurn`은 질문 본문만 저장하고 `question_body`는 안내 필드를 반환하지 않음 | DB 저장, 질문과 카드의 원자 저장, 직렬화 추가 |
| 카드 공개 계약 | LIST·PROGRESS_CHECKLIST·PHOTO_SUGGESTIONS와 `lastAnsweredQuestion` 계약은 존재 | 계약만 있는 부분의 런타임 구현과 실제 HTTP 검증 |
| 답변 평가 | 충분성·확률·부족 측면 검증과 depth 0~5 진행이 구현됨 | 평가의 부족 측면을 후속 카드와 요약에 정확히 전달·보존 |
| 이해 요약 | `IntentSummary`와 `StructureSnapshot`, 참조 검증, 검토별 revision이 구현됨 | 기존 구조 재사용, 부족 정보 전달과 신규 카드 흐름 회귀 검증 |
| 사진 추천 | 전용 AI operation·작업·저장은 없지만 공개 사진 카드와 첨부 대상 계약은 존재 | 요약 저장 후 독립 생성, READY 섹션 검증, 선택적 실패 처리 |
| 사진 첨부 | 업로드와 `PUT review/photos`가 분리되어 있고 mediaId·revision·소유권을 검사함 | 기존 API 재사용, 실패·재시도·기존 사진 보존 검증 |
| 음성 정정 | 이전 섹션 ID로 사진을 다시 연결하며 삭제된 섹션의 참조를 해제함 | ID 교체에 의한 사진 유실 방지, 추천 무효화·재생성 연결 |
| 초안 생성 | 전체 READY 검토와 revision 검사, 생성 snapshot, 섹션 ID와 사진 재연결 구현 | 신규 추천 작업과의 경합 및 최종 사진 연속성 검증 |
| 최종 검토와 게시 | 초안 정정·미리보기·부족 항목 확인·게시·보호된 사진 조회 구현 | 새 인터뷰 흐름에서 같은 초안·사진을 유지하는 통합 검증 |

주요 근거는 [AI 계약](../app/ai/contracts.py), [출력 스키마](../app/ai/schemas.py), [provider](../app/ai/provider.py), [인터뷰 진행](../app/interview/flow.py), [작업 처리](../app/interview/tasks.py), [응답 표현](../app/interview/common.py), [검토 API](../app/interview/routes.py), [검토 콘텐츠](../app/interview/content.py), [초안 생성](../app/interview/drafting.py), [DB 모델](../app/db/models.py)이다.

## 구현 불변 조건

1. 질문 생성 AI 출력은 `question`, nullable `guidance`, `guidanceCards`다. 질문 카드는 LIST와 PROGRESS_CHECKLIST만 생성하고, 카드를 쓰지 않으면 빈 배열을 반환한다.
2. AI 카드에는 `type`, `title`, `items`, nullable `footer`를 둔다. 각 item은 nullable `id`, `label`, nullable `description`, nullable `status`를 가진다. LIST의 status는 null, 진행 항목은 네 상태 중 하나다. 카드 ID와 새 항목 UUID는 서버가 만든다.
3. 새 항목의 null ID를 기존 매뉴얼 콘텐츠의 `new-N` 참조와 혼용하지 않는다. `StructureSnapshot`의 기존 참조 체계는 유지한다.
4. LIST의 예시를 실제 매장 업무로 확정하지 않는다. 답변 접수만으로 CURRENT를 COMPLETED로 바꾸지 않는다. 진행 카드는 최대 하나, CURRENT도 최대 하나이며 전체 목록 질문에는 CURRENT가 없어도 된다.
5. 같은 의미의 기존 항목은 허용된 이전 ID를 유지한다. 카드·항목 ID와 매뉴얼 sectionId·mediaId는 서로 다른 식별자다. `InterviewSubject` 모델을 추가하지 않는다.
6. 평가 출력은 `sufficient`, `probability`, `missing_aspects`를 유지한다. 서버가 기존 depth 한도를 적용하고, 부족한 채 종료되면 부족 정보를 보존한다.
7. 이해 요약은 `summary`와 기존 `structure.shifts`, `structure.sections`, `structure.missing_information`을 사용한다. 요약 표시 아이콘, 진행 카드 상태, 절차의 `checklistItem`을 구분한다.
8. 사진 추천 AI 출력은 `suggestions` 배열이며 각 항목은 `sectionId`, `title`, `items`, nullable `footer`다. item에는 `label`과 nullable `description`만 있다. 추천 없음은 `[]`이며 `helpful`을 추가하지 않는다.
9. 사진 추천은 저장된 요약을 입력으로 생성한다. 새 흐름에서 반환하는 카드는 현재 검토에 존재하는 섹션 하나에 연결하며, 유효한 대상이 없으면 건너뛴다. 추천 실패는 인터뷰·요약·completion의 필수 조건이 아니다.
10. 사진은 최종 매뉴얼 첨부다. #171의 AI 작성용 미디어 입력과 별도로 처리하며, 추천·요약·정정 AI에 첨부 사진의 바이트나 저장소 경로를 전달하지 않는다.
11. 사진 없이 계속하기는 기존 사진 삭제 명령이 아니다. 업로드 성공 뒤 연결 실패는 같은 mediaId로 연결만 재시도한다. 섹션 삭제 시 다른 섹션으로 사진을 임의 이동하지 않는다.
12. AI는 처리 상태·오류·revision·게시 여부를 결정하지 않는다. 녹음 시간과 파일 선택 상태는 프론트가 관리한다. 질문 선생성과 화면 표시 순서를 분리하고 미확인 요약을 먼저 보여 준다.

## 선행 결정과 실행 기본안

사진 추천의 공개 표현은 기존 `guidanceCards`·`PHOTO_SUGGESTIONS`·`attachmentTarget`을 재사용한다. API 필드·endpoint·버전·승인 계약 snapshot의 변경을 이 작업의 선행 조건으로 두지 않는다. A01과 A02에서 기존 응답의 전달 시점과 내부 저장·진행을 확인한다. 아래 새 operation 이름은 내부 AI 호출용 제안이다.

| 결정 | 실행 기본안 | 이유와 확인할 경계 |
| --- | --- | --- |
| 사진 추천 반환 위치 | 기존 질문 응답의 `guidanceCards`에 `PHOTO_SUGGESTIONS`를 포함하고 `attachmentTarget`으로 실제 검토·섹션 연결 | 별도 생성 시점이 새 공개 필드를 요구하지는 않음. 사용자 결정: 실제 다음 미답변 질문이 있을 때만 전달. 마지막 인텐트·질문 없는 이어하기는 기존 수동 사진 첨부 사용 |
| 공개 질문 카드 호환 | 공용 `ManualGuidanceCard`의 세 유형은 유지하되 신규 질문 생성은 두 유형만 사용 | 과거 계약·응답을 불필요하게 거절하지 않고 생성 책임을 분리 |
| 추천 작업 상태 | 기존 review의 UNDERSTANDING/CORRECTION 처리 상태와 별도 내부 task 연결 정보 사용 | 추천 처리·실패 때문에 READY 검토를 PROCESSING/ERROR로 변경하지 않음 |
| 추천 revision | 내부 추천 결과는 원본 review revision에 귀속. 공개 질문 카드 변경은 기존 계약대로 session revision과 함께 원자 저장 | 사진 연결에는 최신 review revision을 사용. 추천만으로 review revision을 올리지 않으며 평가 작업과 답변 시점 snapshot을 훼손하지 않음 |
| 추천 재생성 | 최초 요약 READY와 실제 내용 정정 성공 뒤 생성. GET·사진 순서 변경·동일 확인에서는 생성하지 않음 | 재조회로 ID·추천이 바뀌거나 불필요한 AI 호출이 늘지 않음 |
| 추천 표시 한도 | 기존 guidanceCards 전체 최대 5개와 카드별 item 최대 50개 적용 | LIST·진행·사진 카드의 합계로 검사. 기존 카드를 덮어쓰거나 총 상한을 초과하지 않도록 배치. 공간 부족·늦은 추천은 전달하지 않아도 되며 진행 보존 |
| 이해 확인과 진행 | 프론트가 미확인 READY 요약을 우선 표시. 서버의 confirmedAt을 인터뷰 진행·초안 생성의 새 필수 조건으로 만들지 않음 | 기존 depth 한도 자동 진행과 호환. 사용자의 확인 후 다음 화면으로 이동하는 UI는 별도로 구현 |
| 삭제 처리 | 기존 명시적 삭제 동작만 허용하고, AI가 기존 ID를 빠뜨렸다는 이유만으로 사진 연결을 다른 대상으로 옮기지 않음 | 삭제 의도가 불명확하면 기존 콘텐츠 보존·정정 재입력 경로 사용 |

현재 API의 `attachmentTarget=null` 및 WORK_STRUCTURE 사진 계약은 기존 호환 목적으로 유지한다. 이 작업의 자동 추천은 SECTION 대상만 생성하며, 섹션이 없는 근무 구조 요약에는 추천하지 않는다. 기존 근무 구조 사진 첨부 기능은 그대로 사용할 수 있다.

## 태스크 완료 기준

각 구현 태스크는 변경 코드와 해당 정상·거부·실패·재시도 테스트를 같은 커밋에 포함한다. 아래 파일 경로는 워크트리 루트 기준이며, **신규**로 표시한 파일명은 제안이다. 재사용 태스크는 실제 결함이 확인될 때만 구현을 수정하고, 이미 맞는 로직을 다시 작성하지 않는다.

각 커밋 직후에도 기존 테스트와 실행 경로가 유효해야 한다. 새 타입·검증기·예약 helper는 소비자가 준비될 때까지 비활성 정의로 추가하고, 스키마 선택이나 worker 등록과 같은 활성 전환은 관련 호출자·fake·fixture 변경과 같은 커밋에서 수행한다.

API·백엔드 동작을 바꾸는 태스크는 [백엔드 작업 규칙](../AGENTS.md)에 따라 실제 Uvicorn HTTP와 격리 MySQL을 사용한다. 응답 외에 별도 DB 연결의 commit 결과와 API 재조회를 확인하고, 실패 시 부분 저장이 없는지 검사한다. 각 테스트 작성·수정 뒤에는 작성에 참여하지 않은 독립 에이전트가 누락을 검토해야 한다. 이 절차를 마지막 통합 검토 한 번으로 대체하지 않는다. G04는 전체 변경의 추가 종합 검토다.

## A 계약과 검증 준비

### A01 응답 위치와 상태 경계 결정

- [x] 선행 결정의 결과를 기록한다.
- **선행:** 없음.
- **파일:** `back-end/docs/manual-interview-design.md`, 이 계획.
- **작업:** 기존 `guidanceCards`에서의 카드 배치·전달 시점, 추천 결과의 revision 정책, 진행 카드 ID 수명, 카드만 오류일 때 제외 단위, 명시적 섹션 삭제 경계를 확정한다. 화면 확인과 서버 진행을 구분하고 프론트 의존성을 기록한다.
- **완료·검증:** 최초 요약·마지막 인텐트·추천 없음·추천 실패·정정·이어하기 각각의 입력/저장/응답 책임이 한 가지로 정의된다. InterviewSubject나 새 필수 확인 상태가 도입되지 않는다.
- **커밋:** `docs(interview): 구조화 출력과 사진 추천 처리 경계 정의`.

### A02 기존 사진 카드 계약과 전달 흐름 확인

- [ ] 기존 계약 안에서 사진 추천을 전달할 경로와 시점을 확인한다.
- **선행:** A01.
- **파일:** `back-end/openapi.yaml`과 승인 계약 snapshot은 읽기 기준, `back-end/docs/manual-interview-design.md`, `back-end/docs/tests/manual-guidance.test.mjs`, `manual-photo-guidance.test.mjs`, `manual-question-snapshot.test.mjs`, 이 계획.
- **작업:** `guidanceCards`의 PHOTO_SUGGESTIONS와 attachmentTarget으로 추천 내용·intentId·SECTION/sectionId를 매핑한다. LIST API item에는 AI의 `status:null`을 보내지 않는다. 사진 추천 AI를 요약 뒤 별도로 호출하는 것과 기존 질문 응답에 결과를 전달하는 것을 구분한다. API 스키마·버전·승인 snapshot을 변경하지 않고 기존 계약에 대한 구현 검증을 보강한다.
- **완료·검증:** 카드 표현·대상·총 개수 제한과 답변 스냅샷 불변성을 확인한다. 현재 공개 전달 위치는 `ManualInterviewQuestion.guidanceCards`이고 review 응답에는 해당 필드가 없음을 기록한다. 다음 질문이 있는 경우와 질문 없는 마지막 인텐트·READY_TO_GENERATE·이어하기를 각각 대조해 전달 경로를 확정한다. 사용자 승인 범위에 따라 후자는 추천 카드 없이 기존 수동 사진 첨부를 사용한다. 늦은 결과·공간 부족도 전달을 보장하지 않는다. 임의 질문·새 필드·phase 변경 없이 기존 카드와 상태를 보존하는 것을 검증한다.
- **커밋:** `test(interview): 기존 사진 카드 계약과 전달 흐름 검증`.

### A03 실제 HTTP 인터뷰 테스트 준비

- [ ] 인터뷰 태스크들이 공유할 격리 HTTP fixture를 준비한다.
- **선행:** A01.
- **파일:** `back-end/e2e/conftest.py`, `back-end/e2e/manual_scenario.py`, **신규** `back-end/e2e/interview_helpers.py`, 필요한 경우 `back-end/testing/compose.yml`.
- **작업:** 테스트 점주·승인 매장·세션 준비, 질문/검토 대기, 독립 DB 조회, 테스트별 미디어 정리 도우미를 만든다. AI 성공·실패 입력 제어는 테스트 전용 외부 경계에서 수행하고 제품 endpoint나 handler 교체 기능을 추가하지 않는다.
- **완료·검증:** 실제 app.main HTTP와 `jidan_e2e_test`에 대한 smoke가 commit·재조회를 확인한다. 잘못된 DB/host는 실행 전에 거부한다. fake AI의 적용 범위와 실제 외부 AI 미검증을 보고할 수 있다.
- **커밋:** `test(interview): 격리 HTTP와 DB 검증 도우미 추가`.

## B 질문 Structured Output과 저장

### B01 질문 출력 타입과 JSON Schema 확장

- [ ] 질문·안내·카드의 모델 출력 타입을 정의한다.
- **선행:** A01.
- **파일:** `back-end/app/ai/contracts.py`, `schemas.py`, `back-end/tests/test_ai_schemas.py`.
- **작업:** QuestionCard와 item 타입, `guidance`, `guidanceCards`를 추가한다. AI에 요청하는 모든 키와 nullable 값을 명시하고 새 항목 id는 null로 받는다. 내부 결과에는 구 호출자를 위한 안내 기본값을 두고 `text` 매핑을 유지한다. 새 raw 타입과 schema는 먼저 별도로 정의하며 활성 OUTPUTS 전환은 B08에서 수행한다.
- **완료·검증:** LIST/진행 카드/빈 배열 예제가 새 타입에서 통과한다. 질문 출력의 PHOTO_SUGGESTIONS, 잘못된 enum·타입·필수 키·추가 키를 구분한다. 기존 provider/fake 경로·StructureSnapshot·평가 스키마의 검사가 계속 통과한다.
- **커밋:** `feat(ai): 질문 안내 카드 구조화 출력 타입 추가`.

### B02 질문과 카드의 오류 처리 분리

- [ ] 유효한 질문을 카드 하나의 오류로 잃지 않게 한다.
- **선행:** B01.
- **파일:** `back-end/app/ai/provider.py`, `schemas.py`, `back-end/tests/test_ai_provider.py`, `test_ai_schemas.py`.
- **작업:** 현재 `_structured`의 전체 객체 파싱 실패 경로를 대체할 질문 전용 parser를 준비한다. JSON과 질문 자체를 먼저 검증하고 카드별 검증 실패는 해당 카드 제외로 처리한다. guidance 오류의 null 처리도 A01 규칙에 맞춘다. 활성 provider 연결은 B08에서 함께 전환한다.
- **완료·검증:** 정상 질문+정상/오류 카드 혼합은 정상 카드만 남긴다. 빈 질문·손상 JSON·provider refusal/출력 중단은 기존 제한 재시도 대상으로 남는다. 다른 AI operation의 엄격한 검증을 느슨하게 만들지 않는다.
- **커밋:** `fix(ai): 질문 본문과 안내 카드 검증 실패 분리`.

### B03 카드 구조와 진행 제약 검증

- [ ] 서버의 카드 검증기를 구현한다.
- **선행:** A02, B01.
- **파일:** **신규** `back-end/app/interview/cards.py`, **신규** `back-end/tests/test_interview_cards.py`.
- **작업:** 카드 최대 5개, item 1~50개, 텍스트 길이·공백, 카드/항목 중복, 진행 카드 최대 하나·CURRENT 최대 하나, 유형별 status 규칙을 검사한다. 임의 선택형 입력과 사진 추천이 질문 생성 결과로 들어오는 것을 거부한다.
- **완료·검증:** 0/1/최대/최대+1 경계, 전체 목록 질문의 CURRENT 0개, 중복 진행 카드, LIST의 비-null 상태, 진행 항목의 null 상태를 검증한다. 위반 카드는 B02 정책대로 제거되고 질문은 유지된다.
- **커밋:** `feat(interview): 안내 카드 구조와 진행 제약 검증 추가`.

### B04 카드와 항목 ID 유지

- [ ] 허용된 기존 ID를 보존하고 새 UUID를 부여한다.
- **선행:** B03.
- **파일:** `back-end/app/interview/cards.py`, `back-end/tests/test_interview_cards.py`.
- **작업:** 입력에 포함된 같은 세션의 검증된 카드 이력만 기존 ID의 근거로 사용한다. item의 기존 ID는 순서·표현 변경 뒤에도 유지하고 null에는 새 ID를 부여한다. 카드 자체에는 AI ID가 없으므로 type·유지된 item ID로 유일하게 대응되는 이전 카드 ID를 재사용하며 모호하면 새 카드 ID를 만든다.
- **완료·검증:** 재정렬·표현 수정·새 항목 추가·일시적 카드 없음 뒤 복원에서 ID가 부당하게 재발급되지 않는다. 다른 세션·허용 입력 밖 ID·중복 ID는 거부한다. label이나 배열 index만으로 동일성을 결정하지 않는다.
- **커밋:** `feat(interview): 질문 카드와 항목 식별자 유지`.

### B05 질문 안내 저장 컬럼 추가

- [ ] 질문 행에 저장할 안내 스키마를 추가한다.
- **선행:** A01, B01.
- **파일:** `back-end/app/db/models.py`, `back-end/migrations/versions/`의 새 migration, `back-end/tests/test_migrations.py`, `test_schema_drift.py`, 관련 schema fixture.
- **작업:** `InterviewTurn`에 nullable guidance와 저장용 guidance_cards JSON을 추가한다. 기존 QUESTION/ANSWER/CORRECTION 행의 backfill·null 해석을 정의하고 과거 질문을 AI로 재생성하지 않는다.
- **완료·검증:** SQLite·MySQL upgrade와 빈/기존 데이터 경로를 검증한다. 구 행은 안내 없음으로 읽히고 기존 질문·답변 제약은 보존된다. DB 변경만으로 새 응답 필드가 활성화되지 않는다.
- **커밋:** `feat(db): 인터뷰 질문 안내 저장 컬럼 추가`.

### B06 평가의 부족 측면 저장

- [ ] 성공 평가의 missing_aspects를 손실 없이 보존한다.
- **선행:** A03, B05.
- **파일:** `back-end/app/db/models.py`, 새 migration, `back-end/app/interview/tasks.py`, `back-end/tests/test_interview_api.py`, **신규** `back-end/e2e/test_interview_evaluation_http.py`, migration/schema 검사.
- **작업:** `InterviewEvaluation`에 평가 결과의 부족 측면을 저장하거나 동등한 불변 결과 snapshot을 추가한다. 현재 저장되는 needs_follow_up·probability와 같은 평가 apply 트랜잭션에서 기록한다. 과거 평가에 결과가 없으면 알 수 없는 값으로 다루고 만들어내지 않는다.
- **완료·검증:** 충분/부족·순서·중복 정리·최대 측면 수와 과거 행을 검증한다. 평가 apply 실패와 중복 실행에서 부분 결과나 중복 진행이 남지 않는다. 평가 AI의 세 필드 계약은 바꾸지 않는다.
- **커밋:** `feat(interview): 충분성 평가의 부족 측면 보존`.

### B07 질문 입력에 직전 카드와 평가 연결

- [ ] 질문 생성에 필요한 누적 문맥을 task 입력에 고정한다.
- **선행:** B01, B04, B05, B06.
- **파일:** `back-end/app/ai/contracts.py`, `back-end/app/interview/flow.py`, `back-end/tests/test_interview_api.py`, **신규** `back-end/tests/test_interview_guidance.py`.
- **작업:** QuestionRequest에 직전 검증 카드와 필요한 평가 결과를 추가한다. BASE/PROBE 각각 현재 주제·누적 답변·다른 완료 요약·카드의 출처를 구분한다. 같은 주제에서 카드가 생략된 턴이 있어도 직전 유효 진행 목록을 찾는다.
- **완료·검증:** 같은 저장 입력으로 재시도하며 이후 정정이 기존 task snapshot을 바꾸지 않는다. 다른 세션 문맥은 섞이지 않는다. 배포 전 생성된 task payload는 새 선택 입력의 기본값으로 읽힌다. 최대 데이터는 1 MB task payload 제한을 검증한다.
- **커밋:** `feat(interview): 질문 생성 문맥에 이전 카드와 평가 연결`.

### B08 질문 생성 프롬프트와 provider 연결

- [ ] 질문·카드 유형·내용을 한 번의 생성 결과로 받는다.
- **선행:** B02, B03, B04, B07.
- **파일:** `back-end/app/ai/prompts.py`, `provider.py`, `fake.py`, `openai_provider.py`, `back-end/tests/test_ai_provider.py`.
- **작업:** 질문 하나 원칙, 예시/실제 업무 구분, 읽기 전용 상태, 전체 목록 질문, 기존 ID 유지 지시를 작성한다. 활성 OUTPUTS·질문 parser·provider·FakeAiProvider·fallback·영향 fixture를 같은 커밋에서 새 계약으로 전환하고 PROMPT_VERSION을 갱신한다. 카드 없는 유효 출력도 지원한다.
- **완료·검증:** 재고 정리 COMPLETED·시재 점검 CURRENT 예제, 답변 직후 자동 완료 금지, 미확인 예시 업무의 승격 금지 fixture를 통과한다. 의미 품질은 G03에서 별도 평가하며 문자열 검사만으로 보장했다고 보고하지 않는다.
- **커밋:** `feat(ai): 인터뷰 질문과 안내 카드 생성 연결`.

### B09 질문과 카드의 원자 저장

- [ ] 검증된 질문과 카드 전체를 같은 트랜잭션에 저장한다.
- **선행:** A03, B05, B08.
- **파일:** `back-end/app/interview/flow.py`, `tasks.py`, `back-end/tests/test_interview_guidance.py`, **신규** `back-end/e2e/test_interview_guidance_http.py`.
- **작업:** `_question_apply`와 `write_question`에 검증·ID 부여 결과를 연결한다. 질문 오류의 제한 재시도 소진 시 기존 fallback 질문과 빈 안내를 저장한다. 카드만 오류면 유효한 질문·카드를 저장한다.
- **완료·검증:** 질문/카드/세션 revision이 함께 commit되고 한 번만 증가한다. DB 실패·lease 상실·늦은 task·중복 적용은 부분 저장을 만들지 않는다. 독립 DB 재조회에서 카드 ID와 순서가 동일하며 공개 응답 연결은 B10에서 검증한다.
- **커밋:** `feat(interview): 질문과 안내 카드 원자 저장`.

### B10 질문 안내 응답과 호환 스위치

- [ ] 저장된 안내를 공개 응답으로 변환한다.
- **선행:** A02, B09.
- **파일:** `back-end/app/interview/common.py`, **신규** `back-end/app/interview/settings.py`, 필요한 시작 설정 검사, `back-end/tests/test_interview_guidance.py`, `back-end/e2e/test_interview_guidance_http.py`.
- **작업:** `question_body`에서 저장된 값만 직렬화한다. LIST의 status 키를 제거하고 안내 없음은 null/빈 배열로 투영한다. 신규 제안 설정 `INTERVIEW_GUIDANCE_RESPONSES`는 기본 비활성으로 두고 저장과 송출을 분리한다.
- **완료·검증:** 스위치 off의 구 응답과 on의 기존 0.11.0 카드 계약 응답이 각각 유효하다. GET은 AI 호출·ID 발급·DB 쓰기를 하지 않는다. 이미 저장된 멱등 응답은 수정하지 않고 구 응답의 필드 누락도 허용한다.
- **커밋:** `feat(interview): 저장된 질문 안내의 호환 응답 제공`.

### B11 답변 처리 중 질문 스냅샷 복원

- [ ] `lastAnsweredQuestion`을 런타임에 구현한다.
- **선행:** B09, B10.
- **파일:** `back-end/app/interview/routes.py`, `common.py`, 필요한 snapshot 저장 migration, `back-end/tests/test_interview_guidance.py`, `back-end/e2e/test_interview_guidance_http.py`.
- **작업:** 답변 접수와 같은 트랜잭션에서 questionId·본문·guidance·카드·item 상태의 불변 snapshot을 확보한다. 질문 행 자체를 참조하는 구현은 행의 불변성을 증명하고 계약의 복사 의미와 맞춰 문서화한다. EVALUATION 처리/실패 동안만 answered=true로 반환한다.
- **완료·검증:** POST answers와 GET이 동일 snapshot을 제공하고 처리 중 questions는 빈 배열이다. 평가 재시도에도 CURRENT가 유지된다. 다음 질문/인텐트/초안 생성에서는 비워지며 재답변 대상이 될 수 없다. 실패한 답변·전사에는 snapshot 전환이 없다.
- **커밋:** `feat(interview): 답변 평가 중 질문 스냅샷 복원`.

## C 평가와 이해 요약 연결

### C01 한도 도달 시 부족 정보의 요약 전달

- [ ] 종료된 평가의 부족 정보를 이해 요약에 연결한다.
- **선행:** A03, B06.
- **파일:** `back-end/app/ai/contracts.py`, `prompts.py`, `back-end/app/interview/flow.py`, `tasks.py`, `back-end/tests/test_interview_reviews.py`, **신규** `back-end/e2e/test_interview_reviews_http.py`.
- **작업:** IntentSummaryRequest에 마지막 부족 측면을 전달하고 기존 summary+StructureSnapshot을 재사용한다. needs_detail bool만 전달하던 경로를 보완한다. null 시간·빈 절차·정확한 missing_information 연결과 외부 shift 참조 검증을 유지한다.
- **완료·검증:** 충분하면 즉시 요약, 부족하면 depth 1~5의 질문 하나, depth 5 종료면 NEEDS_DETAIL과 부족 설명이 유지된다. 재시도는 depth를 늘리지 않는다. 근무조 요약 대기·실패·잘못된 ref에서 추정 콘텐츠를 저장하지 않는다.
- **커밋:** `feat(interview): 부족 정보의 이해 요약 전달 보강`.

### C02 음성 정정에서 기존 콘텐츠 ID 보존

- [ ] 같은 항목의 수정으로 섹션 ID와 사진 연결이 바뀌지 않게 한다.
- **선행:** A03, C01.
- **파일:** `back-end/app/ai/prompts.py`, `provider.py`, `validation.py`, `back-end/app/interview/content.py`, `tasks.py`, `back-end/tests/test_interview_reviews.py`, `back-end/e2e/test_interview_reviews_http.py`.
- **작업:** 기존 ID 재사용과 정정 범위 검증을 보강한다. 명칭·설명·절차 수정은 같은 섹션 ID로 적용하고 사진·다른 섹션·외부 근무조 참조를 보존한다. 사진은 서버의 previous 콘텐츠에서 다시 붙인다.
- **완료·검증:** 이름 변경·절차 변경·NO_CHANGE·모호한 정정·AI 실패에서 기존 사진 및 무관한 내용이 보존된다. 새 ID로 기존 섹션을 대체해 사진을 유실시키는 결과는 저장하지 않는다. 명시적 삭제는 E02에서 검증한다.
- **커밋:** `fix(interview): 음성 정정의 섹션 식별자와 사진 보존`.

## D 요약 저장 후 사진 추천

### D01 사진 추천 AI operation 추가

- [ ] 독립적인 사진 추천 Structured Output을 제공한다.
- **선행:** A01, B01.
- **파일:** `back-end/app/ai/contracts.py`, `schemas.py`, `provider.py`, `prompts.py`, `fake.py`, `openai_provider.py`, `back-end/tests/test_ai_schemas.py`, `test_ai_provider.py`.
- **작업:** 제안 operation `suggest_review_photos`의 입력·결과·strict schema·provider/fallback 연결·fake·출력 예산을 추가한다. 입력은 저장된 요약과 실제 섹션 ID이며 출력은 suggestions뿐이다. 사진 바이트를 분석하는 기능을 추가하지 않는다.
- **완료·검증:** 빈 배열·단일/복수 섹션·nullable 설명/푸터를 검증한다. helpful·처리 상태·revision·attachmentTarget을 AI가 반환하면 거부한다. schema/provider/fake의 operation 목록이 일치한다.
- **커밋:** `feat(ai): 저장된 요약의 사진 추천 생성 추가`.

### D02 추천 저장과 내부 task 스키마 추가

- [ ] 추천을 review의 필수 처리 상태와 분리해 저장한다.
- **선행:** A01, B06.
- **파일:** `back-end/app/db/models.py`, 다음 head의 migration, `back-end/tests/test_migrations.py`, `test_schema_drift.py`, `test_tasks.py`.
- **작업:** review에 추천 카드 JSON과 추천 task ID·attempt·원본 review revision 등 필요한 최소 내부 필드를 둔다. 제안 task kind `REVIEW_PHOTO_SUGGESTIONS`와 DB kind 제약을 함께 추가한다. 별도 주제 엔티티를 만들지 않는다.
- **완료·검증:** 기존 review는 추천 없음으로 읽힌다. 추천 task 정보가 있어도 review.status=READY·processing=null이 가능하다. migration chain과 SQLite/MySQL CHECK가 일치하고 기존 task kind를 손상하지 않는다.
- **커밋:** `feat(db): 검토 사진 추천 저장과 작업 종류 추가`.

### D03 요약 저장 뒤 추천 작업 예약

- [ ] 최초 READY 요약에 대해서만 추천 작업을 예약한다.
- **선행:** A03, C01, D01, D02.
- **파일:** **신규** `back-end/app/interview/photo_suggestions.py`, **신규** `back-end/tests/test_interview_photo_suggestions.py`.
- **작업:** 저장된 READY 요약과 실제 sectionId로 추천 입력 snapshot과 task를 준비하는 예약 helper를 작성한다. 섹션이 없으면 예약하지 않는다. 입력은 불변 저장하고 worker가 이후 바뀐 요약을 임의로 읽어 대체하지 않는다. 제품의 요약 완료 hook은 성공/실패 handler가 완성되는 D05에서 연결한다.
- **완료·검증:** 한 요약당 중복 예약을 막고 마지막 인텐트도 예약 가능하다. 외부 호출은 DB 트랜잭션 밖에서 실행한다. 최대 크기 입력과 1 MB task payload 경계를 검사하고, 한도 초과로 추천을 생략해도 원본 요약은 보존한다. 선택 작업의 예약 실패는 복구 가능한 savepoint 등으로 요약을 보존하되 DB 전체 commit 실패를 성공으로 처리하지 않는다.
- **커밋:** `feat(interview): 저장된 요약의 사진 추천 작업 예약`.

### D04 추천 섹션 검증과 카드 변환

- [ ] AI 추천을 실제 첨부 대상이 있는 카드로 변환한다.
- **선행:** B03, B04, D01.
- **파일:** `back-end/app/interview/photo_suggestions.py`, `cards.py`, `back-end/tests/test_interview_photo_suggestions.py`.
- **작업:** sectionId가 대상 review의 저장된 구조에 있는지 검사한다. 유효한 추천마다 서버 카드/item UUID와 `type=PHOTO_SUGGESTIONS`, `attachmentTarget={intentId,target:SECTION,sectionId}`를 만든다. 서로 다른 섹션을 한 카드에 섞지 않는다.
- **완료·검증:** 다른 세션·다른 review·삭제된/없는 섹션·중복 ID·내용 한도 오류를 검증한다. 잘못된 추천을 제외한 후 유효한 것이 없으면 빈 배열이다. 추천 item ID를 sectionId/mediaId로 사용하지 않는다.
- **커밋:** `feat(interview): 사진 추천 대상 검증과 카드 변환`.

### D05 추천 결과의 적용과 선택적 실패

- [ ] 늦은 추천 결과와 실패가 필수 진행을 바꾸지 않게 한다.
- **선행:** D03, D04.
- **파일:** `back-end/app/interview/photo_suggestions.py`, `tasks.py`, `back-end/tests/test_interview_photo_suggestions.py`, **신규** `back-end/e2e/test_interview_photo_suggestions_http.py`.
- **작업:** execute/apply/fail handler 등록과 요약 완료 뒤 D03 helper 호출을 같은 커밋에서 활성화한다. 세션→review 잠금 순서에서 task ID·attempt·원본 revision·READY·생성 동결 여부를 검사한다. 검증된 추천 결과를 내부 저장하고 추천만으로 review revision을 올리지 않는다. 공개 질문 카드 적용과 session revision 갱신은 D06에서 처리한다. 타임아웃·잘못된 출력·제한 재시도 소진은 추천 없음으로 종료한다.
- **완료·검증:** 최신 정정/사진/확인/새 task가 먼저 저장됐거나 completion이 시작됐으면 이전 결과를 폐기한다. stale fail도 새 추천을 지우지 않는다. 실패가 session/review ERROR, 자동 답변, completion 대기 조건을 만들지 않는다. 본문·provider 원문을 로그에 남기지 않는다.
- **커밋:** `feat(interview): 사진 추천 결과의 안전한 적용과 실패 처리`.

### D06 기존 guidanceCards에 사진 추천 연결

- [ ] 저장된 추천을 기존 PHOTO_SUGGESTIONS 카드로 전달한다.
- **선행:** A02, B10, B11, D05.
- **파일:** `back-end/app/interview/common.py`, `flow.py`, `routes.py`, `photo_suggestions.py`, `back-end/tests/test_interview_photo_suggestions.py`, `back-end/e2e/test_interview_photo_suggestions_http.py`.
- **작업:** A02에서 확인한 전달 시점에 저장된 추천을 질문의 기존 guidanceCards와 합성하고 PHOTO_SUGGESTIONS·attachmentTarget을 그대로 사용한다. 카드가 담긴 질문의 intentId와 첨부 대상 intentId를 구분한다. review 응답·content에 새 필드를 추가하지 않는다. 합성 결과는 session revision과 함께 원자 저장하고 GET은 저장본만 직렬화한다.
- **완료·검증:** 기존 스키마와 승인 계약 snapshot 변경 없이 응답이 유효하다. LIST·진행 카드의 ID/순서를 보존하고 총 5개 제한을 지킨다. 답변 접수 뒤 질문·lastAnsweredQuestion을 뒤늦은 추천으로 변경하지 않는다. 처리 중 revision 변경으로 기존 질문 생성/평가 task가 무효화되지 않게 적용 시점을 제어한다. 다른 review 대상, 정정·삭제·completion 경합, 재조회·복원을 검증한다. 마지막 인텐트·질문 없는 상태는 추천 카드 없이 기존 수동 사진 첨부가 가능함을 검증한다. 늦은 결과·공간 부족은 기존 카드·상태를 보존하고 진행을 막지 않는다.
- **커밋:** `feat(interview): 기존 안내 카드에 사진 추천 연결`.

### D07 내용 정정에 따른 추천 무효화

- [ ] 정정된 내용에 이전 사진 추천이 남지 않게 한다.
- **선행:** C02, D05, D06.
- **파일:** `back-end/app/interview/routes.py`, `tasks.py`, `photo_suggestions.py`, `back-end/tests/test_interview_photo_suggestions.py`, `back-end/e2e/test_interview_photo_suggestions_http.py`.
- **작업:** 정정 접수 시 이전 추천 작업과 아직 답변하지 않은 질문의 사진 카드 유효성을 해제하고 실제 내용 정정 성공 뒤 새 저장 구조로 추천을 예약한다. NO_CHANGE이면 보존한 이전 추천 snapshot을 같은 콘텐츠에 유효한지 검사한 뒤 D06의 적용 규칙으로 복원한다. 이미 답변한 질문 snapshot은 수정하지 않는다. 단순 확인·사진 순서 변경은 무조건 재생성하지 않는다. 처리 중/실패에도 기존 첨부 사진은 유지한다.
- **완료·검증:** 정정 전 worker의 성공/실패가 뒤늦게 와도 새 요약·카드·revision을 바꾸지 않는다. 삭제된 섹션 추천은 재노출되지 않는다. 사진 추천이 다시 실패해도 수정된 이해 요약을 표시할 수 있다.
- **커밋:** `feat(interview): 요약 정정에 따른 사진 추천 갱신`.

## E 첨부 사진의 초안과 게시 연속성

### E01 업로드와 연결 재시도 검증

- [ ] 기존 media와 photos API로 사진 첨부를 완성한다.
- **선행:** A03, D06.
- **파일:** `back-end/app/manual_media.py`, `back-end/app/interview/routes.py`, `back-end/app/media/references.py`, `back-end/tests/test_manual_media_api.py`, `test_interview_reviews.py`, **신규** `back-end/e2e/test_interview_photo_lifecycle_http.py`.
- **작업:** 기존 업로드와 전체 사진 목록 교체 API를 재사용한다. 첨부 직전 최신 검토·revision·현재 사진을 읽고 기존 목록을 유지한 채 추가한다. 연결 실패 시 기존 mediaId를 재사용한다.
- **완료·검증:** 업로드 실패/연결 실패/응답 유실/동일 key replay/다른 body 충돌, 다른 매장 파일, 삭제된 파일, 만료된 revision, 중복 첨부를 검증한다. 사진 없이 계속하기에서 빈 photos PUT으로 기존 사진을 지우지 않는다.
- **커밋:** `test(interview): 사진 업로드와 연결 재시도 회귀 검증`.

### E02 삭제된 섹션의 사진 참조 정리

- [ ] 명시적으로 삭제된 섹션의 참조만 해제한다.
- **선행:** C02, D07, E01.
- **파일:** `back-end/app/interview/content.py`, `tasks.py`, `back-end/app/media/references.py`, `back-end/tests/test_interview_reviews.py`, `back-end/e2e/test_interview_photo_lifecycle_http.py`.
- **작업:** 삭제 intent가 분명한 기존 정정 경로에서만 해당 section 사진 연결과 추천을 정리한다. 다른 section·확인 이력·초안·게시본이 참조하는 파일을 보존한다. 제목이 비슷한 섹션으로 사진을 옮기는 보정은 하지 않는다.
- **완료·검증:** 한 사진의 복수 참조, 부분 삭제, 참조 정리 실패 rollback, 삭제·사진 연결 경합을 검증한다. AI의 단순 ID 누락은 묵시적 사진 이동이나 파일 삭제로 처리하지 않는다.
- **커밋:** `test(interview): 섹션 삭제의 사진 참조 보존 검증`.

### E03 completion의 snapshot과 경합 검증

- [ ] 모든 검토의 실제 콘텐츠와 사진을 같은 시점으로 고정한다.
- **선행:** C01, D05, E01.
- **파일:** `back-end/app/interview/drafting.py`, `routes.py`, `back-end/tests/test_interview_reviews.py`, `back-end/e2e/test_interview_photo_lifecycle_http.py`.
- **작업:** 기존 READY·전체 reviewRevisions·세션 revision·근무조 참조 검사와 generation_input_snapshot을 재사용한다. 추천 메타데이터를 생성 콘텐츠에 넣지 않고 추천 task 완료·confirmedAt을 새 필수 조건으로 만들지 않는다.
- **완료·검증:** 누락/중복/낡은 review revision, PROCESSING/ERROR 요약, 끊긴 shift 참조는 기존 계약대로 거절된다. 사진 변경·정정·추천 결과와 completion 경합은 먼저 잠금을 획득한 변경만 반영하며 거절 경로에 부분 snapshot이 없다.
- **커밋:** `test(interview): 사진과 추천 작업의 초안 생성 경합 검증`.

### E04 최초 초안의 섹션과 사진 보존

- [ ] 요약 사진을 같은 ID의 초안 섹션에 이어 붙인다.
- **선행:** E02, E03.
- **파일:** `back-end/app/ai/provider.py`, `validation.py`, `back-end/app/interview/drafting.py`, `back-end/app/manual_editing.py`, `back-end/tests/test_interview_reviews.py`, `test_manual_content.py`, `back-end/e2e/test_interview_photo_lifecycle_http.py`.
- **작업:** 기존 compose_draft의 검토 항목 ID 보존 검사와 draft_content 사진 결합을 재사용한다. model 구조와 서버 사진을 합친 후 기존 콘텐츠 검증·issue 생성·초기 기록을 적용한다.
- **완료·검증:** 모든 원본 section/shift ID·사진 순서·title/caption이 유지된다. AI의 섹션 유실·ID 교체, 생성 실패·재시도·중복 apply에서 사진이 사라지거나 중복되지 않는다. 최초 초안 revision=1과 READY/COMPLETED의 원자 저장을 확인한다.
- **커밋:** `test(manual): 최초 초안 생성의 사진 연속성 검증`.

### E05 최종 초안 음성 정정의 사진 보존

- [ ] 생성 후 정정에서도 같은 초안의 사진을 유지한다.
- **선행:** E04.
- **파일:** `back-end/app/manual_corrections.py`, `manual_editing.py`, `back-end/tests/test_manual_corrections.py`, `back-end/e2e/test_interview_photo_lifecycle_http.py`.
- **작업:** 기존 draft/corrections의 MANUAL/SHIFT/SECTION 정정과 버전·revision guard를 이용한다. 완료된 인터뷰를 다시 열지 않고, 대상 외 내용과 사진을 보존한다.
- **완료·검증:** APPLIED·NO_CHANGE·모호함·참조 충돌·AI 실패·실패 재시도·새 초안 교체·늦은 worker를 검사한다. 실제 내용 변경만 부족 항목 확인을 무효화하고 정정 처리 중 게시를 막는다.
- **커밋:** `test(manual): 초안 음성 정정의 사진 보존 검증`.

### E06 미리보기와 게시본 사진 일치

- [ ] 최종 검토부터 근무자 열람까지 저장된 사진이 일치하는지 검증한다.
- **선행:** E04, E05.
- **파일:** `back-end/app/manual_drafts.py`, `manual_content.py`, `manual_published.py`, `manual_media.py`, `back-end/tests/test_manual_drafts.py`, `test_manual_published.py`, `test_manual_media_api.py`, `back-end/e2e/test_interview_photo_lifecycle_http.py`.
- **작업:** 같은 versionId·revision의 draft/preview 콘텐츠와 섹션 사진을 비교한다. 기존 부족 항목 확인과 게시 트랜잭션을 통해 게시하고 현재 게시본의 보호된 사진 조회를 검사한다.
- **완료·검증:** 미확인 issue·낡은 버전/revision은 게시를 거절한다. 점주는 자신의 연결 사진, 근무자는 접근 가능한 현재 게시본 사진만 조회한다. 게시 후 사진 불변성·권한 상실·다른 매장 접근·중복 게시 요청을 검증한다.
- **커밋:** `test(manual): 미리보기와 게시본의 사진 일치 검증`.

## F 별도 프론트엔드 연동

이 다섯 작업은 `front-end/dev`의 해당 영역에서 수행한다. 백엔드 워크트리에 프론트 코드를 가져오지 않는다. 실제 파일 위치는 프론트 작업 시작 시 확인하고 `src/manual/`의 기존 타입·서비스·화면·테스트를 사용한다. 백엔드 기능의 완료와 전체 사용자 흐름의 완료를 구분한다.

### F01 기존 안내 카드 계약의 프론트 수용 확인

- [ ] 기존 0.11.0 카드 계약을 프론트 타입과 검증기가 수용하는지 확인한다.
- **선행:** A02.
- **대상:** 프론트 매뉴얼 API 타입·계약 snapshot·HTTP 어댑터·계약 테스트.
- **작업:** 기존 guidanceCards의 세 카드 유형, attachmentTarget, lastAnsweredQuestion 지원 여부를 확인하고 구현 누락만 보완한다. 필드 누락은 구 응답, null/빈 배열은 안내 없음으로 처리한다. 사진의 연결 대상은 질문의 intentId가 아니라 attachmentTarget으로 해석한다. 새로운 review 필드를 도입하지 않는다.
- **완료·검증:** 기존 API 계약을 그대로 사용하는 응답이 유효하다. 프론트가 이전 계약을 쓰는 경우 기존 0.11.0 수용 작업을 새 API 계약 개정으로 취급하지 않는다. 렌더러·검증기 준비 전에는 저장된 카드의 송출을 활성화하지 않는다.
- **커밋:** `test(manual): 기존 안내 카드 계약 수용 검증`.

### F02 세 카드 유형 렌더러 구현

- [ ] LIST·PROGRESS_CHECKLIST·PHOTO_SUGGESTIONS를 의미에 맞게 표시한다.
- **선행:** F01.
- **대상:** 프론트 매뉴얼 카드 컴포넌트·컴포넌트 테스트.
- **작업:** 서버 순서·ID·상태대로 읽기 전용 목록을 렌더링한다. 안내/설명/푸터 null을 처리하고 NEEDS_DETAIL을 완료로 표시하지 않는다. 요약 아이콘과 checklistItem을 별도 용도로 유지한다.
- **완료·검증:** CURRENT 0/1개, 긴 목록·설명 없음·카드 없음·복수 사진 대상, 안정 ID 재정렬이 정상이다. 클릭/체크로 서버 업무 완료를 변경하지 않는다.
- **커밋:** `feat(manual): 인터뷰 안내 카드 표시 추가`.

### F03 요약 우선 표시와 이어하기

- [ ] 다음 질문이 준비돼도 미확인 요약을 먼저 표시한다.
- **선행:** B11, D06, F02.
- **대상:** 프론트 인터뷰 흐름·세션/review 조회 어댑터·상태 전이 테스트.
- **작업:** 시작/복귀 시 세션과 검토를 조회하고 미확인 READY 요약을 우선 선택한다. 최초 정상 진행에서는 추천 대상이 준비됐을 때 선택 사진 단계를 거친다. 명시적 이어하기는 기존 요약과 사진부터 복원한다. 요약 생성 대기와 실제 질문 생성 시점을 구분한다.
- **완료·검증:** 선생성 질문·마지막 인텐트·여러 미확인 요약·정정 후 재표시·매장 전환·늦은 응답·새로고침을 검증한다. 추천이 늦게 도착해도 이미 떠난 화면으로 강제 복귀하지 않는다. EVALUATION에서는 lastAnsweredQuestion을 표시용으로만 쓰며 재제출하지 않는다. 요약 오류의 복구가 질문 데이터를 덮어쓰지 않는다.
- **커밋:** `feat(manual): 이해 요약 우선 표시와 인터뷰 복원`.

### F04 사진 선택과 두 단계 재시도

- [ ] 사진 선택·업로드·연결의 상태를 각각 관리한다.
- **선행:** D06, E01, F02, F03.
- **대상:** 프론트 사진 선택기·media 업로드·review/photos 서비스·실패 복구 테스트.
- **작업:** 선택 취소는 추천으로 복귀하고 업로드 실패는 업로드만 재시도한다. 연결 실패는 보관한 mediaId로 연결만 재시도한다. 충돌 시 최신 검토를 다시 읽고 대상과 기존 사진 목록을 확인한다.
- **완료·검증:** 사진 없이 계속해도 이미 연결한 사진이 남는다. 중복 클릭·응답 유실·취소·화면 이탈·삭제된 대상·정정 중 대상·revision 충돌을 처리한다. 녹음 시간과 파일 선택 상태를 AI 출력으로 요청하지 않는다.
- **커밋:** `feat(manual): 사진 첨부 단계별 재시도 처리`.

### F05 최종 검토와 미리보기의 동일 콘텐츠 사용

- [ ] 최종 검토와 근무자 미리보기에서 같은 초안을 표시한다.
- **선행:** E06, F03, F04.
- **대상:** 프론트 초안 조회·정정·미리보기·게시 화면과 테스트.
- **작업:** 서버의 versionId·revision·ManualContent를 공통 렌더러에 전달한다. 음성 정정 성공 후 다시 조회하고 section 사진을 표시한다. 부족 항목 보완/확인 후 최신 버전으로 게시한다.
- **완료·검증:** 미리보기가 별도 초안을 생성하지 않는다. 오래된 응답·정정 처리 중·게시 충돌에서 새 초안 ID로 자동 치환하거나 자동 게시하지 않는다. 사진의 위치와 순서가 서버 저장본과 같다.
- **커밋:** `feat(manual): 최종 검토와 미리보기 사진 표시 통합`.

## G 통합 검증과 반영

### G01 질문과 요약의 전체 HTTP 회귀

- [ ] 질문부터 모든 요약 완료까지 하나의 실제 흐름을 검증한다.
- **선행:** B11, C01, D07.
- **파일:** `back-end/e2e/test_interview_guidance_http.py`, `test_interview_evaluation_http.py`, `test_interview_reviews_http.py`, `test_interview_photo_suggestions_http.py`.
- **작업:** 각 태스크의 개별 E2E에 더해 시작→질문→음성 전사→답변→평가→추가 질문/요약→다음 주제→마지막 요약을 이어 실행한다. 카드만 오류·질문 fallback·평가/전사 실패·depth 5·추천 실패를 별도 시나리오로 연결한다.
- **완료·검증:** 실제 HTTP 응답과 독립 DB의 질문·카드·평가·review·task·revision을 대조한다. 거절·DB 실패 뒤 기존 상태 보존, 멱등 재실행, 서버 재시작 뒤 복원을 확인한다. 예기치 않은 skip/xfail은 완료로 보지 않는다.
- **커밋:** `test(interview): 질문과 이해 요약의 전체 HTTP 흐름 검증`.

### G02 사진부터 게시까지 전체 HTTP 회귀

- [ ] 사진이 마지막 게시본까지 남는 사용자 시나리오를 검증한다.
- **선행:** D07, E06.
- **파일:** `back-end/e2e/test_interview_photo_lifecycle_http.py`.
- **작업:** 추천→업로드→연결→이해 요약 음성 정정→completion→초안 정정→preview→부족 항목 확인→게시→근무자 사진 조회를 연결한다. 사진 없는 분기와 일부 사진만 연결하고 계속하는 분기도 포함한다.
- **완료·검증:** 단계별 sectionId/mediaId·사진 메타데이터·참조 행을 대조한다. 동시 정정/연결/생성, 업로드 성공 후 연결 실패, 삭제, 늦은 결과, 권한 상실에서도 중복·유실·임의 이동·부분 commit이 없다.
- **커밋:** `test(manual): 사진 첨부부터 게시까지 전체 흐름 검증`.

### G03 AI 출력 의미 품질 평가

- [ ] 구조 검증으로 보장할 수 없는 질문·카드·추천 의미를 평가한다.
- **선행:** B08, C01, D01, D04.
- **파일:** `back-end/evals/`, `back-end/tests/test_ai_provider.py`, `test_interview_live.py`, 필요한 신규 opt-in live test.
- **작업:** 예시만 나온 매장, 실제 두 업무, 부분 충족, 대상 없는 전체 목록 질문, 부족 종료, 근무조 없는 응답, 관계없는 사진, 사진이 필요 없는 업무, 섹션 ID 왜곡, 이름 변경 정정 사례를 gold fixture로 만든다. 기존 오프라인 평가 도구를 재사용한다.
- **완료·검증:** 근거 없는 업무 확정·자동 완료·부적절한 사진·잃어버린 부족 정보를 분류해 보고한다. 실제 provider 평가는 비용·키를 갖춘 기존 opt-in 경로에서만 실행하고 모델/프롬프트 버전·표본 수·실패를 기록한다. fake 통과를 실제 AI 품질 통과로 표시하지 않는다.
- **커밋:** `test(ai): 인터뷰 카드와 사진 추천 의미 평가 추가`.

### G04 독립 에이전트의 통합 누락 점검

- [ ] 작성에 참여하지 않은 독립 에이전트가 전체 계약과 테스트를 재점검한다.
- **선행:** G01, G02, G03, F05.
- **대상:** 변경 diff, 요구사항 대응표, 실제 코드·테스트·JUnit·미검증 목록.
- **작업:** 정상/거부/실패/재시도/동시성 분기를 대조한다. 응답만 검사하고 DB commit을 빠뜨린 테스트, fixture가 우회한 동작, snapshot 충돌·사진 삭제·마지막 인텐트·구 응답 누락을 찾는다.
- **완료·검증:** 한국어 검토 결과와 반영 목록을 남기고 결함을 보완한 뒤 영향 테스트를 재실행·재검토한다. 실제 AI·브라우저·배포 서버의 미검증을 분리한다. 검토자를 확보하지 못했으면 완료로 표시하지 않는다.
- **산출 경계:** 검토 기록 하나. 발견된 결함은 관련 도메인의 별도 최소 수정 커밋으로 처리한다.

### G05 구현 문서와 호환 전환 절차 정리

- [ ] 실제 구현과 배포 순서에 맞게 문서를 갱신한다.
- **선행:** G04, F01, F02, F03, F04, F05.
- **파일:** `back-end/README.md`, `back-end/docs/manual-interview-design.md`, `ai-foundation.md`, `manual-authoring-mvp-plan.md`, 필요한 배포 설정 설명.
- **작업:** 타입 매핑·저장 위치·추천의 비필수성·revision·사진 유지·스위치·장애 복구를 구현 기준으로 정리한다. 기존 계약을 수용하는 프론트 검증기/렌더러/흐름 배포→서버 기능 준비→환경별 응답 송출 활성화 순서를 기록한다.
- **완료·검증:** 카드가 없는 구 응답과 기존 멱등 replay의 처리까지 설명한다. 롤백은 먼저 기존 선택 카드 필드의 송출을 끄고 기존 데이터·첨부를 보존하는 방식으로 정의한다. 활성 worker가 새 task를 실행 중일 때 DB 컬럼을 먼저 제거하지 않는다.
- **커밋:** `docs(interview): 구조화 출력 운영과 호환 전환 절차 정리`.

### G06 구현 PR의 통합 조건 확인

- [ ] 구현 결과를 이슈·PR·검증 근거와 연결한다.
- **선행:** G04, G05.
- **대상:** 실제 구현 브랜치와 `.github/pull_request_template.md`, 협업 문서.
- **작업:** 코드와 해당 테스트의 원자 커밋, 올바른 dev 대상, Assignee·작성자 기준 Reviewer, Refs 연결, 프론트/백엔드 의존 PR 순서를 확인한다. 병합·배포 실행은 그 단계에서 부여된 작업 권한에 따른다.
- **완료·검증:** 필수 검사·독립 검토·필요한 연동 검증과 관련 dev PR의 병합을 확인한 뒤 이슈 완료 여부를 판정한다. main 릴리즈·운영 검증은 별도로 추적한다. 구현 계획 작성이나 일부 PR만으로 전체 완료를 선언하지 않는다.
- **산출 경계:** 통합 체크 결과와 PR 연결. 계획 단계에서는 원격 이슈/PR/리뷰 요청을 생성하지 않는다.

## 실행 순서와 병렬 가능 구간

| 묶음 | 태스크 | 착수 조건 |
| --- | --- | --- |
| 계약과 기반 | A01 → A02, A03 | 기준 브랜치 재확인 후 시작 |
| 질문 타입와 저장 | B01 → B02/B03 → B04, B05 → B06 | B01과 B05의 타입 합의, migration은 순서대로 |
| 질문 연결 | B07 → B08 → B09 → B10 → B11 | 각 태스크의 선행 조건 충족 |
| 요약과 정정 | C01 → C02 | B06 이후 질문 연결과 병행 가능 |
| 사진 추천 | D01과 D02 → D03, D04 → D05 → D06 → D07 | D02는 B06 뒤 migration head에 연결 |
| 사진 연속성 | E01 → E02/E03 → E04 → E05 → E06 | D06의 기존 카드 전달과 D07의 무효화 범위 확인 |
| 프론트 | F01 → F02 → F03 → F04 → F05 | 별도 프론트 브랜치. A02 이후 mock 계약 작업 가능 |
| 종합 검증 | G01/G02/G03 → G04 → G05 → G06 | 구현별 테스트·독립 점검은 그 전에 이미 완료 |

논리적으로 병행할 수 있어도 `contracts.py`, `schemas.py`, `provider.py`, `tasks.py`, `models.py`, `openapi.yaml`과 migration head를 동시에 편집하지 않도록 담당 범위를 조정한다. 기존 기능이 검증된 E 영역은 회귀 테스트 중심으로 진행하며, 실패를 발견한 지점만 수정한다.

## 요구사항 대응표

| 첨부 요구사항 | 태스크 |
| --- | --- |
| 질문·guidance·두 카드 유형의 단일 AI 출력 | B01, B02, B08, B09 |
| 카드/item ID 유지와 진행 상태 제약 | B03, B04, B07, B11, F02 |
| 실제 답변과 일반 예시 구분 | B07, B08, G03 |
| 충분성 평가·추가 질문 한도·부족 정보 보존 | B06, C01, G01 |
| 짧은 이해 요약과 기존 구조 재사용 | C01, C02 |
| 요약 저장 후 별도 사진 추천·빈 배열 | D01, D02, D03, D06 |
| 유효한 섹션의 attachmentTarget·추천 실패 허용 | D04, D05, D07 |
| 선택 취소·업로드 실패·연결만 재시도·사진 없이 계속 | E01, F04, G02 |
| 음성 수정 시 ID와 사진 유지·삭제 예외 | C02, D07, E02, E05 |
| 전체 READY 요약과 revision 기반 초안 생성 | E03, E04 |
| 최종 검토·미리보기·게시의 같은 사진 | E05, E06, F05, G02 |
| 이어하기와 이해 요약 우선 표시 | B11, F03 |
| API 구 응답·엄격한 프론트 검증기 호환 | A02, B10, D06, F01, G05 |
| 처리 상태·revision의 서버 책임과 프론트 로컬 상태 | A01, D05, F03, F04 |
| InterviewSubject 미도입·AI 작성용 미디어와 분리 | A01, D01, D02 |

## 구현 시 검증 실행

이 절차는 구현 단계에서 실행한다. 현재 계획 작성만으로 아래 검사를 통과했다고 간주하지 않는다. 실행 위치와 DB 경계를 구분한다.

| 검사 | 실행 위치와 명령 | 판정 |
| --- | --- | --- |
| API 계약 | 저장소 루트에서 `npm ci --prefix back-end/docs`, `npm run check --prefix back-end/docs` | 기존 계약에 대한 구현 응답·예시·구 응답 호환. 승인 snapshot 불변 확인 |
| 정적 검사 | back-end에서 `python -m ruff check .` | 변경 영역을 포함한 lint |
| 관련 Python 검사 | back-end에서 `python -m pytest tests/test_ai_schemas.py tests/test_ai_provider.py tests/test_interview_api.py tests/test_interview_reviews.py tests/test_manual_corrections.py tests/test_manual_media_api.py` 및 각 태스크의 신규 파일 | 빠른 회귀 확인. MySQL skip을 성공 증거로 사용하지 않음 |
| 실제 HTTP와 MySQL | 저장소 루트에서 `back-end/testing/run-e2e.sh` | 폐기용 Compose와 테스트 DB, 실제 서버 HTTP, 별도 DB commit 확인, JUnit 결과 |
| AI 의미 평가 | 기존 `back-end/evals/`와 opt-in live 테스트 | fake/offline/live 결과를 구분. 실제 모델과 프롬프트 버전 기록 |
| 브라우저 연동 | F01~F05 구현 브랜치와 API를 연결해 지정 시나리오 실행 | 화면 순서·마이크·선택 취소·새로고침·사진 복구. 백엔드 HTTP 검사와 별도 |

자동 runner가 사용하는 격리 DB 외의 개발·운영 DB를 테스트용으로 초기화하지 않는다. 기존 도구의 승인된 skip 대응 규칙을 따르고 신규 테스트의 누락/예기치 않은 skip·xfail을 숨기지 않는다. 전체 제품 완료는 40개 태스크의 해당 조건과 실제 연동 검증으로 판정하며, 백엔드 단독 준비 완료는 프론트 다섯 항목과 운영 활성화의 남은 상태를 명시한다.

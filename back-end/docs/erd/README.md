# 지단 MVP ERD

Figma의 입력·조회·상태 흐름과 OpenAPI를 연결한 **구현 전 관계형 데이터 설계**다. DB 마이그레이션이나 API 구현 완료를 뜻하지 않는다.

2026-10-05 사용자 결정에 따라 **근무 요청 수락 시 공고 자동 마감, 근무 시작 전 확정 철회 시 모집 재개**를 우선 반영했다. 나머지 충돌은 [OpenAPI 0.8.0](https://github.com/2026-KW-HACKATHON/29_Jidan/blob/b7c10a6/back-end/openapi.yaml)과 [API PR #91](https://github.com/2026-KW-HACKATHON/29_Jidan/pull/91)을 기준으로 정합화했다. ERD는 [PR #95](https://github.com/2026-KW-HACKATHON/29_Jidan/pull/95)에서 별도 검토하며 두 브랜치의 선후 병합과 관계없이 이 계약을 구현 기준으로 사용한다.

```mermaid
erDiagram
    USERS ||--o| WORKER_PROFILES : has
    WORKER_PROFILES ||--o{ WORKER_CAREERS : records
    WORKER_PROFILES ||--|{ AVAILABILITY_RULES : sets
    USERS ||--o{ STORES : owns
    STORES ||--|| STORE_APPROVAL_REQUESTS : requires
    STORES ||--o{ STORE_INVITATIONS : sends
    STORE_INVITATIONS ||--o| STORE_ACCESS_GRANTS : grants
    STORES ||--o{ JOB_POSTINGS : publishes
    JOB_POSTINGS ||--o{ JOB_APPLICATIONS : receives
    JOB_APPLICATIONS ||--o{ WORK_REQUESTS : prompts
    WORK_REQUESTS ||--o| SHIFT_ASSIGNMENTS : confirms
    JOB_POSTINGS ||--o{ SHIFT_ASSIGNMENTS : records
    SHIFT_ASSIGNMENTS ||--o| STORE_ACCESS_GRANTS : grants
    USERS ||--o{ FAVORITE_STORES : saves
    STORES ||--o{ FAVORITE_STORES : saved_by
    STORES ||--o| STORE_MANUALS : owns
    STORE_MANUALS ||--o{ MANUAL_VERSIONS : versions
    MANUAL_VERSIONS ||--o{ MANUAL_SECTIONS : contains
    STORES ||--o{ MANUAL_QA_CONVERSATIONS : hosts
    USERS ||--o{ MANUAL_QA_CONVERSATIONS : owns
    MANUAL_QA_CONVERSATIONS ||--o{ MANUAL_QA : contains
    MANUAL_VERSIONS ||--o{ MANUAL_QA : grounds
    USERS ||--o{ NOTIFICATIONS : receives
```

## 상세 ERD

| 흐름 | 문서 | 주요 테이블 |
| --- | --- | --- |
| Google 로그인·가입 유형 | [인증 주체](auth.md) | users |
| 기본 정보·경력·가능 시간 | [일반회원 프로필](worker.md) | worker_profiles, worker_careers, availability_rules, availability_days |
| 점주 매장 등록·운영 승인 | [매장 승인](store.md) | stores, store_approval_requests |
| 초대·정기/임시 자료 접근 | [매장 접근](access.md) | store_invitations, store_access_grants |
| 대타 공고·지원·요청·확정 | [대타 공고](jobs.md) | job_postings, job_applications, application_careers, work_requests, shift_assignments, application_selection_effects |
| 관심 매장 | [관심 관계](favorite-store.md) | favorite_stores |
| AI 인터뷰·검토·사진·게시 | [업무 매뉴얼](manual.md) | store_manuals, manual_versions, manual_shifts, manual_sections, manual_steps, manual_media, manual_photo_attachments, interview_turn_photos, interview_question_sets, interview_intents, interview_sessions, interview_session_intents, interview_intent_reviews, interview_probe_batches, interview_turns, interview_evaluations, manual_review_issues, manual_issue_acknowledgements |
| 질문·답변별 평가와 추가 질문 | [AI 인터뷰 실행 흐름](ai-interview-flow.md) | 질문 하나 → 답변 하나 → Jev 판단, depth 0~5 |
| 근무자 대화·질문·인용 | [AI 업무 질문](qa.md) | manual_qa_conversations, manual_qa, manual_qa_citations, qa_media, manual_qa_photos |
| 점주·일반회원 알림 | [앱 알림](notification.md) | notifications |

## 이번 정합화에서 확정한 기준

- User·Worker·Store/승인의 기존 기본 관계를 유지한다. 사용자당 WORKER/OWNER 단일 역할, 매장당 점주 한 명, 최초 승인 신청 한 건이 현재 범위다.
- 초대 링크는 생성 후 7일, 접근 종료일은 선택 값이다. 거절 이력과 재전송 토큰 교체를 지원하고 수락·기한·수동 종료를 분리한다.
- 재지원은 새 지원서와 제출 당시 이름·나이·경력 snapshot을 만든다. 근무 요청은 min(요청+1시간, 근무 시작)에 만료하며 유효 대기 요청은 공고당 하나다.
- 수락 시 CLOSED/closed_at 기록, 시작 전 확정 철회 시 RECRUITING/closed_at=NULL 복구. 확정 이력은 삭제하지 않고 활성 확정만 하나로 제한한다. 철회 시 그 요청 때문에 바뀐 지원만 복구한다.
- 공고/근무 요청/지원 상태는 OpenAPI 용어와 맞춘다. 수치형 경력 기준처럼 의도적으로 다른 저장 표현은 도메인 문서에 API 매핑을 명시한다. API 상태가 조합/시간으로 계산 가능하면 동일한 status 컬럼을 중복 저장하도록 강제하지 않는다.
- 인터뷰의 추가 질문도 한 개씩 생성·답변·평가한다. depth 5 부족은 NEEDS_DETAIL을 유지하고 바로 다음 인텐트로 이동한다. 인텐트별 검토와 최종 초안의 revision·확인 이력을 구분한다.
- 부족 항목은 최종 점주 확인만으로 게시할 수 있다. 미확정 필드는 공개 설명과 함께 남기고 게시본·당시 질문 근거는 불변 보존한다.
- 매뉴얼 업로드 자산과 사진 첨부를 분리한다. 근무자 Q&A는 본인 대화·질문·입력·인용을 보존하고 현재 자료 접근을 매번 확인한다.
- 관심 관계와 알림 typed target을 보완한다. 알림은 개별 읽음이며 모두 읽음 화면을 일괄 변경 API로 해석하지 않는다.

## 물리 스키마와 구현 책임

- 도식의 uuid는 논리 타입이다. MySQL의 BINARY(16)/CHAR(36), 문자열 길이·collation, nullable 컬럼, FK 삭제 정책, 인덱스와 마이그레이션은 구현 단계에서 정한다. 기록을 보존해야 하는 참조에는 무조건적인 cascade 삭제를 적용하지 않는다.
- 저장 시각은 UTC, 근무 날짜·요일 계산은 Asia/Seoul이다. 접근·요청 기한은 끝 시각을 제외한다. API startAt/endAt, 상태·건수·권한·화면 phase는 기존 행에서 계산할 수 있으며 별도 테이블을 요구하지 않는다.
- revision은 같은 리소스의 변경 경쟁을 막는 값이고 게시 버전 번호와 다르다. 상태 전이·지원 복구·접근 생성/종료·알림 outbox는 공고 또는 매뉴얼의 트랜잭션 경계로 묶는다. 외부 AI·메일 실행 동안 DB 트랜잭션을 열어 두지 않는다.
- 서버 세션/OAuth, 멱등성 결과, 작업 큐·전사·자동 재시도·fallback, outbox의 저장 매체와 운영 스키마는 공통 영속성 설계에서 정한다. 응답을 다시 조회해야 하는 처리 상태·입력·결과는 내구성 있게 저장하되, endpoint마다 새 도메인 테이블을 만들 필요는 없다.
- nullable 키의 UNIQUE 동작, 활성 지원/확정의 중복 차단, 같은 매장/버전/인텐트 귀속, snapshot 내부 참조는 DB 제약과 서비스 검증을 함께 설계한다. 문서 정합성 검사가 실제 DB 경합·rollback 검증을 대신하지 않는다.

## 후속 결정과 범위

다중 역할·공동 관리·소유권 이전·승인 거절/재신청, 모집 인원 확대, 계정 삭제와 장기 보존은 현재 MVP 밖이다. 세션/멱등성/파일 보존 상한과 근무 중첩 차단 등 OpenAPI의 보완 설계는 구현 시 검증한다. 이미 결정된 재지원·요청 만료·심야 표현·토큰 교체·부족 항목 확인 발행을 미결정 항목으로 되돌리지 않는다.

Figma의 Design 화면을 주요 근거로 사용했고 Design System/Wireframe/Image Assets/IR Deck으로 용어와 범위를 확인했다. 화면 밖 정책은 사용자 결정 또는 OpenAPI 보완 계약으로 구분한다. 독립적인 정기/일반 일정·체크리스트 수행 기록·실제 급여 지급·AI 실행 구현은 이 ERD 작업에 포함하지 않는다.

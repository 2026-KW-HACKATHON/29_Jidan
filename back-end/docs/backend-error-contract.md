# 백엔드 오류 계약 정리

승인 기준: 원격 `back-end/dev` `5fd6896955ca9133c5b8967485438dd86eceefc4`의 OpenAPI 0.10.0. 현재 로컬 구현도 102개 operation이며 요청·성공 응답 schema 변경은 없다.

사용자 결정(2026-10-07): 원격 응답 선언은 유지한다. 로컬에서 삭제한 23개 선언은 복원했다. 아래 삭제 행은 채택하지 않은 이전 로컬 제안이며, 실제 분기가 아직 관측되지 않았다는 근거로만 남긴다. 추가 사용자 결정(2026-10-07): 원격 선언을 모두 유지하면서 검증된 25개(400 18개, 503 7개)를 보완한다. 최종 로컬 계약 버전은 0.10.1이며 원격 업로드·팀 PR 합의는 별도 절차다. 선언 누락을 감추려고 동작을 임의 변경하거나 미도달 응답을 인위적으로 만들지 않는다.

| Operation | 응답 | 변경안 | 동작 근거 |
| --- | --- | --- | --- |
| `acknowledgeManualIssues` | 503 | 원격 선언 복원·유지 | DB만 원자적으로 변경. 미디어 삭제는 commit 뒤 best effort와 retention 재시도; 저장·작업 예약 경계 없음. |
| `applyForJobPosting` | 400 | 사용자 승인·추가 보완 | JSON body 구문 오류: 400 INVALID_REQUEST; 필드 형식 오류는 별도 422. |
| `approveStoreApprovalRequest` | 400 | 사용자 승인·추가 보완 | JSON body 구문 오류: 400 INVALID_REQUEST; 필드 형식 오류는 별도 422. |
| `askManualQuestion` | 400 | 사용자 승인·추가 보완 | JSON body 구문 오류: 400 INVALID_REQUEST; 필드 형식 오류는 별도 422. |
| `askManualQuestion` | 503 | 사용자 승인·추가 보완 | 실제 저장/작업 예약 실패: 503 JOB_QUEUE_UNAVAILABLE (미디어 저장 실패 포함); 변경 rollback. |
| `cancelStoreInvitation` | 400 | 원격 선언 복원·유지 | 본문 없는 요청은 JSON parser를 호출하지 않음. 미디어 업로드는 multipart 형식 검증(422). |
| `closeJobPosting` | 400 | 사용자 승인·추가 보완 | JSON body 구문 오류: 400 INVALID_REQUEST; 필드 형식 오류는 별도 422. |
| `confirmManualInterviewUnderstanding` | 503 | 원격 선언 복원·유지 | DB만 원자적으로 변경. 미디어 삭제는 commit 뒤 best effort와 retention 재시도; 저장·작업 예약 경계 없음. |
| `createJobPosting` | 400 | 사용자 승인·추가 보완 | JSON body 구문 오류: 400 INVALID_REQUEST; 필드 형식 오류는 별도 422. |
| `createManualDraftCorrection` | 503 | 사용자 승인·추가 보완 | 실제 저장/작업 예약 실패: 503 JOB_QUEUE_UNAVAILABLE (미디어 저장 실패 포함); 변경 rollback. |
| `createQAConversation` | 400 | 사용자 승인·추가 보완 | JSON body 구문 오류: 400 INVALID_REQUEST; 필드 형식 오류는 별도 422. |
| `deleteUnusedManualMedia` | 400 | 원격 선언 복원·유지 | 본문 없는 요청은 JSON parser를 호출하지 않음. 미디어 업로드는 multipart 형식 검증(422). |
| `deleteUnusedManualMedia` | 503 | 원격 선언 복원·유지 | DB만 원자적으로 변경. 미디어 삭제는 commit 뒤 best effort와 retention 재시도; 저장·작업 예약 경계 없음. |
| `getMyWorkCalendarMonth` | 404 | 원격 선언 복원·유지 | 자기 목록·홈은 빈 결과 200; 관심 매장 삭제는 이미 없는 연결에도 204. |
| `getWorkerHome` | 404 | 원격 선언 복원·유지 | 자기 목록·홈은 빈 결과 200; 관심 매장 삭제는 이미 없는 연결에도 204. |
| `getWorkerHome` | 422 | 원격 선언 복원·유지 | 홈 조회는 검증할 path/query/body 입력이 없음. |
| `listManualIntentReviews` | 409 | 원격 선언 복원·유지 | 검토 목록 GET은 읽기 전용이며 revision 입력이나 상태 전이가 없음. |
| `listMyAccessibleStores` | 404 | 원격 선언 복원·유지 | 자기 목록·홈은 빈 결과 200; 관심 매장 삭제는 이미 없는 연결에도 204. |
| `listMyFavoriteStores` | 404 | 원격 선언 복원·유지 | 자기 목록·홈은 빈 결과 200; 관심 매장 삭제는 이미 없는 연결에도 204. |
| `listMyJobApplications` | 404 | 원격 선언 복원·유지 | 자기 목록·홈은 빈 결과 200; 관심 매장 삭제는 이미 없는 연결에도 204. |
| `listMyNotifications` | 404 | 원격 선언 복원·유지 | 자기 목록·홈은 빈 결과 200; 관심 매장 삭제는 이미 없는 연결에도 204. |
| `listMyWorkRequests` | 404 | 원격 선언 복원·유지 | 자기 목록·홈은 빈 결과 200; 관심 매장 삭제는 이미 없는 연결에도 204. |
| `listReceivedStoreInvitations` | 404 | 원격 선언 복원·유지 | 자기 목록·홈은 빈 결과 200; 관심 매장 삭제는 이미 없는 연결에도 204. |
| `markNotificationRead` | 400 | 사용자 승인·추가 보완 | JSON body 구문 오류: 400 INVALID_REQUEST; 필드 형식 오류는 별도 422. |
| `publishManualDraft` | 503 | 원격 선언 복원·유지 | DB만 원자적으로 변경. 미디어 삭제는 commit 뒤 best effort와 retention 재시도; 저장·작업 예약 경계 없음. |
| `removeFavoriteStore` | 404 | 원격 선언 복원·유지 | 자기 목록·홈은 빈 결과 200; 관심 매장 삭제는 이미 없는 연결에도 204. |
| `replaceManualDraftContent` | 503 | 원격 선언 복원·유지 | DB만 원자적으로 변경. 미디어 삭제는 commit 뒤 best effort와 retention 재시도; 저장·작업 예약 경계 없음. |
| `replaceManualInterviewReviewPhotos` | 503 | 원격 선언 복원·유지 | DB만 원자적으로 변경. 미디어 삭제는 commit 뒤 best effort와 retention 재시도; 저장·작업 예약 경계 없음. |
| `requestApplicantWork` | 400 | 사용자 승인·추가 보완 | JSON body 구문 오류: 400 INVALID_REQUEST; 필드 형식 오류는 별도 422. |
| `resendStoreInvitation` | 400 | 원격 선언 복원·유지 | 본문 없는 요청은 JSON parser를 호출하지 않음. 미디어 업로드는 multipart 형식 검증(422). |
| `respondToReceivedStoreInvitation` | 400 | 사용자 승인·추가 보완 | JSON body 구문 오류: 400 INVALID_REQUEST; 필드 형식 오류는 별도 422. |
| `respondToWorkRequest` | 400 | 사용자 승인·추가 보완 | JSON body 구문 오류: 400 INVALID_REQUEST; 필드 형식 오류는 별도 422. |
| `retryManualDraftCorrection` | 503 | 사용자 승인·추가 보완 | 실제 저장/작업 예약 실패: 503 JOB_QUEUE_UNAVAILABLE (미디어 저장 실패 포함); 변경 rollback. |
| `retryManualQuestion` | 400 | 사용자 승인·추가 보완 | JSON body 구문 오류: 400 INVALID_REQUEST; 필드 형식 오류는 별도 422. |
| `retryManualQuestion` | 503 | 사용자 승인·추가 보완 | 실제 저장/작업 예약 실패: 503 JOB_QUEUE_UNAVAILABLE (미디어 저장 실패 포함); 변경 rollback. |
| `retryQAQuestionTranscription` | 400 | 사용자 승인·추가 보완 | JSON body 구문 오류: 400 INVALID_REQUEST; 필드 형식 오류는 별도 422. |
| `retryQAQuestionTranscription` | 503 | 사용자 승인·추가 보완 | 실제 저장/작업 예약 실패: 503 JOB_QUEUE_UNAVAILABLE (미디어 저장 실패 포함); 변경 rollback. |
| `revokeStoreWorkerAccess` | 400 | 원격 선언 복원·유지 | 본문 없는 요청은 JSON parser를 호출하지 않음. 미디어 업로드는 multipart 형식 검증(422). |
| `saveFavoriteStore` | 400 | 사용자 승인·추가 보완 | JSON body 구문 오류: 400 INVALID_REQUEST; 필드 형식 오류는 별도 422. |
| `searchJobPostings` | 404 | 원격 선언 복원·유지 | 자기 목록·홈은 빈 결과 200; 관심 매장 삭제는 이미 없는 연결에도 204. |
| `searchStoreApprovalRequests` | 400 | 사용자 승인·추가 보완 | JSON body 구문 오류: 400 INVALID_REQUEST; 필드 형식 오류는 별도 422. |
| `transcribeQAQuestionAudio` | 400 | 사용자 승인·추가 보완 | JSON body 구문 오류: 400 INVALID_REQUEST; 필드 형식 오류는 별도 422. |
| `transcribeQAQuestionAudio` | 503 | 사용자 승인·추가 보완 | 실제 저장/작업 예약 실패: 503 JOB_QUEUE_UNAVAILABLE (미디어 저장 실패 포함); 변경 rollback. |
| `uploadManualMedia` | 400 | 원격 선언 복원·유지 | 본문 없는 요청은 JSON parser를 호출하지 않음. 미디어 업로드는 multipart 형식 검증(422). |
| `uploadQAQuestionMedia` | 503 | 사용자 승인·추가 보완 | 실제 저장/작업 예약 실패: 503 JOB_QUEUE_UNAVAILABLE (미디어 저장 실패 포함); 변경 rollback. |
| `withdrawJobApplication` | 400 | 사용자 승인·추가 보완 | JSON body 구문 오류: 400 INVALID_REQUEST; 필드 형식 오류는 별도 422. |
| `withdrawOwnerWorkRequest` | 400 | 사용자 승인·추가 보완 | JSON body 구문 오류: 400 INVALID_REQUEST; 필드 형식 오류는 별도 422. |
| `withdrawWorkConfirmation` | 400 | 사용자 승인·추가 보완 | JSON body 구문 오류: 400 INVALID_REQUEST; 필드 형식 오류는 별도 422. |

## 오류 정책

- 관리자 Origin 누락·중복·불일치·형식 오류: 비밀번호와 신청 조회 전에 403 CSRF_INVALID. 인증 실패 횟수에 포함하지 않는다.
- 관리자 비밀번호 유효한 형식의 오답: 401 ADMIN_PASSWORD_INVALID. 누락·공백·길이·유효 UTF-8로 표현할 수 없는 형식: 422 VALIDATION_ERROR. lone surrogate를 401로 바꾸지 않는다(승인 OpenAPI 기준).
- 잘못된 JSON: 400 INVALID_REQUEST. JSON을 받지 않는 요청에 임의 400 분기를 만들지 않는다.
- 관리자 해시 미설정·잘못된 설정: 관리자 요청 500 INTERNAL_ERROR; dev/production health 503, 배포 사전 검사는 pull 전 차단. 설정 값과 원문 비밀번호는 노출하지 않는다.
- 요청 중 미디어 저장 실패 또는 task INSERT의 실제 DB 불가: 503. 업무 오류·무결성 오류·교착/잠금 timeout을 모두 503으로 포장하지 않는다.
- commit 뒤 파일 삭제 실패: 204 성공과 retention 재시도. AI/STT 실행 실패는 접수 응답을 뒤집지 않고 조회 상태 ERROR 및 명시적 재시도로 안내한다.
- 저장·예약 경계가 없는 DB-only 변경의 업무 실패는 기존 404/409/422 또는 내부 오류 정책을 따른다.

## 이미 확인된 정책

- 정기(REGULAR)·대타(TEMPORARY) 접근은 공존하며 메일 링크와 초대함 수락에 동일하게 적용한다.
- 매장 현재/종료 예정 근무자 집계는 ACTIVE WORKER만 포함하며 여러 접근을 workerId 단위로 중복 제거한다.
- 관리자 해시는 원격 PBKDF2_SHA256과 기존 로컬 scrypt를 함께 검증한다. 기존 값을 바꾸지 않고 배포할 수 있다. 새 CLI 기본 형식은 원격 PBKDF2다.

## 검증 기준

`tests/test_contract_matrix.py`는 실제 handler의 JSON parser(400), 인증·권한·형식 경계를 검사한다. `tests/test_task_enqueue_unavailable.py`, `tests/test_unavailable_precision.py`는 저장/작업 INSERT 실패의 503, rollback 및 500과의 구분을 검사한다. 전체 실행 결과·미관측 선언·스킵 사유는 최종 커밋에 결박한 결과로 따로 기록한다. 선언 삭제의 근거는 실행되지 않은 분기 23개를 테스트 통과 건수로 세지 않는다.

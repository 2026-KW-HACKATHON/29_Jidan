# 매장 승인 신청 관리 API 설계

관리자 매장 승인 기능의 계약과 구현 설명이다. 요청·응답 계약은 [OpenAPI 원본](../openapi.yaml)과 [Swagger](http://127.0.0.1:5500)에서 확인한다. 구현은 `app/store_approvals.py`(검색·승인, Origin 검사, 운영 기록)와 `app/admin_password.py`(원격 PBKDF2_SHA256·기존 scrypt 비밀번호 검증)이며, 시험은 `tests/test_store_approvals.py`에 있다.

## 전체 신청 조회

`POST /api/admin/store-approval-requests/search`

```json
{
  "password": "<admin-password>",
  "page": 0,
  "size": 20
}
```

- password는 매 요청 body에 필수 입력한다. 위 값은 실제 비밀번호가 아닌 자리 표시자다.
- status 생략 시 모든 점주의 승인 대기·완료 신청을 조회한다. PENDING/APPROVED 필터를 선택할 수 있다. 명시적인 null은 422로 거부한다.
- 기본 page=0, size=20, 최대 size=100. page는 0 이상의 정수이며 상한이 없다. 전체 건수를 넘는 offset은 DB 조회 없이 빈 목록을 반환한다. submittedAt 및 id 내림차순으로 정렬한다.
- 신청 ID, 신청자 식별/연락 정보, 매장 정보, 상태, 신청/승인 시각을 반환한다.
- 빈 결과나 마지막 페이지를 넘긴 요청은 200, 빈 items를 반환한다. 필터와 totalItems는 현재 DB 기준이며 조회 간 데이터 변동이 가능하다.
- 점주 가입/최초 매장 저장과 동일 트랜잭션에서 승인 신청을 생성한다. 최초 PENDING/approvedAt=null 상태다.

## 관리자 인증 계약

기존 Google 회원 세션과 별도로 관리자 공용 password를 body로 받는다. OpenAPI 보안 scheme은 body 인증을 표현할 수 없으므로 operation의 security=[]로 기존 쿠키 조건을 해제하고, requestBody의 password 필수 조건으로 표현한다. 이는 공개 조회를 허용한다는 뜻이 아니다.

- 허용 Origin 필수: `Origin` 헤더가 `ALLOWED_ORIGINS`의 scheme/host/port와 정확히 같아야 한다. 누락·불일치·중복·형식 오류(경로 포함, `null` 등)는 403 CSRF_INVALID다. 회원 세션이 없으므로 세션 CSRF 토큰(`X-CSRF-Token`)은 요구하지 않는다.
- 처리 순서: JSON 본문 해석(실패 시 400) → Origin(403) → 인증 시도 제한 예약(429) → 본문·경로 검증(422) → 비밀번호(401) → 신청 조회(404/409). Origin 거부는 비밀번호를 확인하지 않으므로 인증 시도 횟수에 포함하지 않고, 운영 기록에는 경로와 결과(CSRF_INVALID)만 남긴다(Origin 값·본문 제외).
- 비밀번호 누락/빈 값/형식 오류: 422. 잘못된 비밀번호: 401 ADMIN_PASSWORD_INVALID.
- 비밀번호 검증 전에는 신청 목록·건수·개인정보를 반환하지 않는다.
- 운영 HTTPS와 서버 설정의 비밀번호 해시 검증을 전제로 한다. 비밀번호를 trim/축약/잘라내기 하지 않는다.
- 프론트 빌드·소스에 하드코딩하거나 브라우저 영구 저장소에 저장하지 않는다.
- 비밀번호는 응답·URL·로그·추적·오류 메시지에서 제외한다. 응답에는 Cache-Control: no-store를 적용한다.
- 관리자 endpoint 및 IP 기준 인증 실패 제한 후 429/Retry-After를 반환한다.
- 공용 password 방식에서는 개인별 관리자를 식별할 수 없다. 운영 기록에는 요청 ID·신청 ID·작업·시각·결과를 남기되 password는 기록하지 않는다.

로그에서 인증 비밀번호를 제외하는 계약과 인증 시도 제한은 [OWASP Logging](https://cheatsheetseries.owasp.org/cheatsheets/Logging_Cheat_Sheet.html), [OWASP Authentication](https://cheatsheetseries.owasp.org/cheatsheets/Authentication_Cheat_Sheet.html)을 참고했다.

## 검증 범위

OpenAPI 구조·예시 및 password/페이지/상태 입력 경계, 승인 상태와 시각 일치, 응답의 password 제외를 Schema로 확인한다. 실제 비밀번호 대조·Origin 검사·인증 시도 제한·DB 페이지 수 계산·정렬은 `tests/test_store_approvals.py`의 통합 시험(SQLite·MySQL)으로 검증한다.

## 승인 처리

`POST /api/admin/store-approval-requests/{requestId}/approve`

requestId는 조회 결과 `items[].id`의 신청 UUID다. store.id와 구분한다.

```json
{
  "password": "<admin-password>"
}
```

- 비밀번호 검증 후 신청을 조회한다. 인증 실패는 401이며, 올바른 비밀번호로 없는 신청에 접근하면 404 STORE_APPROVAL_REQUEST_NOT_FOUND다.
- 신청자가 현재 매장의 점주이면서 OWNER·ACTIVE가 아니면(정지 계정 포함) 409 STORE_APPROVAL_NOT_ALLOWED다. 이미 승인된 신청도 같다.
- 신청과 매장의 승인 상태·approvedAt은 함께 바뀌므로 서로 다르면(매장만 APPROVED, 신청만 APPROVED, 시각 불일치) API 밖에서 바뀐 것이다. 승인하거나 승인 완료로 답하면 그 불일치를 가리므로 409 STORE_APPROVAL_NOT_ALLOWED이며 신청·매장·알림을 바꾸지 않는다.
- 잠금 순서는 신청 → 매장 → 신청자(공유 잠금)다. 신청자 행을 잠금 읽기로 확인하므로 승인 중 다른 트랜잭션이 계정 상태를 바꾸면 승인이 끝날 때까지 기다리고, 승인이 그 변경을 기다렸다면 최신 상태(예: SUSPENDED)를 보고 409로 거부한다. 공유 잠금이라 같은 점주의 다른 매장 승인끼리는 서로 막지 않는다.
- 승인 트랜잭션이 교착 희생자(1213)가 되거나 잠금 대기 시간 초과(1205)가 나면 전체가 rollback되므로, 짧은 간격(0.05초·0.1초)으로 승인 전체를 최대 3회까지 다시 실행한다(가입의 사업자번호 경합 재시도와 같은 방식). 예: 운영자 트랜잭션이 점주 → 매장 역순으로 잠근 경우. 3회 모두 실패하면 이미 명세된 500 INTERNAL_ERROR이며 아무것도 바뀌지 않는다. 409 STORE_APPROVAL_NOT_ALLOWED는 신청 자체를 승인할 수 없다는 뜻이라 일시적인 잠금 경합에 쓰지 않고, 관리 API에는 503이 명세되어 있지 않다. 승인은 멱등이라 관리자가 다시 요청하면 된다.
- PENDING 신청과 연결 매장 상태를 APPROVED로 바꾸고 서버의 최초 승인 시각을 approvedAt에 저장한다.
- 승인 상태·매장 상태·해당 매장의 점주 운영 권한을 같은 트랜잭션으로 적용한다. 실패 시 전체 rollback한다.
- 이미 APPROVED인 신청은 매장도 같은 approvedAt으로 APPROVED일 때 200으로 기존 결과를 반환한다(알림 추가 없음). 최초 승인 시각과 권한을 중복 변경하지 않는다. 동시 승인은 잠금 또는 조건부 갱신으로 한 번만 반영한다.
- 서버가 상태·시각·권한을 결정하며, 요청에는 password 이외 필드를 받지 않는다. 별도 Idempotency-Key는 필요하지 않다.
- 기존 점주 세션의 다음 `/api/auth/session` 조회에서 승인된 매장 권한과 OWNER_HOME을 확인한다. 다른 매장의 승인은 영향을 받지 않는다.

Schema 검사는 비밀번호 필수·외부 상태/시각/권한/승인자 주입 거절·승인 응답의 APPROVED 및 승인 시각 필수를 확인한다. 비밀번호 검증 우선순위, 실제 404/409 처리, 중복·동시 승인, 트랜잭션 rollback과 세션 권한 반영은 `tests/test_store_approvals.py`·`tests/test_notification_wiring_store.py`의 통합 시험(동시 승인은 MySQL)으로 검증한다.

운영 해시 형식과 오류 구분은 [백엔드 오류 계약](backend-error-contract.md) 및 [배포 설정](../../deploy/CI-CD.md#backend-origin-및-관리자-비밀번호-환경-설정)을 따른다.

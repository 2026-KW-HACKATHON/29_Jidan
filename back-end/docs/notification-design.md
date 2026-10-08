# 알림 계약

Figma 전체/안 읽음/모두 읽음 화면은 같은 목록 API의 read 필터와 unreadCount로 표현합니다. 모두 읽음 화면은 안 읽음 목록이 빈 결과인 상태이며 일괄 읽기 버튼은 없으므로 일괄 처리 API를 만들지 않습니다. 카드를 선택하면 별도 읽기 변경 API를 호출합니다.

알림에는 외부 URL·초대 토큰을 담지 않습니다. typed target을 통해 공고 지원서, 근무 요청, 초대함, 매장, 매뉴얼, 일정으로 이동하고 대상의 현재 권한을 다시 검사합니다. 발행은 상태 전이 트랜잭션의 outbox이며 중복·재전송·만료 경계를 구현 테스트로 검증해야 합니다. 근무 전날 안내의 발송 시간은 운영 설정의 제안 정책입니다.

## 철회 알림과 캘린더 이동

요청 철회/확정 철회는 WORK_REQUEST_WITHDRAWN/WORK_CONFIRMATION_WITHDRAWN이며 target=WORK_REQUEST로 종료 상태를 확인합니다. 근무 일정 알림에는 eventId·storeId·서울 workDate를 담아 해당 월 전체 캘린더에서 일정을 강조합니다. 취소 일정이 더 이상 목록에 없으면 철회 안내를 표시하고 자료 접근을 복원하지 않습니다. 일정 상세 API는 필요하지 않습니다.

## #116 구현

### outbox의 의미

알림 행은 원인 상태 전이와 **같은 트랜잭션**에 저장되는 outbox이며, MVP에는 외부 push가 없으므로 저장된 행이 곧 전달(앱 알림 목록)이다. 별도 전송 큐·재시도 워커는 두지 않는다. 업무 변경이 롤백되면 알림도 남지 않고, 커밋되면 반드시 남는다. 외부 push를 도입하면 이 테이블을 원본으로 읽는 전송 상태를 별도로 추가한다.

### 공용 함수 `app.notifications.record_notification`

```python
from app.notifications import NotificationType, WorkScheduleTarget, record_notification

record_notification(
    db,                                   # 호출자의 트랜잭션. commit하지 않는다
    recipient_user_id=worker.id,
    type=NotificationType.WORK_CONFIRMED,
    target=WorkScheduleTarget(event_id=shift.id, store_id=store.id, work_date=seoul_work_date),
    event_key=shift.id,                   # 원인 이벤트 ID
    body="컴포즈커피 광운대점 10월 10일 09:00–14:00",
    # title=None이면 type별 기본 문구, created_at=None이면 현재 UTC
)
```

- 반환값은 `Notification` 행이다. 같은 수신자에게 같은 `type`·`event_key`가 이미 있으면 새로 만들지 않고 기존 행을 반환한다(명세의 "이벤트 ID+수신자 유일성"). 저장 키는 `dedupe_key = "<TYPE>:<event_key>"`이며 `(recipient_user_id, dedupe_key)`가 UNIQUE다.
- 중복은 오류 없이 넘어가는 upsert(SQLite `ON CONFLICT DO NOTHING`, MySQL no-op `ON DUPLICATE KEY UPDATE`)로 처리하므로 호출자 트랜잭션이 깨지지 않는다. 동시 중복은 UNIQUE 인덱스에서 앞선 트랜잭션을 기다린 뒤 그 행을 반환한다. `INSERT IGNORE`는 CHECK·FK 위반도 경고로 바꾸므로 쓰지 않는다.
- 같은 `event_key`를 다른 target에 재사용하면 `ValueError`, type이 허용하지 않는 target 클래스는 `TypeError`, 없는 수신자는 `IntegrityError`다. 모두 호출 코드의 버그다.
- 여러 수신자(예: 매뉴얼 게시 시 접근 가능한 근무자 전원)는 수신자마다 호출한다.

| type | 수신자(권장) | 허용 target 클래스 | API target 필수 필드 | `event_key` |
| --- | --- | --- | --- | --- |
| NEW_APPLICATION | 점주 | `JobApplicationTarget(application_id, store_id, job_id)` | applicationId·storeId·jobId | applicationId |
| INVITATION_ACCEPTED | 점주 | `StoreTarget(store_id)` | storeId | invitationId |
| STORE_APPROVED | 점주 | `StoreTarget(store_id)` | storeId | storeId |
| INVITATION_EXPIRED | 점주(근무자면 초대함) | `StoreTarget` 또는 `StoreInvitationTarget(invitation_id)` | storeId 또는 invitationId | invitationId (만료된 초대는 재전송·취소·수락이 모두 거절되므로 한 초대는 한 번만 만료된다) |
| WORK_REQUEST_RECEIVED | 근무자 | `WorkRequestTarget(request_id, store_id, job_id)` | requestId·storeId·jobId | requestId |
| WORK_REQUEST_NO_RESPONSE | 점주 | `WorkRequestTarget` | requestId·storeId·jobId | requestId |
| WORK_CONFIRMED | 근무자·점주 | `WorkScheduleTarget(event_id, store_id, work_date)` | eventId·storeId·workDate | 확정 근무(shift assignment) ID |
| STORE_INVITED | 근무자 | `StoreInvitationTarget(invitation_id)` | invitationId | `notification_event_key(invitation.id, invitation.last_sent_at)` (재전송마다 새 알림) |
| WORK_REMINDER | 근무자 | `WorkScheduleTarget` | eventId·storeId·workDate | 확정 근무 ID |
| MANUAL_PUBLISHED | 근무자 | `ManualTarget(store_id)` | storeId | 게시 버전 ID |
| WORK_REQUEST_WITHDRAWN | 근무자 | `WorkRequestTarget` | requestId·storeId·jobId | requestId |
| WORK_CONFIRMATION_WITHDRAWN | 근무자 | `WorkRequestTarget` (취소 일정으로 이동하지 않음) | requestId·storeId·jobId | requestId |

- target의 ID는 UUID 문자열(소문자로 정규화), `work_date`는 `datetime`이 아닌 `date`(서울 근무 시작일)만 받는다. API target에 없는 필드는 저장할 수 없다.
- 제목(1~100자, 한 줄)·본문(1~500자)은 생성 당시 스냅샷이다. URL, 32자 이상 토큰·UUID, 이메일, 9자리 이상 숫자(전화·사업자 번호), 서식 있는 전화번호, 제어문자는 `SensitiveTextError`로 거절한다. 사람 이름은 기계적으로 막을 수 없으므로 본문에는 매장명·날짜·시간만 쓰고 근무자/점주 실명·연락처를 넣지 않는다.
- `notification_event_key(*parts)`는 부분을 `:`로 잇고 aware datetime을 UTC 마이크로초 정수로 바꾼다.

### 조회와 읽음

- 목록은 `createdAt DESC, id DESC`로 같은 시각도 순서가 고정된다. items·totalItems·unreadCount는 한 트랜잭션에서 같은 `asOf` 상한(`created_at <= asOf`)으로 계산한다.
- 목록은 target을 다시 판정하거나 바꾸지 않는다. 철회·만료된 초대나 철회된 일정의 알림도 생성 당시 target을 그대로 보이며, 이동한 target API가 현재 권한을 검사하므로 오래된 알림으로 접근이 복구되지 않는다.
- 읽음은 본인 알림만 가능하고 타인·없는 ID는 같은 404다. 최초 readAt만 기록하며(행 잠금 + `read_at IS NULL` 조건부 UPDATE) 다른 key의 재요청도 같은 readAt으로 200이다.

### 시각 기반 알림 sweep

요청이 일으키지 않는 알림은 `app/notification_sweeps.py`의 `SWEEPS` 목록에 등록한 sweep이 기록한다. FastAPI lifespan(`app/lifespan.py`)이 1분마다 모든 sweep을 같은 `now`로 한 번씩 실행한다. 하나가 실패해도 나머지는 실행되며, 실패는 sweep 이름만 로그에 남긴다.

sweep 계약 `def sweep(now: datetime) -> int`(처리한 알림 수):

- **결정적**: 주어진 `now`만 쓰고 벽시계를 직접 읽지 않는다. `created_at`은 `now`이거나, 사건 시각이 저장돼 있으면 그 시각(요청·초대의 기한)이다. 그래야 sweep과 이후 쓰기 중 누가 먼저 기록해도 같은 값이다.
- **멱등**: 위 표의 `event_key`로 `record_notification`을 호출한다. 이미 알린 행을 SQL에서 빼는 것은 최적화일 뿐이고, 1회 보장은 UNIQUE가 한다.
- **유한**: `LIMIT` 배치마다 `session_scope()`로 commit하고 최대 배치 수에서 멈춘다. 남은 행은 다음 주기에 처리한다.
- **다중 프로세스**: 후보 행을 `with_for_update(skip_locked=True, of=<자기 테이블>)`로 잠가 대기하지 않는다. 다른 도메인 트랜잭션이 잡은 행은 다음 주기에 다시 본다. 공고 같은 상위 행은 잠그지 않는다.
- 외부 호출 동안 트랜잭션을 열어 두지 않고, 개인정보를 로그에 남기지 않는다.

등록된 sweep(`SWEEPS` 순서):

| 이름 | type | 동작 | `event_key` |
| --- | --- | --- | --- |
| `work-reminder` | WORK_REMINDER | 아래 "근무 전날 안내" | 확정 근무 ID |
| `work-request-expiry` | WORK_REQUEST_NO_RESPONSE | jobs의 `expire_due_requests(now)`. 만료 판정과 저장은 jobs 함수(`pending_requests`, `expire_request`)가 그대로 하고, 알림은 `expire_request` 안에서 같은 트랜잭션으로 기록된다. 공고 행은 `FOR UPDATE SKIP LOCKED`로 잠가, 다른 전이가 잡은 공고는 기다리지 않고 다음 주기에 처리한다. 그 전이도 공고 쓰기 첫머리에서 만료를 먼저 확정하므로 알림이 빠지지 않는다 | requestId |
| `invitation-expiry` | INVITATION_EXPIRED | 수락·거절·취소 없이 유효 기한(링크 만료와 접근 종료 중 이른 시각)이 `now` 이전 1일 안에 지난 초대. 초대 만료는 저장된 전이가 없어 알림만 기록한다. 이미 알린 초대는 SQL에서 제외하고 `FOR UPDATE OF store_invitations SKIP LOCKED`로 잠근다. 수신자는 매장 점주, `created_at`은 기한이다 | invitationId |

### 근무 전날 안내(WORK_REMINDER)

- 발송 시각은 명세상 운영 설정이다. `WORK_REMINDER_HOUR`(서울 기준 0~23시, 기본 18시)부터 자정 전까지, 서울 근무일(`job_postings.work_date`)이 내일인 활성 확정 근무(`shift_assignments.withdrawn_at IS NULL`)마다 근무자에게 한 번 기록한다. 잘못된 값이면 앱이 시작되지 않는다.
- 그 저녁에 늦게 확정된 근무도 다음 sweep에서 안내한다. 철회된 근무, 당일·지난 근무는 안내하지 않는다. 저녁 내내 서버가 멈췄다면 근무 당일에 늦게 보내지 않는다.
- `event_key`·`eventId`는 확정 근무(`shift_assignments.id`)다. 캘린더 API의 이벤트 ID도 같은 값이어야 알림에서 일정을 강조할 수 있다.
- 본문은 `매장명 M월 D일 HH:MM–HH:MM`(익일 종료면 `(익일)`)이다. 매장명이 민감정보 패턴에 걸리면 매장명 없이 `M월 D일 HH:MM–HH:MM 근무가 있어요`로 기록한다.

## 도메인 연결 지점과 잠금(#116 wiring)

모든 지점은 `app/notification_events.py`의 함수 한 줄로 연결하며, 전이와 같은 트랜잭션에서 도메인 잠금·쓰기를 모두 마친 **뒤** 기록한다(커밋은 전이가 한다). 전이가 실패하면 알림도 롤백된다.

| 지점 | type | 수신자 | target | `event_key` |
| --- | --- | --- | --- | --- |
| 관리자 매장 승인(`store_approvals`, 실제로 PENDING→APPROVED가 바뀐 경우만) | STORE_APPROVED | 매장 점주 | STORE | storeId |
| 초대 생성·재전송(`invitations`) | STORE_INVITED | 초대 이메일과 검증된 Google 이메일이 같은 ACTIVE WORKER 전원 | STORE_INVITATION | invitationId + lastSentAt(재전송마다 새 알림) |
| 초대 수락(`invitation_responses._accept`, 토큰·초대함 경로 공통) | INVITATION_ACCEPTED | 매장 점주 | STORE | invitationId |
| 지원(`jobs/applications`) | NEW_APPLICATION | 매장 점주 | JOB_APPLICATION | applicationId |
| 근무 요청(`jobs/work_requests`) | WORK_REQUEST_RECEIVED | 지원 근무자 | WORK_REQUEST | requestId |
| 요청 철회 | WORK_REQUEST_WITHDRAWN | 지원 근무자 | WORK_REQUEST | requestId |
| 수락·확정 | WORK_CONFIRMED | 근무자와 매장 점주 각각 | WORK_SCHEDULE(eventId=확정 근무 ID) | 확정 근무 ID |
| 확정 철회 | WORK_CONFIRMATION_WITHDRAWN | 근무자 | WORK_REQUEST | requestId |
| 요청 만료(`jobs/state.expire_request`) | WORK_REQUEST_NO_RESPONSE | 요청을 보낸 점주 | WORK_REQUEST | requestId |

- 거절(DECLINE)과 관리자가 이미 승인한 매장의 재승인은 알림을 만들지 않는다.
- 본문에는 매장명·공고 제목·서울 날짜/시간만 쓴다. 근무자·점주 이름, 이메일, 연락처는 넣지 않는다. 매장명이나 제목이 민감정보 패턴에 걸리면 `body_or`가 중립 문구로 바꿔, 알림 때문에 전이가 실패하지 않게 한다.
- **STORE_INVITED와 미가입자**: 알림에는 수신 회원이 필요하므로, 초대 시점에 이메일이 일치하는 가입 회원이 없으면 알림을 만들지 않는다. 미가입자는 이메일 링크로 초대를 받고, 가입 후에는 초대함(`GET /api/users/me/store-invitations`)에서 같은 초대를 본다(invitation-inbox-design: 초대함이 검증된 이메일로 초대를 찾는다). 가입 시 과거 초대를 알림으로 소급 생성하지 않는다. 명세에 그런 요구가 없고, 알림은 사건 시점 스냅샷이기 때문이다. 일치 기준은 초대함과 같다(role WORKER, ACTIVE, email_verified, 소문자·앞뒤 공백 제거 후 동일. LIKE가 아니라 동등 비교이므로 `_`·`%`는 글자 그대로다). OWNER 계정은 초대함을 쓸 수 없어 제외한다.

### 근무 요청 만료 알림의 원자성

만료는 시간의 투영이다(조회는 기한이 지난 PENDING을 이미 EXPIRED로 보인다). 만료를 상태로 저장하는 곳은 jobs의 `expire_request` 하나다. 이 함수는 두 경로로 호출된다.

- 공고 쓰기 첫머리의 `settle_expired_requests`
- `work-request-expiry` sweep의 `expire_due_requests`

어느 경로든 상태 저장과 WORK_REQUEST_NO_RESPONSE 기록이 같은 트랜잭션이라 함께 커밋되거나 함께 롤백된다. 같은 요청을 두 경로가 동시에 확정하려 해도 공고 잠금이 직렬화한다. 그래서 뒤에 오는 쪽은 이미 EXPIRED라 다시 확정하지 않고, `event_key=requestId` UNIQUE가 중복을 한 번 더 막는다.

- 기한 제한(lookback)을 두지 않는다. 만료는 알림이기 이전에 상태라서 오래된 기한도 반드시 저장해야 하고, 알림은 그 저장과 짝을 이루어야 하기 때문이다. 장애 뒤 늦게 확정되더라도 `createdAt`이 기한 시각이므로 목록에서 새 알림처럼 맨 위에 오지 않고 과거 위치에 정렬된다.
- 알림 기록이 실패하면 그 공고 트랜잭션 전체가 롤백되어 요청은 PENDING으로 남고, 다음 주기에 다시 확정된다. 조회는 그 사이에도 EXPIRED로 투영한다.
- 상태를 바꾸지 않고 알림만 기록하던 이전 sweep(`app/work_request_expiry.py`)은 제거했다. 만료를 저장하는 경로를 `expire_request` 하나로 두어 상태와 알림이 항상 함께 커밋되게 하려는 것이다. 배포 전이라 알림 없이 EXPIRED가 된 기존 행을 보정할 필요도 없다.

### 잠금 순서

- 확정된 도메인 순서(jobs 리뷰 반영): 매장(S, `share_store`) → 공고 → 요청 → 지원 → 근무자(`lock_worker`) → 확정 근무 → 접근 grant. 매장 승인은 승인 신청 → 매장, 초대는 매장 → 초대 → (수락 시) grant 순서다.
- 알림 호출은 각 전이의 도메인 잠금과 쓰기를 마친 뒤에 온다. `expire_request`도 요청·지원 갱신 뒤에 기록한다.
- `record_notification`이 잡는 잠금은 다른 트랜잭션이 X로 쥐지 않는 말단 자원뿐이다.
  1. 새 알림 행과 UNIQUE 항목. 같은 이벤트끼리만 겹치고, 그 경합은 도메인 잠금이 이미 직렬화한다.
  2. FK 검사로 수신자 `users` 행에 거는 공유(S) 잠금.
  3. 같은 알림 행을 다시 읽는 `FOR UPDATE`.
- `users` 행에 X를 거는 곳은 둘뿐이고, 점주 행은 어디서도 X로 잠그지 않는다.
  - `lock_worker`: 수락 트랜잭션. 매장 S를 처음에 잡은 뒤 근무자 X를 잡으므로, 매장 X를 쥔 트랜잭션(초대 생성 등)을 근무자 X를 쥔 상태에서 기다리지 않는다.
  - `worker_profile.lock_worker`: 본인 프로필 수정. 다른 행을 잠그지 않는다.
  - 따라서 근무자에게 가는 알림이 S 잠금을 기다리는 경우는 단방향 대기뿐이다.
- 주기 작업은 모두 SKIP LOCKED로 기다리지 않는다. 만료 sweep은 공고 행, 초대 만료와 전날 안내는 각자의 행을 잠근다.
- MySQL에서 동시 수락·철회·만료 경합, 토큰과 초대함 수락 경합, 잠긴 공고 건너뛰기를 테스트로 확인했다.
- **같은 사건은 도메인 잠금으로 직렬화된 지점에서만 기록한다.** 같은 수신자·`event_key`를 두 트랜잭션이 동시에 기록하면, 중복 쪽 `ON DUPLICATE KEY`가 UNIQUE 인덱스에 next-key 잠금(앞 간격 포함)을 건다. 그 상태에서 같은 수신자에게 다른 알림을 이어 쓰면 서로의 간격에 삽입하려다 1213 교착이 난다(MySQL 공격: 16스레드에서 12회). 이미 커밋된 중복이나 서로 다른 키끼리의 동시 기록은 교착이 없었다(0회). 현재 같은 사건을 여러 트랜잭션이 기록할 수 있는 곳은 WORK_REQUEST_NO_RESPONSE뿐이다. 이 알림은 `jobs.state.expire_request` 한 함수에서만 기록되고, 그 호출부(만료를 정리하는 공고 쓰기, `expire_due_requests` sweep)는 모두 공고 행을 먼저 잠그므로(sweep은 `SKIP LOCKED`) 동시에 실행되지 않는다. 데모 시드는 단일 트랜잭션으로만 기록한다. 새 연결 지점이 이미 기록되는 사건을 다른 경로에서 다시 기록하려면 그 사건의 도메인 행을 먼저 잠가야 한다. `tests/test_notification_wiring_sweeps.py::test_each_event_is_recorded_from_known_paths_only`가 기록 경로 목록을 고정해, 경로가 늘면 이 절을 다시 확인하도록 실패한다.

### #109·#115 확인 결과

- #115 초대함 응답은 토큰 경로와 같은 `respond_to_invitation → _accept`를 쓰므로 INVITATION_ACCEPTED가 이미 연결되어 있다. 알림은 두 commit 지점보다 앞에서 기록된다.
  - 토큰 경로: 함수 안 `commit=True`.
  - 초대함 경로: `commit=False`로 `run_idempotent`가 commit한다.
  - 거절에는 알림 type이 없다.
- #109 근무자 접근 종료(`DELETE .../workers/{workerId}/access`)와 그에 따른 대기 초대 취소에는 명세의 알림 type이 없어 알림을 만들지 않는다.
- 명세상 남은 지점은 MANUAL_PUBLISHED(매뉴얼 게시, `docs/manual-interview-design.md`의 게시 트랜잭션 outbox)뿐이다. 매뉴얼 도메인 구현 시 `notification_events`에 함수를 추가해 연결한다.

# Jidan API

Python 3.12 / FastAPI 기반 API.

## 데이터 설계

[Figma 기반 MVP ERD](docs/erd/README.md)는 인증·프로필·매장·초대·대타·관심 매장·매뉴얼·Q&A·알림의 관계와 확정 정책을 정리한 구현 전 문서입니다.

```bash
cd back-end
python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements-dev.txt
python -m uvicorn app.main:app --reload --port 8000
```

검증:

```bash
python -m ruff check .
python -m pytest
```

`GET /api/health`는 인증 없이 상태를 반환한다. 실행·배포 계약은
[CI/CD 운영 문서](../deploy/CI-CD.md)를 참고한다.

로컬에서 DB 설정이 없으면 `database: not_configured`를 반환한다. `APP_ENV=dev|production`에서는 DB 설정이 필수이며, `/api/health`가 `SELECT 1`까지 확인한다. DB 장애 시 자격 증명을 노출하지 않고 503을 반환한다.

로컬에서 DB까지 연결해 실행할 때는 `APP_ENV=local`에 `DB_*`를 지정한다. `APP_ENV=dev`는 CI가 만드는 `swagger-static/` 설계 문서 산출물이 없으면 시작하지 않으므로(`app/design_docs.py`), 로컬에서 쓰려면 `back-end/docs`의 빌드로 산출물을 먼저 만들어야 한다.

## DB 접근과 마이그레이션

SQLAlchemy 2.x(드라이버 PyMySQL)와 Alembic을 쓴다. 연결 정보는 `DB_HOST`, `DB_PORT`, `DB_NAME`, `DB_USER`, `DB_PASSWORD`에서 읽으며 엔진은 첫 사용 시 만든다.

- `app/db/`: 엔진·세션 팩토리(`get_engine`, `session_scope`), FastAPI 의존성 `SessionDep`/`get_session`, `UtcDateTime`, `new_uuid`.
  `session_scope`는 블록이 성공하면 commit, 예외가 나면 rollback하는 스크립트·서비스용 컨텍스트다.
- 규칙: UUID는 `CHAR(36)`, 시각은 UTC `DATETIME(6)`(`UtcDateTime`은 타임존이 없는 값을 거부하고 읽을 때 UTC aware로 돌려준다), 근무일·요일은 `Asia/Seoul` 기준으로 서비스에서 계산한다. enum 값은 `VARCHAR` + `CHECK`다. enum 성격 컬럼과 `google_sub`·`token_hash`는 MySQL에서 대소문자를 구분하도록 `cs_string`(`utf8mb4_0900_as_cs`, NO PAD)을 쓴다. MySQL 기본 collation은 `'worker'`를 `'WORKER'`와 같게 보기 때문이다. 새 enum 컬럼은 반드시 `cs_string`을 쓰고 `tests/test_collation.py`의 컬럼 수를 갱신한다. 이메일은 의도적으로 대소문자를 구분하지 않는다(근거: [ERD 결정 기록](docs/erd/README.md)).
- `app/db/checks.py`: SQLite와 MySQL에서 의미가 다른 문자열 CHECK(`LENGTH`는 글자/바이트, `TRIM`은 U+0020만, MySQL 기본 collation은 폭 없는 문자를 빈 값으로 취급)를 방언별 SQL로 쓰는 `digits_only`·`not_blank`. 사업자 번호(ASCII 숫자 10자리)와 `introduction`·경력 `store_name`(공백 아닌 글자 필요)이 쓴다. CHECK 이름은 같고 본문만 다르며 `0001`에 같은 문장이 있다. 새 문자열 CHECK는 `LENGTH`·`TRIM`을 직접 쓰지 말고 이 모듈에 추가한다. 남은 차이는 [ERD 결정 기록](docs/erd/README.md)의 표를 본다.
- `app/db/models.py`: P0 도메인 15개 테이블(users, 프로필, 매장·승인, 초대·접근, 공고·지원·근무 요청). 기준 스키마는 [ERD](docs/erd/README.md)에서 옮겼다.
- 기록을 보존해야 하는 참조에는 cascade 삭제를 두지 않는다. 다른 테이블을 봐야 하는 규칙(역할, 같은 매장 일치, 시간 중첩, 행 개수 상한, 30분 단위, 이메일 정규화)은 서비스 계층에서 검증한다.

```bash
cd back-end
export DB_HOST=127.0.0.1 DB_PORT=3306 DB_NAME=jidan_local DB_USER=... DB_PASSWORD=...
python -m alembic upgrade head      # 최신 스키마 적용
python -m alembic downgrade -1      # 한 단계 되돌리기
python -m alembic current           # 현재 리비전
python -m alembic upgrade head --sql  # 연결 없이 SQL만 출력(오프라인)
python -m alembic revision --autogenerate -m "변경 설명"  # 새 리비전 초안, 반드시 직접 검토
```

**리비전은 한 줄(선형)로만 유지한다.** 새 리비전은 항상 현재 head 뒤에 붙이고, 한 시점에 한 사람만 추가한다. 병렬 PR로 head가 둘이 되면 `tests/test_migrations.py`가 실패한다. 발행된 리비전은 수정하지 않고 새 리비전을 추가한다. 모델과 마이그레이션이 어긋나면 같은 테스트가 실패한다. `compare_metadata`는 CHECK 제약을 비교하지 않으므로 `tests/test_schema_drift.py`가 CHECK의 이름과 정규화한 SQL(SQLite·MySQL 각각, 방언별 CHECK는 해당 방언으로 렌더링해서)을 따로 비교한다. 정규화 규칙(대소문자·따옴표·공백·`_utf8mb4` 제거, 문자열 리터럴은 원문 비교, MySQL은 괄호 비교 제외)은 `tests/schema_checks.py` 설명에 있다. UNIQUE·INDEX·FK 어긋남은 `compare_metadata`가 잡으며 같은 파일에서 확인한다. collation 어긋남도 `compare_metadata`가 잡지 못하므로 `tests/test_collation.py`가 MySQL `information_schema`와 모델의 collation을 비교한다.

### 요청 처리 commit 규칙 (#104/#105 공통)

> 쓰기는 응답(성공 상태·Set-Cookie 포함)이 나가기 전에 commit이 끝난다. commit이 실패하면 2xx나 성공 쿠키는 나가지 않고, 내부 정보가 없는 500을 반환한다.

설치된 FastAPI 0.141 / Starlette 1.6에서 `yield` 의존성은 기본적으로 **응답을 보낸 뒤** 종료 코드를 실행한다(미들웨어가 응답을 받은 다음 실행됨을 테스트로 확인). 그래서 종료 시 `commit()`하면 이미 200과 쿠키가 나간 뒤에 실패할 수 있다. 선택지는 다음과 같았다.

- (a) 핸들러가 명시적으로 `session.commit()`하고, 의존성은 `scope="function"`으로 응답 전에 rollback·close만 한다. 프레임워크 버전에 덜 의존하고 commit 실패가 핸들러 예외로 그대로 500이 된다. 단점은 commit을 빠뜨릴 수 있다는 점이다.
- (b) `APIRoute` 래퍼가 핸들러 직후 commit한다. commit을 잊지 않지만 라우트 클래스·응답 객체 처리가 프레임워크 내부에 얽히고 쿠키 설정 순서가 불투명하다.

**(a)를 채택한다.**

- 핸들러는 항상 `session: SessionDep`(= `Depends(get_session, scope="function")`)으로 주입받는다. 기본 스코프의 `Depends(get_session)`은 쓰지 않는다.
- 쓰기 핸들러는 응답·쿠키를 설정한 뒤가 아니라 **반환 전에 `session.commit()`을 명시적으로 호출**한다. commit이 예외를 던지면 그대로 두어 500이 되게 한다(응답 객체에 설정한 쿠키는 나가지 않는다).
- 핸들러 예외(`HTTPException` 포함)는 rollback된다. 읽기 전용 핸들러는 commit이 필요 없다.
- commit을 빠뜨려 ORM 쓰기(추가·수정·삭제, `session.execute(update/delete/insert)`)가 남으면 의존성 종료 시 rollback하고 `UncommittedWriteError`를 던져 500으로 드러낸다(조용히 사라지지 않는다). 원시 SQL(`text()`) 쓰기는 감지하지 못하므로 반드시 commit한다.
- 500 본문은 프레임워크 기본 `Internal Server Error`이며 SQL·호스트 등 내부 정보를 담지 않는다. 필요한 로그는 서버 측에만 남긴다.
- `begin_nested()`(SAVEPOINT)의 종료는 바깥 트랜잭션의 commit이 아니다. 미커밋 쓰기 추적은 savepoint 해제·rollback에 영향을 받지 않고 **최외곽 트랜잭션이 끝날 때만** 비워진다. savepoint를 rollback하면 그 안(중첩 포함)에서 한 쓰기만 추적에서 빠지므로, savepoint 안의 쓰기가 전부 취소된 요청은 500이 되지 않고 바깥 쓰기를 commit하지 않은 요청은 500이 된다. 읽기 전용 savepoint는 오탐하지 않는다.
- `tests/test_commit_rule.py`가 SQLite·MySQL 양쪽에서 위 규칙을 검증한다.

### 테스트

직접 API를 호출하고 MySQL 데이터를 확인하는 로컬 환경과 실제 HTTP·MySQL 자동 검증은
[수동 API·DB 및 HTTP E2E 실행 안내](testing/README.md)를 참고합니다.


기본 테스트는 테스트마다 마이그레이션으로 만든 SQLite 인메모리 DB를 쓰므로 DB 없이 `python -m pytest`가 통과한다. SQLite로 확인할 수 없는 MySQL 동작(잠금, 동시성, 실제 CHECK/생성 컬럼)은 `@pytest.mark.mysql`로 표시한다. `DB_*`가 없으면 건너뛰고, 설정되어 있어도 `DB_NAME`이 `_test`로 끝나지 않으면 건너뛴다(마이그레이션 테스트가 테이블을 지우기 때문이다). 스키마 테스트(`tests/test_schema.py`)는 SQLite와 MySQL 양쪽에서 같은 케이스를 실행한다.

MySQL 테스트는 시작할 때 `*_test` 가드를 확인하고 DB의 모든 테이블을 지운 뒤 마이그레이션으로 head까지 새로 만들므로, 빈 DB에서 개별 테스트만 실행해도 통과하고 끝나면 데이터가 남지 않는다. **SQLite 통과나 mysql skip은 MySQL 검증이 아니다.** pytest 요약 끝의 `mysql: ran=N passed=N failed=0 error=0 skipped=0`을 확인하고, skip이 있으면 경고가 출력된다. `JIDAN_REQUIRE_MYSQL=1`이면 mysql 테스트를 건너뛰는 대신 실행을 중단한다.

```bash
JIDAN_REQUIRE_MYSQL=1 DB_HOST=127.0.0.1 DB_PORT=3306 DB_NAME=jidan_test DB_USER=... DB_PASSWORD=... python -m pytest
```

Docker test 단계에는 MySQL이 없으므로 CI에서는 SQLite 테스트만 실행된다. 실제 MySQL 검증은 개발 서버에서 한다.

## 공통 API 계층

업무 endpoint가 공통으로 쓰는 모듈입니다. 계약은 [OpenAPI 명세](openapi.yaml)와 [인증 설계](docs/auth-design.md)를 따릅니다.

| 모듈 | 제공 |
| --- | --- |
| `app/errors.py` | `ApiError(status, code, message)`, `ErrorCode`(명세의 code 전체), 전역 예외 핸들러 |
| `app/middleware.py` | 모든 `/api` 응답에 `Cache-Control: no-store` |
| `app/auth.py` | 세션 쿠키, `require_member`·`require_owner`·`require_worker`·`require_registration_session`, `create_session`·`revoke_session` |
| `app/csrf.py` | Origin 정확 일치와 `X-CSRF-Token` 검증 (`CsrfMember` 등) |
| `app/idempotency.py` | `Idempotency-Key` 재현·충돌·동시 처리 (`run_idempotent`) |
| `app/pagination.py` | `page`/`size` 의존성(`Pagination`)과 목록 응답 형식 |
| `app/ratelimit.py` | 로그인·관리자 비밀번호용 429 제한 |

### 오류 응답

모든 실패는 `{"code", "message", "requestId", "fieldErrors"}`입니다. 업무 코드는 `raise ApiError(409, ErrorCode.STATE_CONFLICT)`처럼 발생시키며 `message`를 생략하면 기본 안내 문구를 씁니다. 형식이 잘못된 JSON은 400 `INVALID_REQUEST`, 필드 검증 실패는 입력값을 되풀이하지 않는 422 `VALIDATION_ERROR`(`fieldErrors`), 처리되지 않은 예외는 내부 정보 없는 500 `INTERNAL_ERROR`입니다. `/api/health`의 `{"detail": ...}` 응답만 배포 점검 계약이라 그대로 유지합니다(`UnstructuredHTTPException`).

### 세션과 인가

```python
from app.auth import CurrentOwner, CurrentWorker, DbSession

@router.get("/api/stores")
def list_stores(owner: CurrentOwner, db: DbSession): ...
```

- 쿠키: 회원 `jidan_session`(HttpOnly, SameSite=Lax, Path=`/`, 유휴 24시간·절대 7일), 가입 `jidan_registration`(HttpOnly, SameSite=Lax, Path=`/api/auth`, 고정 10분). Domain은 설정하지 않습니다. `Secure`는 `production`(및 알 수 없는 `APP_ENV`)에서 항상 켜지며 `COOKIE_SECURE=false`이면 앱이 시작되지 않습니다(`ConfigurationError`). `dev`는 기본 Secure이고 HTTP로 접속하는 공용 개발 서버를 위해 `COOKIE_SECURE=false`만 허용하며, `local`은 기본 꺼짐입니다. `true`/`false` 외의 값(오타)은 조용히 무시하지 않고 시작을 거부합니다.
- 서버에는 토큰 원문이 아니라 SHA-256 해시만 저장합니다(`auth_sessions`, `registration_sessions`). 역할·계정 상태는 매 요청 DB에서 확인합니다.
- 오류: 세션 없음·만료·폐기 401 `SESSION_EXPIRED`, 가입 세션만 있음 401 `REGISTRATION_REQUIRED`, 역할 불일치 403 `FORBIDDEN`, 정지 계정 403 `ACCOUNT_SUSPENDED`(세션도 폐기). `require_owner`는 역할만 보므로 매장 소유·승인(APPROVED) 확인은 endpoint에서 합니다.
- `create_session(user_id)`로 만든 `IssuedSession`을 `set_session_cookie(response, issued)`로 내려보냅니다. 가입 세션은 `create_registration_session(sub, email)`와 `set_registration_cookie`, 가입 완료 시 `consume_registration_session(id, db=db)`(한 번만 성공)를 씁니다. `db=`를 넘기면 호출자의 트랜잭션에 참여하고 생략하면 자체 트랜잭션으로 커밋합니다.
- 토큰을 받는 `GET /api/auth/csrf`는 `principal.csrf_token`과 `principal.expires_at`을 응답하면 됩니다.

### CSRF와 Origin

변경 요청(GET·HEAD·OPTIONS 제외)은 `Origin`이 `ALLOWED_ORIGINS`(쉼표 구분, 예: `https://jidan.example.com,http://localhost:5173`)의 scheme/host/port와 정확히 같고 `X-CSRF-Token`이 세션에 바인딩된 값과 같아야 하며, 아니면 403 `CSRF_INVALID`입니다. 쓰기 endpoint는 `CsrfMember`·`CsrfOwner`·`CsrfWorker`·`CsrfRegistration`을 쓰고, 세션이 없는 endpoint(로그인·관리자 비밀번호)는 `Depends(require_allowed_origin)`만 씁니다. `ALLOWED_ORIGINS`가 비어 있으면 모든 변경 요청이 거절됩니다. 와일드카드는 지원하지 않습니다.

### 멱등성

```python
@router.post("/api/stores", status_code=201)
def create_store(body: StoreIn, owner: CsrfOwner, db: DbSession, key: IdempotencyKey):
    def work() -> IdempotentResult:
        store = ...  # db에 업무 변경을 쓴다. commit은 하지 않는다.
        return IdempotentResult(201, {"id": store.id})

    return run_idempotent(
        db=db, principal=owner, key=key, method="POST",
        path="/api/stores", body=body, handler=work,
        revalidate=lambda: ensure_still_owner(db, owner),  # 재현 직전 권한 재확인
    )
```

- 멱등성 주체(`subject_id`)는 회원 id가 아니라 **Google `sub`의 SHA-256**(`subject_id_for(principal)`)입니다. 회원 세션은 `users.google_sub`, 가입 세션은 검증된 Google `sub`를 쓰므로 같은 사람은 가입 전후에 같은 주체이고, 서로 다른 `sub`의 같은 key는 서로 무관합니다. 유일 키는 `(subject_id, idempotency_key)`이고 endpoint·body 해시가 다르면 409 `IDEMPOTENCY_KEY_REUSED`입니다.
- 같은 key·endpoint·정규화 body는 24시간 동안 최초 응답을 재현하며 `Idempotent-Replayed: true`를 붙입니다. 다른 body·endpoint는 409 `IDEMPOTENCY_KEY_REUSED`입니다. key는 UUID(대소문자 무관)여야 하며 누락·형식 오류는 422입니다. **key 대소문자 정책**: 앱이 key를 소문자 UUID로 정규화해(`idempotency_key` 의존성) 저장·비교하므로 대문자 key와 소문자 key는 같은 key입니다(최초 요청을 재현, 저장 값은 소문자). 컬럼은 MySQL에서 `cs_char(36)`(`utf8mb4_0900_as_cs`)이라 정규화를 우회한 값이 DB에서 조용히 합쳐지지 않습니다. 같은 이유로 `subject_id`·`endpoint`(경로는 대소문자 구분)·`request_hash`·`state`와 세션 테이블의 `token_hash`·가입 세션 `google_sub`도 `cs_string`입니다. `google_email`은 이메일이므로 기본 collation을 유지합니다. 핸들러에서 `begin_nested()`를 써도 됩니다: 해제된 savepoint의 쓰기는 `run_idempotent`의 최종 commit에 포함되고 롤백된 savepoint는 미커밋 쓰기 감지(`UncommittedWriteError`)를 일으키지 않습니다(`tests/test_idempotency.py`).
- 동시 같은 key는 DB 유니크 제약과 60초 임대(PROCESSING)로 한 번만 처리하고, 나머지는 최대 5초 기다렸다가 최초 응답을 재현합니다. 그래도 끝나지 않으면 409 `STATE_CONFLICT`와 `Retry-After`입니다(명세에 처리 중 전용 코드가 없어 재사용).
- **재현되는 헤더**: `IdempotentResult(status, body, headers={...})`의 헤더는 허용 목록(`REPLAY_HEADERS`: `Location`, `Content-Location`, `ETag`)만 저장·재현합니다(`idempotency_records.response_headers`, 마이그레이션 0004). 목록 밖 헤더는 조용히 버리지 않고 `ValueError`로 거절하며, 재현 시에도 목록으로 다시 걸러냅니다. **`Set-Cookie`와 세션·토큰 비밀은 DB에 저장하지 않으며 재현되지 않습니다.** 쿠키는 결과가 아니라 호출자가 핸들러를 실제로 실행한 요청의 응답(`Idempotent-Replayed` 헤더 없음)에만 붙입니다. 재현 응답에는 항상 `Idempotent-Replayed: true`가 붙고 0004 이전 행은 헤더 없이 재현됩니다.
- 예약 단계에서는 유니크 충돌과 락 대기 초과·데드락(MySQL 1205·1213, SQLite locked/busy)만 경합으로 보고 기다립니다. 연결 오류 등 그 밖의 DB 오류는 기다리지 않고 그대로 전파되어 500입니다(409 `STATE_CONFLICT` 아님).
- 핸들러 호출과 재현 응답의 권한 재검사 직전에 `db.commit()`으로 읽기 스냅샷을 갱신하고 `db.expire_all()`로 기존 ORM 캐시도 만료합니다. 대기 중 계정 정지·접근 변경을 이전 상태로 판정하지 않도록 합니다. 그 전에 쓴 변경은 이때 함께 commit됩니다.
- 업무 변경과 응답 저장은 한 트랜잭션으로 커밋되므로 핸들러가 직접 commit하면 안 됩니다. 핸들러가 예외를 내면 롤백하고 key를 풀어 재시도할 수 있습니다. 성공 결과만 저장합니다.
- 만료 행은 같은 key가 다시 오면 교체되며, `purge_expired()`로 주기적으로 정리할 수 있습니다.

#### #105 합의 사항: 가입 완료 endpoint의 멱등성

| 항목 | 계약 |
| --- | --- |
| 주체 식별자 | `google_sub`(Google OIDC `sub`). `RegistrationPrincipal.google_sub`와 `MemberPrincipal.google_sub`(= `users.google_sub`)가 같은 값이어야 합니다. 저장은 `idempotency_records.subject_id = sha256(google_sub)` |
| 주체 함수 | `app.idempotency.subject_id_for(principal) -> str`(64자 hex). `run_idempotent(db=, principal=, key=, method=, path=, body=, handler=, revalidate=)`가 내부에서 호출하므로 호출자가 직접 계산하지 않습니다 |
| 의존성 | `CsrfMemberOrRegistration`(내부 `require_member_or_registration`): 회원 세션 우선, 없으면 가입 세션, 둘 다 없으면 401 `SESSION_EXPIRED`. 정지 계정은 403 `ACCOUNT_SUSPENDED` |
| 연결 규칙 | 별도 연결 단계가 없습니다. 가입 전 기록은 `sha256(가입 세션의 google_sub)`로, 가입 후 회원 세션은 `sha256(users.google_sub)`로 조회하므로 같은 행을 찾습니다. 따라서 가입 트랜잭션은 **`users.google_sub`에 가입 세션의 `google_sub`를 그대로 저장**해야 합니다(정규화·소문자화 금지) |
| 가입 트랜잭션(handler 안, `db`에 쓰고 commit 금지) | (1) 회원 세션 주체면 `ApiError(409, ALREADY_REGISTERED)`: 이미 기록이 있으면 handler까지 오지 않고 최초 응답이 재현되므로, handler에 들어온 회원 세션은 "동일 주체의 성공한 재시도"가 아닙니다. (2) `users`·프로필·(점주) 최초 매장 저장, (3) `consume_registration_session(registration_id, db=db)`가 False면 409 `STATE_CONFLICT`로 중단, (4) `create_session(user.id, db=db)`, (5) `IdempotentResult(201, 본문)` 반환 |
| 응답 후 | `run_idempotent`가 업무 변경·응답 기록을 한 트랜잭션으로 commit한 뒤 반환하므로, 그 응답에 `set_session_cookie`로 새 회원 쿠키를 싣고 `clear_registration_cookie`로 가입 쿠키를 지웁니다. 재현 응답(`Idempotent-Replayed: true`)에는 새 세션을 만들지 않으므로 쿠키를 추가하지 않습니다(handler가 실행됐을 때만 발급). `Set-Cookie`는 DB에 저장되지 않으므로 재현되지 않습니다. 재현 요청은 이미 발급된 회원 세션 쿠키(또는 재로그인으로 얻은 세션)를 그대로 따르고, 가입 쿠키는 만료(10분)로 정리됩니다. 호출 규칙: `if "Idempotent-Replayed" not in response.headers:` 일 때만 쿠키를 설정하세요. `Location` 등은 `IdempotentResult(headers=)`로 넘기면 재현됩니다 |
| 재현 | 같은 key·같은 body: 회원 세션이든 (같은 Google 계정으로 다시 로그인해 얻은) 가입 세션이든 최초 201을 재현. 다른 body·다른 endpoint: 409 `IDEMPOTENCY_KEY_REUSED`. 재현 직전에 `revalidate`가 양쪽 세션에서 호출됩니다 |
| 쿠키까지 잃은 경우 | 가입이 이미 끝났다면 Google 재로그인은 회원 세션을 발급하므로 같은 key로 가입 endpoint를 재시도할 수 있습니다 |

### commit과 응답 순서

원칙(#103 공통 규칙과 동일): **commit이 끝나기 전에 2xx와 `Set-Cookie`를 보내지 않습니다.** commit이 실패하면 5xx이고 쿠키는 없습니다.

| 경로 | 응답 전 commit 보장 |
| --- | --- |
| `run_idempotent` | 보장. 업무 변경과 응답 기록을 `db.commit()`한 뒤에만 반환합니다. 실패하면 롤백·key 해제 후 예외(5xx)이며 재시도할 수 있습니다 |
| `create_session`·`revoke_session`·`create_registration_session`·`revoke_registration_session`·`revoke_user_sessions`를 `db=` 없이 호출 | 보장. 자체 트랜잭션이 반환 전에 commit됩니다 |
| 위 함수들을 `db=db`로 호출 | **보장 아님**. 호출자의 commit을 기다립니다. `commit_then_set_session_cookie`·`commit_then_clear_session_cookie`·`commit_then_set_registration_cookie`·`commit_then_clear_registration_cookie`로 commit한 뒤에 쿠키를 설정하면 보장됩니다(예외 시 쿠키 없음) |
| `set_session_cookie`·`clear_*_cookie`를 직접 호출 | 보장 아님. `get_session`이 응답 전에 commit하도록 #103이 고정될 때까지는 `commit_then_*`를 쓰세요 |
| `consume_registration_session(db=db)` | commit하지 않음. 가입 handler(`run_idempotent` 안)에서 호출하면 응답 전 commit됩니다 |
| 정지 계정 감지(`_resolve_member`) | 보장. 세션 폐기를 commit한 뒤 403을 냅니다 |
| `last_seen_at` 갱신 | 요청 세션의 마지막 commit에 편승합니다. 실패해도 응답 의미가 바뀌지 않는 최적화입니다 |
| 멱등성 예약·해제(`_reserve`·`_release`) | 자체 트랜잭션이 즉시 commit됩니다 |

`tests/test_commit_before_cookie.py`에서 위 보장을 commit 실패 주입으로 검증합니다. 직접 쿠키 설정 후 요청 말미 commit이 실패하는 경우(`test_bare_cookie_setter_cannot_outrun_the_request_commit`)는 #103의 `get_session` 규칙에 의존하며 그 반영 전에는 strict xfail입니다.

### 페이지네이션과 레이트 리미터

- `Pagination` 의존성은 `page`(0부터, 기본 0, 최대 1,000,000)와 `size`(1~100, 기본 20)를 검증합니다. 숫자가 아니거나 범위를 벗어나거나 중복되면 422 `VALIDATION_ERROR`이고, 끝 페이지를 넘기면 오류가 아니라 빈 `items`입니다. `page_response(items, total, params)`로 응답을 만듭니다.
- `enforce_login_rate_limit`(분당 20회/IP)는 확인과 기록을 한 번의 잠금(`hit`)으로 처리해 동시 요청이 한도를 넘지 못합니다.
- 관리자 비밀번호는 `attempt: AdminAttempt`(= `enforce_admin_password_rate_limit`) 의존성이 요청 시작 시 IP별(10분 5회)·전체(10분 50회) 시도 횟수를 **원자적으로 확보(reserve)** 하고, 한도를 넘으면 `Retry-After`와 429 `RATE_LIMITED`입니다. endpoint는 비밀번호가 틀리면 `attempt.failed()`, 맞으면 `attempt.succeeded()`를 정확히 한 번 호출합니다. 성공하면 확보한 슬롯을 돌려주고(전체·IP 모두) 해당 IP의 끝난 실패 기록을 초기화하되, 아직 진행 중인 다른 시도의 슬롯은 유지합니다. 결과를 보고하지 않고 끝난 요청(예외·검증 실패)은 실패로 남습니다. 전체 한도는 성공해도 초기화되지 않습니다.

### 환경변수

| 이름 | 용도 |
| --- | --- |
| `ALLOWED_ORIGINS` | 변경 요청을 허용할 Origin 목록. 비우면 모두 거절 |
| `COOKIE_SECURE` | 생략·빈 값·`true`·`false`만 허용(그 외는 시작 실패). `production`에서 `false`는 시작 실패, `dev`만 `false`로 끌 수 있음. 생략 시 `local`은 끔, 그 외는 켬 |
| `TRUST_FORWARDED_FOR` | `true`면 프록시가 덧붙인 `X-Forwarded-For` 마지막 항목을 클라이언트 IP로 사용 |

### 한계

- 레이트 리미터는 프로세스 메모리에만 있어 워커·서버가 여럿이면 카운터가 공유되지 않고(실제 한도 = 한도 × 프로세스 수) 재시작하면 초기화됩니다. 정확한 제한이 필요하면 DB나 캐시로 옮겨야 합니다.
- 레이트 리미터는 추적 키를 최대 10,000개로 제한합니다. 만료된 키만 앞에서 하나씩 제거하고(분할상환 O(1)), 아직 창 안에 있는 키는 한도 초기화를 막기 위해 절대 밀어내지 않습니다. 가득 차면 **새 키를 429(fail-closed)** 로 거절하므로 서로 다른 주소를 대량으로 보내면 새 클라이언트가 최대 한 창(로그인 60초·관리자 600초) 대기할 수 있습니다(DoS 특성). CPU 소모나 기존 한도 초기화는 불가능합니다. `TRUST_FORWARDED_FOR=true`는 클라이언트가 키를 위조할 수 없을 때(신뢰 프록시가 헤더를 덧붙일 때)만 켜세요.
- 세션 만료 시각 갱신은 최대 1분 간격으로만 기록하며, 만료된 세션·멱등성 행의 정기 삭제 작업은 아직 없습니다.
- 가입 세션 쿠키는 Path가 `/api/auth`라 그 아래 endpoint에서만 전송됩니다.
- 관리자 비밀번호 API는 비밀번호가 body에 있어 `page`/`size`를 body에서 받습니다. `Pagination`은 query 전용이므로 body 검증은 해당 endpoint 모델에서 같은 범위(0 이상, 1~100)로 맞춥니다.

## API 설계

[Figma 기반 OpenAPI 명세](openapi.yaml)를 제공합니다.
인증 API 8개(Google 시작/callback, 가입 컨텍스트, 일반회원/점주 가입, 세션/CSRF 조회, 로그아웃)를 구현했습니다. 외부 인증 설정과 프론트 `__auth` 진입점 연결은 [인증 설계](docs/auth-design.md#105-구현과-운영-설정)를 참고합니다. 일반회원 프로필 조회·기본 정보 부분 수정·경력/가능 시간 전체 교체 API 4개도 구현했습니다. [프로필 구현과 검증](docs/worker-profile-design.md#106-구현과-검증)을 참고합니다. 매장 관리·초대·근무자 관리 endpoint는 아직 제공하지 않습니다.

- [인증 화면 근거·인가 정책](docs/auth-design.md)
- [관리자 매장 승인 계약](docs/store-approval-design.md)
- [일반회원 프로필 조회·기본 정보·근무 정보·가능 시간 수정 계약](docs/worker-profile-design.md)
- [점주 관리 매장 조회·추가·현황 요약](docs/owner-store-design.md)
- [근무자 초대·재전송·취소·수락·거절](docs/store-invitation-design.md)
- [근무자 조회·자료 접근 기간·수동 종료](docs/store-worker-design.md)
- [매뉴얼·AI 인터뷰·음성/사진·점주 확인 발행·근무자 열람](docs/manual-interview-design.md)

```bash
cd docs
npm ci
npm run check
npm run dev
```

[로컬 Swagger 문서](http://127.0.0.1:5500)를 확인합니다. 자세한 실행 방법은 [문서 서버 안내](docs/README.md)를 참고합니다.

설계 Swagger는 `back-end/dev`의 CI/CD를 통해 개발 환경에서만 `/api/swagger/`로 제공됩니다. [주소·자동 배포 흐름](docs/README.md#개발-서버-cicd와-endpoint)을 참고합니다. PR 단계에서는 배포하지 않으며 업무 API 구현과 설계 문서 제공은 별개입니다.

### OAuth 로그아웃 경합과 만료 기록 정리

`0006`은 OAuth 소비와 취소를 분리하고 발급된 회원/가입 세션 ID를 기록한다.
로그아웃과 callback은 같은 OAuth 행을 잠가 발급·폐기를 직렬화한다. 쿠키 응답이
늦게 도착하더라도 폐기된 세션으로 인증할 수 없다. 링크는 세션 정리 이후에도
OAuth 기록이 독립적으로 남을 수 있도록 외래키 없이 ID로 보관한다.

FastAPI lifespan에서 시작한 작업이 시작 시 한 번, 이후 5분마다 만료 OAuth 기록을
정리한다. 만료 후 10분의 유예를 두어 callback/로그아웃 처리 중 즉시 삭제하지 않는다.
한 번에 500행씩 최대 10회 삭제하고 각 배치를 commit한다. 남은 기록은 다음 주기에
처리한다. 여러 프로세스에서는 MySQL `SKIP LOCKED`로 중복 잠금 대기를 피한다.
회원/가입 세션은 이 작업에서 삭제하거나 폐기하지 않는다. 정리 실패는 비밀값 없는
로그를 남기고 다음 주기에 재시도한다. 별도 cron이나 서버 설정은 필요하지 않다.

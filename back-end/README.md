# Jidan API

Python 3.12 / FastAPI 기반 API.

## 구현 범위 요약

[OpenAPI 명세](openapi.yaml)의 operation **102개 모두 로컬 통합 코드에 연결**되어 있다. `94982be`의 명세와 라우트를 대조한 결과이며, 점주 AI 인터뷰(#120)의 12개 operation도 포함한다. 명세 밖 업무 라우트는 없다. 라우트 연결, 실제 응답 코드 커버리지, AI 의미 품질, 개발 서버 배포는 각각 해당 코드와 실행 근거를 확인한다. 이전 `eb69e26`의 90/102 및 응답 코드 501/527 결과는 현재 통합 코드의 실행 결과로 사용하지 않는다.

| 영역 | 구현/명세 | 이슈 | 모듈 | 설계 문서 |
| --- | --- | --- | --- | --- |
| 인증·프로필 | 12/12 | #104~#106 | `app/oauth.py`, `app/registration.py`, `app/auth_views.py`, `app/logout.py`, `app/worker_profile.py` | [인증](docs/auth-design.md), [프로필](docs/worker-profile-design.md) |
| 매장·승인·초대·근무자 | 21/21 | #107~#109, #115 | `app/stores.py`, `app/store_approvals.py`, `app/invitations.py`, `app/invitation_responses.py`, `app/invitation_inbox.py`, `app/store_workers.py`, `app/worker_stores.py` | [점주 매장](docs/owner-store-design.md), [승인](docs/store-approval-design.md), [초대](docs/store-invitation-design.md), [초대함](docs/invitation-inbox-design.md), [근무자](docs/store-worker-design.md), [매장 선택](docs/worker-store-selection-design.md) |
| 대타·홈·캘린더 | 24/24 | #110~#114, #117 | `app/jobs/`, `app/home.py` | [공고](docs/job-posting-design.md), [탐색](docs/job-search-design.md), [지원](docs/application-design.md), [지원자](docs/applicant-review-design.md), [근무 요청](docs/work-request-design.md), [홈](docs/home-design.md), [캘린더](docs/calendar-design.md) |
| 알림·관심 매장 | 5/5 | #116, #117 | `app/notifications.py`, `app/notification_views.py`, `app/favorite_stores.py` | [알림](docs/notification-design.md), [관심 매장](docs/favorite-store-design.md) |
| 매뉴얼·AI | 40/40 | #118~#121 | `app/manual_*.py`, `app/interview/`, `app/qa/`, `app/ai/`, `app/tasks/`, `app/media/` | [매뉴얼·인터뷰](docs/manual-interview-design.md), [게시 버전 확인](docs/published-version-guard-design.md), [AI 기반](docs/ai-foundation.md), [업무 질문](docs/qa-conversation-design.md), [질문 미디어](docs/qa-media-design.md) |

데이터 설계는 [ERD](docs/erd/README.md), 배포는 [CI/CD 운영 문서](../deploy/CI-CD.md)를 본다.

실행과 검증:

```bash
cd back-end
python -m pip install -r requirements-dev.txt
python -m uvicorn app.main:app --reload --port 8000            # 로컬 실행(DB 없으면 health만 not_configured)
python -m ruff check .                                         # 린트
python -m pytest                                               # SQLite 전체(MySQL 테스트는 skip)
JIDAN_REQUIRE_MYSQL=1 DB_HOST=127.0.0.1 DB_PORT=3306 DB_NAME=jidan_test DB_USER=... DB_PASSWORD=... \
  python -m pytest                                             # MySQL 포함 전체(요약의 mysql: ran=N passed=N skipped=0 확인)
JIDAN_RUN_OPENAI=1 OPENAI_API_KEY=... python -m pytest -m openai   # 실제 OpenAI opt-in 테스트
JIDAN_SPEC_COVERAGE=/tmp/cov.json python -m pytest && python -m tests.spec_coverage report /tmp/cov.json
(cd docs && npm ci && npm run check)                           # OpenAPI·설계 문서 검사
```

네트워크나 DB 없이 저장된 AI 출력을 검토하는 도구는 [로컬 AI 평가](docs/ai-evaluation.md)에 있다. 카페·음식점·편의점의 14개 합성 사례를 현재 parser와 서버 검증에 적용하고, 구조 오류와 정답 속성 오류를 구분한다. 합성 응답 통과는 평가 도구의 검사이며 실제 모델 품질 승인이 아니다. 응답·데이터·소스 해시와 사람 검토가 남은 항목도 결과에 기록한다.

충분성 결과는 정보가 충분할 확률 0.5와 `sufficient`가 일치해야 하고, 충분한 결과에 부족 항목이 있으면 `INVALID_OUTPUT`으로 재시도한다. 이 서버 정책의 설정 버전은 `2026-10-06.6`이다. 원래 답변은 보존하며 재시도를 소진하면 기존 공개 오류 계약을 따른다.

## 로컬 실행

```bash
cd back-end
python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements-dev.txt
python -m uvicorn app.main:app --reload --port 8000
```

`GET /api/health`는 인증 없이 상태를 반환한다. 실행·배포 계약은
[CI/CD 운영 문서](../deploy/CI-CD.md)를 참고한다.

로컬에서 DB 설정이 없으면 `database: not_configured`를 반환한다. `APP_ENV=dev|production`에서는 DB 설정이 필수이며, `/api/health`가 `SELECT 1`까지 확인한다. DB 장애 시 자격 증명을 노출하지 않고 503을 반환한다. dev·production에서는 DB 확인 전에 `ADMIN_PASSWORD_HASH`가 로그인과 같은 PBKDF2_SHA256 또는 기존 scrypt 형식인지(`app/admin_password_config.py`) 확인하고, 없거나 틀리면 값 없이 같은 503을 반환한다(배포 사전 검사·롤백 조건은 [CI/CD](../deploy/CI-CD.md#backend-origin-및-관리자-비밀번호-환경-설정)).

로컬에서 DB까지 연결해 실행할 때는 `APP_ENV=local`에 `DB_*`를 지정한다. `APP_ENV=dev`는 CI가 만드는 `swagger-static/` 설계 문서 산출물이 없으면 시작하지 않으므로(`app/design_docs.py`), 로컬에서 쓰려면 `back-end/docs`의 빌드로 산출물을 먼저 만들어야 한다.

## DB 접근과 마이그레이션

SQLAlchemy 2.x(드라이버 PyMySQL)와 Alembic을 쓴다. 연결 정보는 `DB_HOST`, `DB_PORT`, `DB_NAME`, `DB_USER`, `DB_PASSWORD`에서 읽으며 엔진은 첫 사용 시 만든다.

- `app/db/`: 엔진·세션 팩토리(`get_engine`, `session_scope`), FastAPI 의존성 `SessionDep`/`get_session`, `UtcDateTime`, `new_uuid`, 응답 시각 표기 `iso_utc`(RFC 3339 UTC `+00:00`, 모든 응답 시각이 이 표기를 쓴다).
  `session_scope`는 블록이 성공하면 commit, 예외가 나면 rollback하는 스크립트·서비스용 컨텍스트다.
- DB 시간 제한(`app/db/engine.py`): 연결 3초, 세션 `innodb_lock_wait_timeout` 5초(`DB_LOCK_WAIT_TIMEOUT_SECONDS`), PyMySQL 읽기·쓰기 15초(`DB_READ_TIMEOUT_SECONDS`). 잠금 대기가 반드시 소켓 제한보다 먼저 끝나야 한다. 예전에는 읽기 제한이 연결과 같은 3초였고 서버 잠금 대기는 50초라서, 3초 넘는 잠금 대기가 1205가 아니라 2013(Lost connection)으로 끝났다. 그 결과 `is_lock_contention` 재시도와 멱등 예약의 409 경로를 건너뛰고 500이 됐고, 서버 쪽 대기는 계속 남았다(부하 하 초대 생성 500의 원인). 값이 `1 ≤ 잠금 대기 < 읽기 ≤ 55`를 어기면 시작 시 `ValueError`로 멈춘다. 헬스체크는 `get_health_engine()`(1개 연결, 3초)을 써서 DB가 멈춰도 빨리 503이 된다.
- 잠금 읽기(`FOR UPDATE`/`FOR SHARE`)는 PK나 고유 키 조회로만 한다. 비고유 조건이나 JOIN에 거는 잠금은 MySQL이 통계에 따라 고른 계획대로 범위가 정해지고 next-key 간격까지 잡아, 다른 매장 요청과 교착한다(근무 확정 1213 사례). 비고유 조건으로 찾을 행은 일반 읽기로 PK를 찾은 뒤 하나씩 잠근다(`app.jobs.state.lock_each`, READ COMMITTED와 상위 잠금이 전제). `tests/lock_scope.py`의 autouse 가드가 MySQL 테스트마다 모든 잠금 문장을 검사하며, 예외(SKIP LOCKED 스윕, 미디어 참조 FOR SHARE, 선정 효과 PK 접두사)는 그 파일에 근거와 함께 적는다. UPDATE·DELETE도 같은 규칙이며, PK `IN (...)` 목록도 인덱스 스캔으로 실행되어 넓게 잠글 수 있어(멱등 기록 정리 1213 사례) SKIP LOCKED 읽기 밖에서는 쓰지 않는다. 일반 읽기로 키를 찾은 뒤 `app.db.keyed`(update_by_key·delete_by_key·lock_by_key)로 한 건씩 처리한다. 가드는 app/ 코드가 보낸 쓰기를 검사하고, 운영자 CLI 진입점(`demo_seed.run`, E2E 정리)이 `app.operator_cli.operator_cli()` 안에서 실행한 문장만 면제한다(요청 경로가 같은 함수를 불러도 검사한다).
- 규칙: UUID는 `CHAR(36)`, 시각은 UTC `DATETIME(6)`(`UtcDateTime`은 타임존이 없는 값을 거부하고 읽을 때 UTC aware로 돌려준다), 근무일·요일은 `Asia/Seoul` 기준으로 서비스에서 계산한다. enum 값은 `VARCHAR` + `CHECK`다. enum 성격 컬럼과 `google_sub`·`token_hash`는 MySQL에서 대소문자를 구분하도록 `cs_string`(`utf8mb4_0900_as_cs`, NO PAD)을 쓴다. MySQL 기본 collation은 `'worker'`를 `'WORKER'`와 같게 보기 때문이다. 새 enum 컬럼은 반드시 `cs_string`을 쓰고 `tests/test_collation.py`의 컬럼 수를 갱신한다. 이메일 컬럼은 `email_string`(`utf8mb4_0900_as_ci`)으로 대소문자는 구분하지 않고 악센트·ß는 구분한다. SQL 매칭은 `app.email_match.email_is`로 바이트까지 비교한다(근거: [ERD 결정 기록](docs/erd/README.md)).
- `app/db/checks.py`: SQLite와 MySQL에서 의미가 다른 문자열 CHECK(`LENGTH`는 글자/바이트, `TRIM`은 U+0020만, MySQL 기본 collation은 폭 없는 문자를 빈 값으로 취급)를 방언별 SQL로 쓰는 `digits_only`·`not_blank`. 사업자 번호(ASCII 숫자 10자리)와 `introduction`·경력 `store_name`(공백 아닌 글자 필요)이 쓴다. CHECK 이름은 같고 본문만 다르며 `0001`에 같은 문장이 있다. 새 문자열 CHECK는 `LENGTH`·`TRIM`을 직접 쓰지 말고 이 모듈에 추가한다. 남은 차이는 [ERD 결정 기록](docs/erd/README.md)의 표를 본다.
- `app/db/models.py`: 전 도메인 50개 테이블(인증·프로필·매장·승인·초대·접근·공고·지원·근무 요청·알림·관심 매장·AI 작업·미디어·전사·매뉴얼·인터뷰·Q&A). 기준 스키마는 [ERD](docs/erd/README.md)에서 옮겼다.
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

**한 번이라도 적용된 상태는 체인의 접두사로 남긴다.** 병렬 브랜치 통합 중 체인이 `0009 → 0020 → 0030`(일부 로컬 DB)과 `0009 → 0010~0013(AI) → 0020 → 0030` 두 계열로 존재했다. 후자 기준으로 `alembic_version=0020`인 옛 DB를 `upgrade head`하면 0010~0013을 건너뛰어 AI 테이블 없이 "최신"이 된다. 그래서 체인을 `… 0009 → 0020 → 0030 → 0032 → 0033 → 0034 → 0035`로 재정렬하고 AI 리비전을 0032~0035로 재번호했다(내용은 그대로, 0020·0030은 AI 테이블에 의존하지 않는다). 이제 원격 dev(0006), 옛 로컬(0009·0020·0030) 어느 상태든 그대로 `upgrade head`가 된다. 리비전 번호는 순서가 아니라 식별자이며 순서는 `down_revision`이 정한다. 이메일 collation 변경은 처음에 `0031`로 0030과 0032 사이에 끼울 계획이었지만, 그러면 이미 0032~0035까지 올라간 DB가 0031을 적용된 것으로 보고 건너뛴다(0020 결함과 같은 구조). 그래서 `0036`(down `"0035"`)으로 head 뒤에 붙이고 `0031`은 쓰지 않는다. **한 번이라도 어떤 DB에 적용됐을 수 있는 리비전 사이에는 새 리비전을 끼워 넣지 않는다. 새 리비전은 항상 head 뒤에 붙인다.** `tests/test_migrations.py`의 `test_mysql_released_states_upgrade_to_the_fresh_head_schema`가 0006·0009·0020·0030 상태에서 head로 올린 스키마(`information_schema`의 테이블·컬럼·인덱스·CHECK·FK·collation)가 새 DB와 같고 행이 보존되는지 확인한다.

재정렬 전 통합 브랜치(`0009 → 0010~0013 → 0020 → 0030`)로 만든 로컬 DB는 `alembic_version=0030`이면서 AI 테이블이 이미 있다. 새 체인에서 0030은 AI 테이블이 없는 상태라 `upgrade head`가 `background_tasks` 생성에서 멈춘다(DDL 전에 실패해 스키마는 바뀌지 않음). 이 DB의 스키마는 새 head와 같으므로(`information_schema` 비교 차이 0으로 확인) 덤프를 남긴 뒤 아래처럼 표시만 옮긴다. 0010~0013에 멈춘 DB는 그 리비전 id가 없어 Alembic이 인식하지 못하므로 다시 만든다.

```bash
python -m alembic current                    # 0030
mysql "$DB_NAME" -e "SHOW TABLES LIKE 'background_tasks'"  # 한 줄이 나오면 통합 체인 DB
python -m alembic stamp 0035                 # 스키마는 그대로, 리비전 표시만 변경
python -m alembic upgrade head               # 0035 뒤의 리비전(0036 collation 등)을 적용
```

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
[수동 API·DB 및 HTTP E2E 실행 안내](testing/README.md)를 참고합니다. PR #154의 수동 sandbox도 보존합니다.

기본 테스트는 테스트마다 마이그레이션으로 만든 SQLite 인메모리 DB를 쓰므로 DB 없이 `python -m pytest`가 통과한다. SQLite로 확인할 수 없는 MySQL 동작(잠금, 동시성, 실제 CHECK/생성 컬럼)은 `@pytest.mark.mysql`로 표시한다. `DB_*`가 없으면 건너뛰고, 설정되어 있어도 `DB_NAME`이 `_test`로 끝나지 않으면 건너뛴다(마이그레이션 테스트가 테이블을 지우기 때문이다). 스키마 테스트(`tests/test_schema.py`)는 SQLite와 MySQL 양쪽에서 같은 케이스를 실행한다.

MySQL 테스트는 시작할 때 `*_test` 가드를 확인하고 DB의 모든 테이블을 지운 뒤 마이그레이션으로 head까지 새로 만들므로, 빈 DB에서 개별 테스트만 실행해도 통과하고 끝나면 데이터가 남지 않는다. **SQLite 통과나 mysql skip은 MySQL 검증이 아니다.** pytest 요약 끝의 `mysql: ran=N passed=N failed=0 error=0 skipped=0`을 확인하고, skip이 있으면 경고가 출력된다. `JIDAN_REQUIRE_MYSQL=1`이면 mysql 테스트를 건너뛰는 대신 실행을 중단한다.

```bash
JIDAN_REQUIRE_MYSQL=1 DB_HOST=127.0.0.1 DB_PORT=3306 DB_NAME=jidan_test DB_USER=... DB_PASSWORD=... python -m pytest
```

Docker image의 test 단계는 SQLite만 쓴다. CI의 별도 `Backend HTTP E2E` 작업이 격리된 MySQL 8.4에서 전체 Python 시험(`JIDAN_REQUIRE_MYSQL=1`)과 실제 서버 HTTP E2E를 실행하고, 실패·오류가 있으면 이미지 빌드로 넘어가지 않는다. 로컬에서도 같은 `back-end/testing/run-e2e.sh`를 쓴다([격리 MySQL 검증과 실제 HTTP E2E](testing/README.md)).

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

모든 실패는 `{"code", "message", "requestId", "fieldErrors"}`입니다. 업무 코드는 `raise ApiError(409, ErrorCode.STATE_CONFLICT)`처럼 발생시키며 `message`를 생략하면 기본 안내 문구를 씁니다. 형식이 잘못된 JSON은 400 `INVALID_REQUEST`, 필드 검증 실패는 입력값을 되풀이하지 않는 422 `VALIDATION_ERROR`(`fieldErrors`), 처리되지 않은 예외는 내부 정보 없는 500 `INTERNAL_ERROR`입니다. `/api/health`의 `{"detail": ...}` 응답만 배포 점검 계약이라 그대로 유지합니다(`UnstructuredHTTPException`). 모든 응답(성공·오류·처리되지 않은 500 포함)에는 같은 요청의 로그 `request_id`와 오류 본문 `requestId`와 같은 값의 `X-Request-ID` 헤더가 한 개 붙습니다(`app/request_id.py`, 클라이언트가 보낸 값은 무시).

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
- 같은 key·endpoint·정규화 body는 24시간 동안 최초 응답을 재현하며 `Idempotent-Replayed: true`를 붙입니다. 다른 body·endpoint는 409 `IDEMPOTENCY_KEY_REUSED`입니다. key는 UUID(대소문자 무관)여야 하며 누락·형식 오류는 422입니다. **key 대소문자 정책**: 앱이 key를 소문자 UUID로 정규화해(`idempotency_key` 의존성) 저장·비교하므로 대문자 key와 소문자 key는 같은 key입니다(최초 요청을 재현, 저장 값은 소문자). 컬럼은 MySQL에서 `cs_char(36)`(`utf8mb4_0900_as_cs`)이라 정규화를 우회한 값이 DB에서 조용히 합쳐지지 않습니다. 같은 이유로 `subject_id`·`endpoint`(경로는 대소문자 구분)·`request_hash`·`state`와 세션 테이블의 `token_hash`·가입 세션 `google_sub`도 `cs_string`입니다. `google_email`은 이메일이므로 대소문자를 구분하지 않는 `email_string`(`utf8mb4_0900_as_ci`)입니다. 핸들러에서 `begin_nested()`를 써도 됩니다: 해제된 savepoint의 쓰기는 `run_idempotent`의 최종 commit에 포함되고 롤백된 savepoint는 미커밋 쓰기 감지(`UncommittedWriteError`)를 일으키지 않습니다(`tests/test_idempotency.py`).
- **멱등 기록 보관 정책**: 재현 기한은 24시간(`expires_at`)이고, 실제 보관은 그 뒤 첫 정리 주기까지입니다. 주기 작업 `idempotency-retention`(`app/lifespan.py`, 5분 간격)이 만료 기록을 1,000건 배치로 주기당 최대 20배치 삭제하고(남은 적체는 다음 주기), lease가 살아 있는 PROCESSING 기록은 만료여도 남기며, 실패한 주기는 다음 주기에 다시 실행합니다(`SKIP LOCKED`, 기본 키 삭제).
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
| `set_session_cookie`·`clear_*_cookie`를 직접 호출 | 핸들러가 반환 전에 `session.commit()`하고 `SessionDep`이 응답 전에 세션을 닫으므로(위 commit 규칙) commit 실패 시 쿠키 없이 5xx가 된다. 쿠키를 설정한 뒤 commit하는 순서가 드러나도록 `commit_then_*` 사용을 권장한다 |
| `consume_registration_session(db=db)` | commit하지 않음. 가입 handler(`run_idempotent` 안)에서 호출하면 응답 전 commit됩니다 |
| 정지 계정 감지(`_resolve_member`) | 보장. 세션 폐기를 commit한 뒤 403을 냅니다 |
| `last_seen_at` 갱신 | 요청 세션의 마지막 commit에 편승합니다. 실패해도 응답 의미가 바뀌지 않는 최적화입니다 |
| 멱등성 예약·해제(`_reserve`·`_release`) | 자체 트랜잭션이 즉시 commit됩니다 |

`tests/test_commit_before_cookie.py`에서 위 보장을 commit 실패 주입으로 검증합니다. 직접 쿠키 설정 후 요청 말미 commit이 실패하는 경우(`test_bare_cookie_setter_cannot_outrun_the_request_commit`)도 위 commit 규칙(`SessionDep`)으로 통과합니다(예전 strict xfail은 제거됨).

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
| `BACKGROUND_JOBS` | 생략·빈 값·`on`이면 주기 작업(알림 sweep·초대 메일·미디어 보관 정리)과 AI/STT 작업 실행기 실행, `off`면 중지(테스트가 사용). 그 외 값은 시작 실패 |
| `WORK_REMINDER_HOUR` | 근무 전날 안내 시각(서울 0~23시, 기본 18). 그 외 값은 시작 실패 |
| `APP_ENV` | `local`(기본)·`dev`·`production`. 쿠키 Secure·DB 필수 여부·설계 문서 제공·일부 local 전용 설정을 정한다 |
| `DB_HOST` / `DB_PORT` / `DB_NAME` / `DB_USER` / `DB_PASSWORD` | MySQL 연결. `dev`·`production`에서 필수 |
| `GOOGLE_CLIENT_ID` / `GOOGLE_CLIENT_SECRET` / `GOOGLE_REDIRECT_URI` | Google OAuth. 없으면 로그인 시작·callback이 500([인증 설계](docs/auth-design.md#105-구현과-운영-설정)) |
| `FRONTEND_ORIGIN` | 로그인 후 이동·초대 링크의 고정 frontend origin(ASCII, local 외에는 https) |
| `KAKAO_REST_API_KEY` | 매장 주소 검증(Kakao 로컬 API). 없으면 매장 추가가 500 |

도메인별 변수는 각 절의 표에 있다: `ADMIN_PASSWORD_HASH`(매장·승인), `INVITATION_MAIL_*`·`SMTP_*`·`MAIL_FROM`(근무자 초대), `AI_PROVIDER`·`OPENAI_*`·`MEDIA_ROOT`·`TASK_RUNNER_*`(AI·STT·미디어 기반).

테스트·도구 전용 변수(앱은 읽지 않음):

| 이름 | 용도 |
| --- | --- |
| `JIDAN_REQUIRE_MYSQL=1` | MySQL 테스트를 건너뛰지 않고 실패시킨다 |
| `JIDAN_RUN_OPENAI=1` (+ `OPENAI_API_KEY`) | `@pytest.mark.openai` 실키 테스트 실행 |
| `JIDAN_SPEC_COVERAGE=<파일>` | 테스트가 호출한 operation·상태를 기록(`python -m tests.spec_coverage report <파일>`) |
| `JIDAN_E2E_FULL=1` | 시연 E2E를 실시간 미응답 만료까지 전체 실행 |
| `JIDAN_LARGE_CONTENT=1` | 매뉴얼 초안 명세 상한(단계 2만 행·최대 길이 4바이트 문자) MySQL 회귀 테스트(`tests/test_manual_large_content.py`, 약 30초·60MB) |
| `JIDAN_QA_SAMPLES=<파일>` | Q&A 실키 테스트의 질문·답변 표본을 파일에 덧붙임 |
| `E2E_ADMIN_PASSWORD` | 이미 떠 있는 서버에 시연 E2E를 돌릴 때 관리자 비밀번호 원문 |

### 한계

- 레이트 리미터는 프로세스 메모리에만 있어 워커·서버가 여럿이면 카운터가 공유되지 않고(실제 한도 = 한도 × 프로세스 수) 재시작하면 초기화됩니다. 정확한 제한이 필요하면 DB나 캐시로 옮겨야 합니다.
- 레이트 리미터는 추적 키를 최대 10,000개로 제한합니다. 만료된 키만 앞에서 하나씩 제거하고(분할상환 O(1)), 아직 창 안에 있는 키는 한도 초기화를 막기 위해 절대 밀어내지 않습니다. 가득 차면 **새 키를 429(fail-closed)** 로 거절하므로 서로 다른 주소를 대량으로 보내면 새 클라이언트가 최대 한 창(로그인 60초·관리자 600초) 대기할 수 있습니다(DoS 특성). CPU 소모나 기존 한도 초기화는 불가능합니다. `TRUST_FORWARDED_FOR=true`는 클라이언트가 키를 위조할 수 없을 때(신뢰 프록시가 헤더를 덧붙일 때)만 켜세요.
- 세션 만료 시각 갱신은 최대 1분 간격으로만 기록하며, 만료된 세션·멱등성 행의 정기 삭제 작업은 아직 없습니다.
- 가입 세션 쿠키는 Path가 `/api/auth`라 그 아래 endpoint에서만 전송됩니다.
- 관리자 비밀번호 API는 비밀번호가 body에 있어 `page`/`size`를 body에서 받습니다. `Pagination`은 query 전용이므로 body 검증은 해당 endpoint 모델에서 같은 범위(0 이상, 1~100)로 맞춥니다.

## API 설계

[Figma 기반 OpenAPI 명세](openapi.yaml)를 제공합니다.
구현 현황과 도메인별 설계 문서는 맨 위 [구현 범위 요약](#구현-범위-요약)의 표를 본다. 외부 인증 설정과 프론트 `__auth` 진입점 연결은 [인증 설계](docs/auth-design.md#105-구현과-운영-설정), 프로필 검증은 [프로필 구현과 검증](docs/worker-profile-design.md#106-구현과-검증)을 참고한다.

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

## 매장·승인 (#107)

- `app/store_access.py`: 매장 하위 endpoint의 공용 판정. `load_owned_store(db, owner_id, store_id, *, require_approved=True, lock=False, not_found=STORE_NOT_FOUND)`는 없는 매장과 타 점주 매장을 같은 404로, 본인 승인 대기 매장을 403 `STORE_APPROVAL_REQUIRED`로 처리하고 `lock=True`면 매장 행을 `FOR UPDATE`로 잠근다. `StoreIdPath`는 형식이 틀린 `storeId`를 422로 거절한다. 시점 판정 SQL 조건 `valid_grant_clause(now)`(granted_at <= now < valid_until, 미종료), `pending_invitation_clause(now)`, `lasting_grant_clause(now)`(24시간 넘게 유효)를 함께 제공한다.
- `app/stores.py`: 점주 매장 목록·상세·추가·관리 요약. 매장 추가는 `run_idempotent` 트랜잭션에서 매장과 PENDING 승인 신청을 함께 만들고, 사업자 번호 UNIQUE 위반은 409 `STORE_ALREADY_REGISTERED`다.
- `app/store_approvals.py`: 관리자 승인 신청 조회·승인. 회원 세션·CSRF 없이 body `password`만으로 인증한다(명세 `security: []`, 응답 코드에 403이 없어 Origin 검사를 하지 않는다). 승인은 신청 → 매장 순으로 잠그고 `status=PENDING` 조건부 갱신으로 최초 `approvedAt`을 한 번만 기록한다.
- `app/admin_password.py`: 관리자 비밀번호는 원문 대신 `ADMIN_PASSWORD_HASH`(원격 PBKDF2_SHA256 또는 기존 scrypt)만 서버 설정에 둔다. 서버에서 `python -m app.admin_password`로 생성해 `runtime.env`에 넣는다. 값이 없거나 형식이 틀리면 관리자 API가 500을 반환한다. 비밀번호는 trim하지 않고 상수시간 비교하며 응답·로그에 남기지 않는다.

| 이름 | 용도 |
| --- | --- |
| `ADMIN_PASSWORD_HASH` | 관리자 공용 비밀번호의 PBKDF2_SHA256 또는 기존 scrypt 해시. 원문 비밀번호는 저장하지 않음 |

## 알림 (#116)

`app/notifications.py`의 `record_notification(db, *, recipient_user_id, type, target, event_key, body, title=None, created_at=None)`로 상태 전이 트랜잭션 안에서 알림을 저장한다(commit은 호출자). type별 허용 target과 `event_key` 규칙은 [알림 계약](docs/notification-design.md#116-구현)을 따른다. 조회·읽음 API는 `app/notification_views.py`다.

## 관심 매장 (#117)

`app/favorite_stores.py`가 본인 관심 매장 등록·해제·목록을 제공한다. 규칙은 [관심 매장 계약](docs/favorite-store-design.md#117-관심-매장-구현)을 따른다.


## 홈·캘린더 (#117)

`app/home.py`가 점주·근무자 홈 요약과 월별 대타 근무 캘린더(`/api/users/me/calendar/events`, `/api/owners/me/calendar/events`)를 제공한다. 한 응답의 건수와 목록은 같은 `asOf`(jobs 시계 `app.jobs.common.now`)로 계산하고, 캘린더에는 진행 중이거나 끝난 대타 확정만 나온다(정기 접근 기간·가능 시간은 이벤트가 아님). 규칙은 [홈 계약](docs/home-design.md)과 [캘린더 계약](docs/calendar-design.md)을 따른다.

## 근무자 초대 (#108)

- `app/invitations.py`(점주 측 생성·목록·재전송·취소)와 `app/invitation_responses.py`(근무자 측 링크 확인·수락·거절). 초대 상태는 컬럼 없이 결과 시각과 두 기한(링크 7일, 선택 접근 종료)으로 계산하며 기한 도달 시점부터 EXPIRED다(종료 배타적). 두 기한이 모두 지났으면 먼저 도달한 기한으로 410 코드를 정한다.
- 이메일은 trim·소문자로 저장·비교하고 점/plus 별칭은 통합하지 않는다([ERD 결정 기록](docs/erd/README.md)). 초대 생성은 앞뒤 ASCII 공백만 지우고, 출력 가능한 ASCII가 아닌 글자(NBSP·폭 없는 문자·전각·악센트·키릴 등)가 남으면 정규화하지 않고 422 `VALIDATION_ERROR`(`email`, `INVALID_FORMAT`)로 거절한다. 수락·거절·확인은 세션 사용자의 검증된 Google 이메일이 초대 이메일과 같아야 하며, 다르면 초대·매장 정보 없이 403 `INVITATION_EMAIL_MISMATCH`다.
- 잠금 순서는 매장 행 → 초대 행이며 모든 전이는 "결과 없음" 조건부 UPDATE다. MySQL REPEATABLE READ에서 잠금 대기 후 최신 커밋을 보도록 잠금 전에 읽기 스냅샷을 끝낸다(`db.commit()`, `run_idempotent` 핸들러는 이미 새 트랜잭션). 근무자 접근 종료(#109)도 같은 순서를 따른다.
- 토큰은 256bit(`secrets.token_urlsafe(32)`)이고 SHA-256만 저장한다. 링크는 `FRONTEND_ORIGIN` + `/invitations/accept#token=…`(fragment)이며 원문 토큰·링크는 응답·로그·오류에 남기지 않는다.
- 메일 요청은 `invitation_mail_outbox`(0007)에 초대와 같은 트랜잭션으로 저장한다. 수신자·매장명·링크는 Fernet으로 암호화한 payload에만 있고 발송·포기·폐기 시 지운다. 키나 origin 설정이 없으면 503 `DELIVERY_UNAVAILABLE`과 전체 rollback이다. 재전송·취소는 미발송 메일을 폐기한다.
- 발송(`deliver_queued_mail`)은 응답 직후 BackgroundTask와 60초 주기 작업(`PERIODIC_JOBS`의 `invitation-mail`)에서 돈다. 짧은 트랜잭션에서 `FOR UPDATE SKIP LOCKED`로 고른 행에 `claim_token`과 5분 lease(0020)를 기록하고, SMTP 호출은 트랜잭션 밖에서 하며, 점유 토큰 조건부 UPDATE로 마무리한다. 그래서 두 경로·여러 프로세스가 같은 메일을 중복 발송하지 않는다(발송 직후 프로세스가 죽으면 lease 만료 후 재발송될 수 있음). 실패는 1분·5분·15분·1시간 간격으로 재시도하고 5회째 실패하면 FAILED다. 수락할 수 없게 된 초대의 메일은 발송 전에 DISCARDED로 바뀐다. 같은 주기 작업이 처리 후 30일(설계 제안, 명세는 기한 미정)이 지난 SENT·FAILED·DISCARDED 행을 기본 키 배치로 삭제한다(payload는 처리 시점에 이미 지워짐).
- 메일은 한국어 제목·본문, 매장명, 링크·접근 종료 시각(한국 시간)과 링크를 텍스트·HTML 두 형식으로 담는다.

| 이름 | 용도 |
| --- | --- |
| `INVITATION_MAIL_KEY` | outbox payload 암호화 Fernet 키(`python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"`). 쉼표로 여러 개를 줄 수 있으며 첫 키로 암호화하고 모든 키로 복호화한다. 교체는 새 키를 앞에 추가하고, 이전 키로 저장된 미발송 메일이 처리된 뒤 이전 키를 뺀다. 목록에서 빠진 키로 저장된 메일은 FAILED가 된다. 발송 시점에 키가 없거나 형식이 틀리면 아무 행도 점유하거나 바꾸지 않고(QUEUED·시도 횟수·백오프 유지) 발송만 멈춘다 |
| `INVITATION_MAIL_BACKEND` | 발송 어댑터. `smtp`(dev·운영), `memory`(프로세스 메모리, local 기본·dev 허용·production 거부), `disabled`(대기열만). 미설정 시 local은 `memory`, 그 외는 `disabled`(시작 시 경고). 알 수 없는 값은 시작 실패 |
| `SMTP_HOST` / `SMTP_PORT` | SMTP 서버. 포트 기본값은 보안 방식별 587(starttls)·465(ssl)·25(none) |
| `SMTP_SECURITY` | `starttls`(기본)·`ssl`(암묵적 TLS)·`none`(평문, `APP_ENV=local`만). TLS는 인증서·호스트 이름을 검증 |
| `SMTP_USERNAME` / `SMTP_PASSWORD` | SMTP AUTH 계정. 둘 다 있거나 둘 다 없어야 하며 평문 연결에서는 거부. 비밀번호는 로그·repr에 남기지 않음 |
| `SMTP_TIMEOUT_SECONDS` | 연결·응답 타임아웃 유한한 1~60초(기본 10), NaN/무한대는 시작 시 거부 |
| `MAIL_FROM` | 발신 주소(예: `Jidan <no-reply@jidan.example.com>`) |
| `FRONTEND_ORIGIN` | 초대 링크의 고정 frontend origin(인증에서 쓰는 값과 같음) |

## 대타 공고·지원·근무 요청 (#110~#114)

`app/jobs/`가 공고 4개, 탐색 2개, 지원 4개, 지원자 2개, 근무 요청 8개 endpoint를 제공한다. 모듈: `postings`·`search`·`applications`·`applicants`·`work_requests`(라우트), `state`(상태 전이·잠금), `common`(서울 시각·직렬화·시계 `now()`), `access`(대타 접근 표현), `queries`(홈·캘린더용 조회).

### 상태 전이

| 대상 | 전이 | 주체·조건 | 함께 바뀌는 것 |
| --- | --- | --- | --- |
| 공고 | RECRUITING→CLOSED | 수락 | closedAt=수락 시각 |
| 공고 | RECRUITING→CLOSED | 수동 마감, 유효 PENDING 없음 | 현재 APPLIED/REQUESTED → NOT_SELECTED |
| 공고 | CLOSED→RECRUITING | 근무 시작 전 확정 철회 | closedAt=null |
| 공고 | CLOSED 재마감 | 최신 revision | 변경 없음(200 현재 상태) |
| 지원 | (새 행) APPLIED | 모집 중·시작 전·미확정, 본인 유효 지원 없음 | 이름·나이·경력 스냅샷 |
| 지원 | APPLIED/REQUESTED→WITHDRAWN | 근무자 | 유효 PENDING → CANCELLED |
| 지원 | APPLIED→REQUESTED | 점주 요청 | PENDING 요청 생성 |
| 지원 | REQUESTED→APPLIED | 거절·요청 철회·만료 | |
| 지원 | REQUESTED→CONFIRMED | 수락 | 확정 근무·TEMPORARY 접근 |
| 지원 | APPLIED/REQUESTED→NOT_SELECTED | 다른 지원자 수락·수동 마감 | 수락이면 원인 효과 기록 |
| 지원 | NOT_SELECTED→APPLIED | 원인 확정 철회, 상태·revision 불변일 때만 | restored_at |
| 지원 | CONFIRMED→APPLIED | 확정 철회 | |
| 지원 | CONFIRMED→(COMPLETED) | 근무 종료(조회 투영) | |
| 지원 | APPLIED/REQUESTED→(NOT_SELECTED) | 확정 없이 근무 시작(조회 투영) | 신청 중 → 종료 탭, 지원자 수 제외 |
| 요청 | PENDING→ACCEPTED/DECLINED | 근무자, now < expiresAt | respondedAt=endedAt=응답 시각 |
| 요청 | PENDING→CANCELLED | 점주 철회(now < expiresAt)·지원 철회 | endedAt |
| 요청 | PENDING→EXPIRED | now ≥ expiresAt(조회 투영, 다음 쓰기·스윕이 기록) | endedAt=expiresAt, revision 불변 |
| 요청 | ACCEPTED→CONFIRMATION_WITHDRAWN | 점주, now < startAt | 확정 withdrawn_at, 해당 접근 revoked_at(최초값 유지) |

금지 전이는 모두 409다: 종료된 요청 응답·철회(`WORK_REQUEST_NOT_PENDING`/`WORK_REQUEST_EXPIRED`), 확정·미선정·완료 지원 철회(`APPLICATION_NOT_WITHDRAWABLE`), ACCEPTED가 아닌 요청의 확정 철회(`WORK_CONFIRMATION_NOT_ACTIVE`), 시작 이후 확정 철회(`JOB_ALREADY_STARTED`).

### 동시성·시간

- 모든 쓰기는 공고→요청→지원→근무자→접근 순으로 잠근다. 근무자 응답은 그 앞에서 매장 행을 공유 잠금한다(매장 도메인은 매장 행을 가장 먼저 잠그고, 수락의 접근 행 삽입이 매장 행 FK를 기다리므로 근무자 행을 쥔 채 매장을 기다리면 초대 수락과 교착한다). 상태 전이 핸들러는 MySQL에서 READ COMMITTED로 시작하고(`begin_transition`) 잠금 조회는 identity map을 갱신한다(`locked`). 수락은 근무자 행도 잠가 다른 공고의 동시 수락과 직렬화하고 실제 시간 중첩(인접 허용)을 막는다.
- 공고·지원·요청의 상태가 바뀌면 각 revision이 오른다. 지원·요청 변화도 공고 revision을 올린다(명세 예시: 재지원 후 공고 revision 4). 만료 기록은 시각의 투영이라 revision을 바꾸지 않는다.
- 요청 기한은 `min(요청+1시간, 근무 시작)`이고 정확히 기한부터 EXPIRED다. 근무일·시각은 `Asia/Seoul`이며 `ends_next_day`이면 다음 날 종료다.
- 확정 없이 근무 시작을 지난 공고(선정하지 않았거나 확정 철회로 다시 연 공고)의 APPLIED/REQUESTED 지원은 조회 시 NOT_SELECTED로 투영한다(`common.unselected_at_start`, 저장 상태·revision 불변). 근거: 명세의 탭 정의는 신청 중=APPLIED/REQUESTED, 종료=WITHDRAWN/NOT_SELECTED/COMPLETED로 종료 탭에 APPLIED를 둘 수 없고, 시작 이후에는 지원·요청·수락이 모두 막혀(미래 시작 조건) 선정 없이 끝난 수동 마감과 같으며 명세는 그 결과를 NOT_SELECTED로 정한다. COMPLETED와 같은 읽기 투영이므로 목록 탭·건수, 상세, 점주 ACTIVE 목록, `applicantCount`(WITHDRAWN/NOT_SELECTED 제외), 공고 상세 `myApplicationId`, 철회(409 APPLICATION_NOT_WITHDRAWABLE), 재지원(409 JOB_STARTED)이 모두 이 투영을 따른다. 공고 자체의 RECRUITING은 바꾸지 않는다(점주가 마감하면 저장 상태도 NOT_SELECTED).
- `expire_due_requests(at)`는 기한이 지난 PENDING 요청을 EXPIRED로 기록하는 일괄 함수이며, 알림 sweep `work-request-expiry`(`app/notification_sweeps.py`, 1분 주기)가 호출해 `WORK_REQUEST_NO_RESPONSE` 알림을 같은 트랜잭션에 남긴다.
- 매뉴얼 게시 여부(`access.published_manual_version`)는 `app.worker_stores.published_version_id`(매장의 `store_manuals.current_published_version_id`)를 읽는다. 게시본이 있으면 PUBLISHED, 없으면 NOT_PUBLISHED이며 근무 확정 여부와는 무관하다.

### 백그라운드 작업

`app/lifespan.py`가 백그라운드 작업의 유일한 등록 지점이다. 주기 작업은 `PERIODIC_JOBS`에 `PeriodicJob(name, interval_seconds, run)`(`app/periodic.py`)로, 별도 시작·종료가 필요한 작업은 `LIFESPANS`에 lifespan 컨텍스트로 추가한다. 시각 기반 알림은 [알림 계약](docs/notification-design.md#시각-기반-알림-sweep)의 sweep 계약을 따라 `app/notification_sweeps.py`의 `SWEEPS`에 등록한다. `WORK_REMINDER_HOUR`(서울 0~23시, 기본 18)는 근무 전날 안내 시각이다.

## 근무자 관리·근무자 매장 선택 (#109)

- `app/store_workers.py`: 점주의 근무자 목록·상세·자료 접근 종료. 근무자는 매장당 한 항목(이름만 노출)이고 상태는 사용 가능한 접근 중 ACTIVE가 있으면 ACTIVE, 모두 24시간 내 종료면 EXPIRING, 없으면 ENDED다. 종료는 매장 잠금 아래에서 미종료 접근 revoke·같은 이메일의 수락 가능 초대 취소·미발송 메일 폐기를 한 트랜잭션으로 처리하며 재요청도 204다.
- `app/worker_stores.py`: 근무자 본인의 사용 가능 매장 목록과 매장별 재확인. `publishedVersionId`는 현재 게시 매뉴얼 버전 ID이고, 게시본이 없으면 null이다(`published_version_id`, 게시 시 #118이 포인터를 교체).
- 자료 API(매뉴얼·체크리스트·AI Q&A)는 매 요청, 긴 응답 중에도 `app.store_access.require_worker_store_access(db, worker_id, store_id, now=...)`로 판정한다. 계정 ACTIVE WORKER, 매장 APPROVED·소유자 ACTIVE OWNER, 판정 시각에 유효한 접근(정기·대타) 하나 이상이 조건이며 실패는 기본 404 `RESOURCE_NOT_FOUND`다(Q&A가 이 계약). 게시 매뉴얼 열람은 명세대로 404 `STORE_NOT_FOUND`이고 OWNER도 허용하므로 `app.manual_content.require_manual_reader`를 쓴다. 유효 접근이 있는데 매장 승인이 없으면 두 경로 모두 403 `STORE_APPROVAL_REQUIRED`이고, 점주가 비ACTIVE이면 매뉴얼 열람은 403, Q&A는 404다. 목록 응답의 권한을 접근 증명으로 쓰지 않는다.

## 일반회원 초대함 (#115)

- `app/invitation_inbox.py`: 세션의 검증된 Google 이메일과 초대 대상(정규화)이 같은 초대만 목록·상세·응답한다. 응답은 이메일 링크와 같은 `respond_to_invitation`을 `run_idempotent` 안에서 실행하며(`commit=False`), 오류만 초대함 계약(불일치 404, 완료·기한 409)으로 바꾼다. 같은 매장에 유효한 REGULAR 접근이 이미 있으면 초대함 수락은 명세대로 200과 기존 접근을 반환하고 아무것도 바꾸지 않는다(초대는 PENDING 유지, token API는 409 `WORKER_ALREADY_HAS_ACCESS`). TEMPORARY(대타) 접근은 정기 초대의 생성·수락을 막지 않는다. 알림에는 토큰을 담지 않고 초대 id로 이 화면에 진입한다.

## 데모 시드와 시연 E2E (#122)

### 데모 시드

```bash
cd back-end
export APP_ENV=local DB_HOST=127.0.0.1 DB_PORT=3317 DB_NAME=jidan_local DB_USER=... DB_PASSWORD=...
python -m alembic upgrade head
python -m app.demo_seed        # 다시 실행하면 데모 데이터만 지우고 같은 데이터로 다시 만든다
```

- `APP_ENV=production`이면 거부하고, `APP_ENV`는 `local`·`dev`만, `DB_NAME`은 `_local`·`_dev`·`_demo`·`_test`로 끝나야 한다(거부 시 종료 코드 2). 개발 서버 DB `jidan_dev`에는 `APP_ENV=dev`로 넣을 수 있다.
- 데모 계정의 Google `sub`는 `demo-seed:`로 시작해 실제 Google 로그인으로는 들어갈 수 없다. 화면을 채우는 데이터다.
- 알림: API 전이와 같은 `app.notification_events` 함수로 매장 승인·초대·초대 수락·지원·근무 요청·근무 확정·매뉴얼 게시 알림 17건을 남기고, 하루보다 오래된 것은 읽음으로 둔다.
- 내용: 승인 매장 `지단 카페 월계점`과 승인 대기 매장 `지단 분식 월계점`, 근무자 4명(김지수·이민준·박서연·최도윤)의 프로필·경력·가능 시간, 수락된 초대(정기 접근)와 대기 초대, 모집 중 공고 2개(지원자·대기 근무 요청 포함), 확정 공고, 완료된 과거 근무, 선정 없이 마감된 공고, 관심 매장.
- 매뉴얼(`app.manual_demo_seed`): `지단 카페 월계점`에 3일 전 게시된 매뉴얼 1개(오픈조·마감조, 공통 업무 `포스 마감`·`매장 청소`·`음료 레시피`(절차 미정, 확인 필요 항목 1개), 오픈조 `오픈 준비`, 규정 `복장 규정`). AI 호출 없이 정적 내용을 초안 생성·게시와 같은 함수(`prepare_content`·`write_initial_content`·`publish_draft`)로 넣으므로 확인 기록과 김지수의 MANUAL_PUBLISHED 알림도 남는다. id는 고정이다. 시연 Fake AI(`e2e.ai_scenario`)는 "포스"가 들어간 질문에 `포스 마감` 근거를 인용한다.
- 매뉴얼·AI Q&A 데이터의 초기화와 추가 단계는 `app.demo_seed.EXTRA_RESETS`·`EXTRA_SEEDS`(`seed(db, ids, now)`)에 붙인다. `users`를 참조하는 새 테이블이 생기면 `tests/test_demo_seed.py`가 초기화 누락을 알려 준다.

### 시연 시나리오 E2E

실제 uvicorn 서버에 HTTP로 18단계를 실행한다: 점주 가입(지역 밖 주소 422) → 승인 전 제한 → 관리자 승인 → 근무자 3명 가입 → 초대 2건 → 초대함 수락 → 곧 시작하는 공고의 요청 준비 → 공고 → 지원 → 요청 철회 → 수락 → 확정 철회(재오픈)와 재확정 → 대타 확정자의 실제 메일 링크 수락 → 홈·캘린더 → 요청 미응답 만료 → 알림 읽음 → 보안 확인. 모든 응답은 `tests/api_contract.validate_response`로 OpenAPI와 대조하고, 쓰기에는 Origin·CSRF 토큰·Idempotency-Key를 실제로 보낸다. 실행 시간은 약 2분이다(요청 기한을 실제 시간으로 기다림). `--no-realtime-expiry`는 그 대기와 두 단계(요청 준비·미응답 만료)를 빼고 16단계만 실행한다.

```bash
cd back-end
APP_ENV=local DB_HOST=127.0.0.1 DB_PORT=3317 DB_NAME=jidan_local DB_USER=... DB_PASSWORD=... \
  python -m e2e.demo_scenario --start-server     # 서버를 띄우고 실행한 뒤 종료
  python -m e2e.demo_scenario --cleanup          # 러너가 만든 계정(e2e:)과 그 데이터만 삭제
```

- `--start-server`는 `python -m e2e.serve`(앱 그대로 + 아래 Kakao 처리)를 관리자 비밀번호 해시·초대 메일 키·허용 Origin(`--origin`, 기본 `http://localhost:5173`)을 생성해 띄운다.
- 이미 떠 있는 서버(예: Docker 이미지)에 실행하려면 `--port`(또는 `--base-url`), 서버의 `ALLOWED_ORIGINS`와 같은 `--origin`, `E2E_ADMIN_PASSWORD`(서버 `ADMIN_PASSWORD_HASH`의 원문)를 준다. 러너는 서버와 같은 DB(`DB_*`)에 가입 세션을 만들 수 있어야 한다.
- Google 로그인: 자동화할 수 없어 OAuth callback이 하는 것과 같은 `create_registration_session`으로 가입 세션만 DB에 만들고, 그 뒤는 실제 가입 API다. 앱에는 테스트용 우회 경로가 없다.
- Kakao 주소 검증: `KAKAO_REST_API_KEY`가 있으면 실제 Kakao를 호출한다. 없으면 `e2e.serve`가 그 서버 프로세스에서만 `dapi.kakao.com`을 데모 주소(광운로 20 → 월계1동, 월계로 372 → 월계2동) 고정 응답으로 바꾼다. 주소 일치·우편번호·행정동 검증 코드는 그대로 실행된다.
- 메일: 러너가 SMTP 수신 서버(`e2e/mailbox.py`, aiosmtpd)를 띄우고 서버는 `INVITATION_MAIL_BACKEND=smtp`, `SMTP_SECURITY=none`(local 전용)으로 그곳에 보낸다. 서버의 주기 발송 작업이 보낸 메일의 제목·매장명·링크 만료 문구를 단언하고, 링크 fragment의 토큰으로 `/api/store-invitations/preview`·`accept`를 호출한다. 이미 떠 있는 서버에는 `--smtp-port`(Docker면 `--smtp-listen 0.0.0.0`)를 주고 서버 `SMTP_HOST`/`SMTP_PORT`를 그쪽으로 맞춘다.
- 알림: 단계마다 수신자별 알림을 종류별 건수와 (종류, 대상) 건수까지 정확히 대조하고 읽음 처리·`unreadCount`·홈 건수를 확인한다. 실제 날짜에 따라 생길 수 있는 `WORK_REMINDER`만 대조에서 뺀다.
- 미응답 만료: 시작이 65~125초 뒤인 공고에 요청을 보내 기한이 근무 시작이 되게 하고, 실제로 기다린 뒤 EXPIRED 조회 → 마감 → `WORK_REQUEST_NO_RESPONSE` 1건(마감 트랜잭션 또는 서버 sweep 중 먼저 실행된 쪽이 기록)을 확인한다. 시계 조작이나 sweep 직접 호출은 없다.
- 대타·정기 접근 공존: 근무자 A는 대타 확정(TEMPORARY, 철회된 첫 확정 이력 포함) 뒤에 메일 링크로 정기 초대를 수락한다. TEMPORARY는 초대 수락을 막지 않으므로 수락 후 근무자 본인 접근 상세(`/api/users/me/stores/{storeId}/access`)에 REGULAR·TEMPORARY(ACTIVE)·TEMPORARY(REVOKED)가 함께 나오고, 매장 목록에는 한 번만, 점주 근무자 상세에도 두 종류가 남는지 확인한다.
- 출력: 단계별 `PASS`/`FAIL`/`SKIP`(소요 시간), 수신자별 알림 건수, 실패한 호출의 `code`·`requestId`. 실패 시 종료 코드 1, 대상 거부 시 2다. E2E가 만든 계정·매장은 실행마다 고유 이름(`e2e:<run>:...`)으로 남는다.
- 정리: `--cleanup`은 Google `sub`가 `e2e:`로 시작하는 계정과 그 계정에 속한 데이터(매장·공고·지원·초대·접근·알림·세션·멱등 기록, 미완료 가입 세션 포함)를 데모 시드 reset과 같은 코드(`app.demo_seed.delete_accounts`)로 지우고 끝낸다. 같은 가드(`APP_ENV=production` 거부, DB 이름 `_local/_dev/_demo/_test`)를 적용하며 데모 시드 계정(`demo-seed:`)과 실제 계정은 대상이 아니다.
- `tests/test_e2e_demo.py`가 같은 시나리오를 MySQL 테스트로 실행한다(`JIDAN_REQUIRE_MYSQL=1`). 기본은 실시간 미응답 만료를 뺀 빠른 경로(약 10초)이고 `JIDAN_E2E_FULL=1`이면 전체(약 2분)를 실행한다.

#### 매뉴얼·AI 단계 (M0~M14)

18단계 뒤에 매뉴얼·AI 15단계가 이어진다(`e2e/manual_scenario.py`, Q&A는 `e2e/manual_qa_steps.py`, `--skip-manual`로 뺄 수 있다).

| 단계 | 내용 |
| --- | --- |
| M0 | 근무자 D를 새 공고에 확정해 TEMPORARY 접근만 가진 근무자를 만든다 |
| M1 | 매뉴얼 상태가 비어 있고, 근무자는 `MANUAL_NOT_PUBLISHED`를 받는다 |
| M2 | 사진(PNG)과 녹음을 업로드한다. 같은 키·같은 파일은 재현, 같은 키·다른 파일은 `IDEMPOTENCY_KEY_REUSED` |
| M3 | 녹음 전사가 READY가 되고, 다시 요청하면 200으로 같은 전사를 돌려준다 |
| M4 | 인터뷰를 시작하면 인텐트 6개, 첫 질문은 BASE·depth 0. 두 번째 시작은 `INTERVIEW_ALREADY_EXISTS` |
| M5 | 모든 질문에 답한다. RULES는 **음성(VOICE, M3 전사)**, WORK_STRUCTURE는 사진 첨부. Fake에서는 COMMON_TASKS PROBE 1회, EQUIPMENT PROBE depth 5까지(NEEDS_DETAIL), 질문 12개. 낡은 revision은 `REVISION_CONFLICT`, 답한 질문은 `QUESTION_ALREADY_ANSWERED` |
| M6 | WORK_STRUCTURE 이해 정정(08:30) → 확인, COMMON_TASKS의 포스 섹션에 사진을 단다 |
| M7 | 초안이 생성된다: 정정한 근무조, 섹션 id·사진 보존, EQUIPMENT가 OPEN issue |
| M8 | 초안 전체 편집(revision 증가, 낡은 revision 거부)과 섹션 정정(DRAFT_CORRECTION) |
| M9 | 미리보기가 초안과 같다. issue를 일부만 확인하면 `MANUAL_REVIEW_REQUIRED`, 전부 확인하면 게시. **MANUAL_PUBLISHED는 REGULAR(A·C)와 TEMPORARY만 가진 D에게 가고 접근 없는 B에게는 가지 않는다** |
| M10 | A·C·D가 매장 목록의 게시 버전, 섹션, 사진 byte를 읽는다. 녹음은 `MANUAL_RESOURCE_NOT_FOUND`, B는 `STORE_NOT_FOUND` |
| M11 | Q&A "포스 마감" 질문이 ANSWERED이고, 인용 섹션과 발췌가 게시본 단계 원문과 같다 |
| M12 | 근거 없는 질문은 NEEDS_OWNER(인용 0). 음성 질문(QA 녹음 → 전사 → VOICE 질문). 대화를 복원하면 3개가 순서대로 나온다 |
| M13 | 점주가 C의 접근을 종료하면 C의 대화 조회·질문·새 대화는 `RESOURCE_NOT_FOUND`, 게시본은 `STORE_NOT_FOUND`. A·D는 계속 읽는다 |
| M14 | 두 번째 인터뷰로 버전 2를 게시하면, 버전 1 id로 읽을 때 `MANUAL_VERSION_CHANGED`. Fake 모드 전용 |

- AI 작업은 서버의 백그라운드 작업 실행기(`TASK_RUNNER_MODE=background`)가 처리하고, 러너는 공개 GET을 0.5초 간격으로 폴링한다(상한 Fake 30초, 실키 300초). 미디어는 실행마다 임시 `MEDIA_ROOT`에 저장하고 끝나면 지운다.
- Fake 모드(기본): `e2e.serve`가 서버 프로세스 안에서 `app.ai.set_ai_provider`로 `e2e.ai_scenario`의 결정적 `FakeAiProvider`를 설치한다(앱 코드 변경 없음). 출력은 실제 파싱·서버 재검증을 그대로 거친다.
- 실키 모드: `--ai live`는 `JIDAN_E2E_OPENAI=1`과 `OPENAI_API_KEY`가 모두 있어야 실행된다(과금). `--ai-live-ops`에 적은 연산만 OpenAI로 보내고 나머지는 Fake가 처리한다.
  - 기본 연산은 요약·초안·정정·Q&A 답변·전사로, 15회다.
  - 질문 생성과 충분성 판정은 Fake로 둔다. 그래서 질문 수가 고정되고 호출 수도 고정된다.
  - `--ai-call-limit`(기본 20)을 넘는 호출은 `NOT_CONFIGURED`로 거부하며 재시도하지 않는다.
  - 실행이 끝나면 연산별 호출 수를 출력한다.
  - 실키에서는 문장을 단언하지 않고 상태 전이·귀속·인용 불변식만 확인한다.
  - 녹음은 macOS `say`/`afconvert`로 합성한 한국어 음성이다.

```bash
set -a; . <키 파일>; set +a     # OPENAI_API_KEY를 환경 변수로만 받는다
JIDAN_E2E_OPENAI=1 APP_ENV=local DB_NAME=jidan_e2e_local ... python -m e2e.demo_scenario --start-server --ai live
```

### 브라우저 로그인 도구 (프론트 실연동 확인)

Google 로그인은 자동화할 수 없어, 실제 브라우저로 프론트 화면을 확인할 때는 앱과 분리된 별도 프로세스 `e2e/browser_login.py`로 세션 쿠키를 받는다. 앱에는 우회 경로가 없다.

```bash
APP_ENV=local DB_HOST=127.0.0.1 DB_PORT=3317 DB_NAME=jidan_local DB_USER=... DB_PASSWORD=... \
  python -m e2e.browser_login --port 8120 --frontend http://127.0.0.1:5173 --key-file /tmp/login-key
# 브라우저: http://127.0.0.1:8120/login?as=owner&key=$(cat /tmp/login-key)   (owner|jisu|minjun|seoyeon|doyoon|new:<label>[&email=..][&next=..])
```

- OAuth callback이 Google 응답 뒤에 하는 일을 같은 함수로 한다: 브라우저의 기존 회원·가입 세션 폐기, 기존 계정이면 `create_session`, 없으면 `create_registration_session`, commit 뒤 `set_session_cookie`/`set_registration_cookie`(같은 이름·Path·HttpOnly·SameSite·Secure), frontend의 `/__auth/session`·`/__auth/signup`으로 302. 쿠키는 호스트 단위(Domain 없음)라 같은 127.0.0.1의 다른 포트(frontend와 그 `/api` 프록시)에 전달된다.
- 계정: 데모 시드 계정(`python -m app.demo_seed` 먼저)과 `new:<label>`(Google `sub` `e2e:browser:<label>`, 가입 전에는 가입 세션, 가입 후에는 회원 세션). `next`는 `--frontend` origin 안에서만 허용한다.
- **범위와 위험**: 개발자 자기 PC의 로컬 스택 전용이다. 데모 시드 계정과 `e2e:browser:` 신원만 로그인시키며, 키를 아는 사람은 그 계정으로 세션을 만들 수 있으므로 키 파일·URL을 공유하지 않는다. 공용 dev 서버·운영에서는 쓰지 않는다(아래 가드로 거부).
- 로컬 전용 강제: `APP_ENV`가 정확히 `local`일 때만 실행한다(미설정·`dev`·`staging`·`production` 거부, `COOKIE_SECURE=false`인 dev도 거부). DB 이름은 `_local`·`_test`로 끝나야 한다(`_dev`·`_demo` 거부). 데모 시드보다 좁다.
- 로그인 CSRF 완화: `/`와 `/login`은 시작할 때 만든 무작위 `key`(`secrets.token_urlsafe(24)`)를 query로 요구한다(상수 시간 비교, 정확히 1개). 키는 `--key-file`(권한 0600)에 쓰거나 한 번 출력한다. 다른 웹 페이지가 링크·이미지·리다이렉트로 `/login`을 부르게 해도 키를 모르므로 403이며, 세션 발급·폐기·쿠키가 없다(`tests/test_browser_login.py::test_a_link_from_another_page_cannot_sign_in_or_out`).
- 가드: `COOKIE_SECURE=true` 거부(http에서 저장되지 않음), 127.0.0.1에만 바인딩, `--frontend`는 `http://127.0.0.1:<포트>` 또는 `http://<이름>.localhost:<포트>`만. 쿠키는 포트와 무관한 호스트 단위라, 여러 frontend를 한 브라우저에서 동시에 확인할 때는 `<이름>.localhost`(브라우저가 loopback으로 해석)를 하나씩 써야 서로의 세션을 덮어쓰지 않는다. 로그인 페이지도 frontend와 같은 호스트 이름으로 열어야 한다(다르면 400). `--cleanup`은 `e2e.demo_scenario --cleanup`과 같은 `e2e:` 계정만 지운다. 검증은 `tests/test_browser_login.py`.

### 로컬 Docker 이미지 검증

```bash
cd back-end
docker build -f ../deploy/backend/Dockerfile --target runtime -t jidan-backend:local \
  --build-arg DOCS_REVISION=$(git rev-parse HEAD) .   # 문서 revision은 40자 SHA 또는 uncommitted
docker run --rm -p 127.0.0.1:18080:8000 -e APP_ENV=dev -e DB_HOST=host.docker.internal -e DB_PORT=3317 \
  -e DB_NAME=jidan_local -e DB_USER=... -e DB_PASSWORD=... -e ALLOWED_ORIGINS=http://localhost:5173 \
  -e ADMIN_PASSWORD_HASH="$(python -m app.admin_password)" jidan-backend:local   # dev는 hash가 없으면 health 503
curl http://127.0.0.1:18080/api/health        # {"status":"ok","environment":"dev","database":"ok"}
curl -I http://127.0.0.1:18080/api/swagger/   # 200
```

runtime 이미지에는 `tests/`와 `e2e/`가 들어가지 않는다(`runtime-source` 단계에서 제외, CI의 test 단계는 그대로 tests를 쓴다). 이미지로 시연 E2E를 돌리려면 `-v "$PWD/e2e:/app/e2e:ro"`로 하네스만 마운트하고 `python -m e2e.serve --host 0.0.0.0 --port 8000`을 명령으로 띄운다. 환경 변수는 `e2e.demo_scenario.server_env(origin, smtp_port, "host.docker.internal")`이 만드는 값(`APP_ENV=local`, `ADMIN_PASSWORD_HASH`, `INVITATION_MAIL_KEY`, SMTP 설정 등)과 `DB_*`다. 호스트에서는 `E2E_ADMIN_PASSWORD=... python -m e2e.demo_scenario --port <포트> --smtp-port <SMTP_PORT> --smtp-listen 0.0.0.0`으로 실행한다.

## AI·STT·미디어 기반 (#119)

매뉴얼 인터뷰·초안·Q&A가 쓰는 AI 제공자(`app/ai`), DB 기반 비동기 작업 실행기(`app/tasks`), 미디어 저장·검증·보관(`app/media`)과 매뉴얼 미디어·전사 API 5개(`app/manual_media.py`)를 제공한다. 사용법·규칙·예시는 [AI·작업·미디어 기반 사용법](docs/ai-foundation.md)을 따른다.

- AI·STT 호출은 요청 트랜잭션 밖의 작업(`enqueue` → `execute` → `apply`)에서만 하며, 결과는 task ID·attempt·입력 revision·RUNNING을 확인한 뒤 한 번만 적용한다.
- 모든 테스트는 `FakeAiProvider`(네트워크 없음)와 `TASK_RUNNER_MODE=manual`로 돈다. 작업은 `app.tasks.drain()`으로 실행한다. 실제 OpenAI 테스트는 `JIDAN_RUN_OPENAI=1`과 `OPENAI_API_KEY`가 있을 때만 `@pytest.mark.openai`가 실행한다.
- 업로드 파일은 `MEDIA_ROOT`(기본 `back-end/.media`, git 제외)에 저장한다. 배포에서는 환경별 Docker volume을 `/var/lib/jidan/media`에 마운트해 `MEDIA_ROOT`로 쓴다([CI-CD](../deploy/CI-CD.md#백엔드-미디어-영속-저장소)). 배포 Nginx는 미디어 업로드 두 경로만 21m까지 받는다.

| 환경 변수 | 용도 |
| --- | --- |
| `AI_PROVIDER` | `openai`(기본) 또는 `fake`(production 금지) |
| `OPENAI_API_KEY` | OpenAI 키. 환경 변수로만 주입하며 로그·응답에 남기지 않음 |
| `OPENAI_MODEL` / `OPENAI_FALLBACK_MODEL` / `OPENAI_TRANSCRIBE_MODEL` | 기본 `gpt-6-luna` / 없음 / `gpt-transcribe` |
| `OPENAI_SERVICE_TIER` | 기본 `fast`; `auto/default/fast/priority` 허용. Responses 주 모델·fallback에 적용, STT 제외 |
| `OPENAI_REASONING_EFFORT`, `OPENAI_TIMEOUT_SECONDS`, `OPENAI_TRANSCRIBE_TIMEOUT_SECONDS` | 기본 `low`, 60, 120 |
| `MEDIA_ROOT` | 미디어 저장 디렉터리 |
| `INTERVIEW_GUIDANCE_RESPONSES` | `true`이면 인터뷰 응답에 질문 안내(`guidance`·`guidanceCards`·`lastAnsweredQuestion`)를 포함. 기본은 생략(프론트 계약 갱신 후 켬) |
| `TASK_RUNNER_MODE`, `TASK_RUNNER_WORKERS`, `TASK_RUNNER_POLL_SECONDS` | `background`(기본)/`manual`, 양의 정수(기본 2), 유한한 양수 초(기본 2). 잘못된 숫자 설정은 시작 시 거부. 실행기 스레드는 `BACKGROUND_JOBS=off`여도 뜨지 않음 |

## 점주 AI 인터뷰 (#120)

`app/interview/`가 인터뷰 세션·턴·답변·완료·재시도와 인텐트 검토(조회·확인·정정·재시도·사진), 최초 초안 생성까지 12개 operation을 제공한다. 질문 셋 v1(6개 인텐트, 고정 uuid5)은 마이그레이션 `0040`이 seed한다.

- 질문 하나 → 답변 하나 → Jev 판단. 부족하면 같은 인텐트의 추가 질문을 depth 1~5에서 하나씩 만들고, depth 5에서도 부족하면 `NEEDS_DETAIL`로 표시하고 확인 없이 다음 인텐트로 간다. 검토 확인은 진행·생성 조건이 아니다.
- 전역 잠금 순서는 `store_manuals` → 세션 → 버전 → 검토다(#118의 `store_manuals` → 정정 → 버전과 같은 순서). 초안을 바꾸는 경로(시작·completion·세션 재시도·초안 생성 apply/fail)는 `store_manuals`를 가장 먼저 잡고 READ COMMITTED로 시작한다. 단, 시작 경로만 매장 첫 시작을 직렬화하려고 `stores` → `store_manuals` 순서로 잡는다. `store_manuals`를 쥔 채 `stores`를 잠그지 말 것. 인터뷰만 바꾸는 경로(답변·검토·질문/Jev/요약 작업)는 세션 행 `FOR UPDATE`를 첫 문장으로 둔다. 세션 revision은 질문 진행, 검토 revision은 검토별로 독립이다.
- 새 인터뷰 시작은 `store_manuals` 잠금 아래에서 기존 초안을 확인하고 `app.manual_drafts.ensure_no_running_correction`으로 정정 중 409를 먼저 판정한다. 초안 내용은 `app.manual_editing.prepare_content`/`write_initial_content`로 기록한다(revision 1 유지).
- 근무 구조 외 인텐트의 요약 작업은 AI 호출 직전에 다른 검토의 최신 READY 근무조를 읽는다. 근무 구조 최초 요약이 아직 생성 중이면 `app.tasks.TaskDeferred`로 시도 횟수를 쓰지 않고 기다린다(근무 구조 요약 ERROR는 막지 않음).
- 질문 안내는 생성기의 `guidance`와 `guidanceCards`를 질문과 함께 저장한다. LIST와 PROGRESS_CHECKLIST만 질문 생성에 사용하고, 같은 인텐트의 저장된 항목 ID는 표현·순서·카드 재구성이 달라도 유지한다. 새 항목 ID는 서버가 부여한다. 카드만 잘못되면 그 카드만 제외하고 질문은 유지한다. 저장된 이해 요약의 선택 사진 추천은 별도 `after_success`에서 다음 실제 미답변 질문에만 붙이며 필수 진행을 막지 않는다.
- 안내 송출은 `INTERVIEW_GUIDANCE_RESPONSES=on|off`로 설정한다. 답변 접수 때 공개된 안내는 기존 ANSWER 행의 nullable 안내 필드에 고정한다. `lastAnsweredQuestion`은 원 QUESTION 기본 필드와 이 스냅샷으로 복원하여 설정 변경·오류·재시도에도 접수 당시 값을 유지한다. 배포된 `0042`·`0043`와 DB 모델은 변경하지 않는다.
- 질문 생성 실패는 재시도 후 고정 문구로 fallback하므로 세션을 멈추지 않는다. Jev·초안 생성 실패만 세션 `ERROR`이고 요약·정정 실패는 해당 검토만 `ERROR`다. 재시도는 실패한 작업의 저장된 입력을 그대로 쓴다.
- completion은 모든 검토 READY·revision·근무조 참조를 확인하고 `generation_input_snapshot`에 고정한 뒤 `DRAFT_GENERATION`을 예약한다. 적용 시 같은 근무조·섹션 ID로 초안 행을 만들고 사진을 섹션 ID로 다시 붙이며, 미확정 정보마다 같은 ID의 issue(유래 인텐트 연결)와 `NEEDS_DETAIL` 인텐트마다 대상 없는 issue를 만든다. 초안 편집·정정·게시는 #118 범위다.
- 테스트 도우미는 `tests/interview_factories.py`(`ensure_question_set`, `InterviewDriver`)에 있다. MySQL 테스트는 테이블을 비우므로 seed를 다시 넣는다. 실키 스모크는 `tests/test_interview_live.py`(`@pytest.mark.openai`).

## 매뉴얼 초안·게시·근무자 열람 (#118)

- `app/manual_content.py`: 버전 조회(`store_manual(lock=)`, `active_draft`, `current_published_version`), 열람 권한(`require_manual_reader`: 소유 OWNER 또는 판정 시각에 유효한 접근의 WORKER. 유효 접근이 있는데 매장이 운영 중이 아니면(승인 상실·점주 비ACTIVE) 403 `STORE_APPROVAL_REQUIRED`, 접근이 없으면 404 `STORE_NOT_FOUND`), Q&A 근거용 `published_structure(db, version_id, *, store_id=None)`(PUBLISHED만, 아니면 None), 게시본 확인(`require_published_version`: 없음 404 `MANUAL_NOT_PUBLISHED`, 교체 409 `MANUAL_VERSION_CHANGED`), 직렬화(`content_body` → API `ManualContent`, `structure_snapshot` → AI `StructureSnapshot`). Q&A(#121)도 이 함수로 게시본 근거를 읽는다.
- `app/manual_editing.py`: 스키마 밖 내용 규칙 검증(`prepare_content`)과 교체(`replace_content`), 최초 생성 내용 기록(`write_initial_content`, revision 유지·#120 DRAFT_GENERATION용), 부족 항목 확인(`acknowledge`). `app/manual_drafts.py`: 상태·초안·편집·미리보기·확인·게시 API. `app/manual_corrections.py`: 초안 정정 API와 `DRAFT_CORRECTION` 작업. `app/manual_published.py`: 근무자 게시본 목록·섹션.
- 모든 초안 변경은 `lock_current_draft`로 시작한다: 소유·승인 → `store_manuals` 행 잠금 → 현재 DRAFT id(`MANUAL_VERSION_CONFLICT`) → revision(`REVISION_CONFLICT`) → READY·인터뷰 완료(`MANUAL_STATE_CONFLICT`, 게시는 `MANUAL_NOT_READY`) → RUNNING 정정 없음(`MANUAL_CORRECTION_IN_PROGRESS`). MySQL은 READ COMMITTED로 시작해 잠금 뒤 읽기가 최신 commit을 본다. 그 밖의 경로에서 잠금 뒤 초안을 찾을 때는 `active_draft(refresh=True)`(잠금 읽기)를 쓴다. 새 인터뷰로 초안을 교체하는 경로도 같은 잠금 안에서 `manual_drafts.ensure_no_running_correction`을 호출해야 한다.
- 부족 항목은 `manual_review_issues` 행이며 missingInformation과 같은 ID다. 확인은 현재 `content_revision`에 묶인다(`manual_issue_acknowledgements`, 이력은 삭제하지 않음).
- 게시는 확인 → PUBLISHED → 게시 포인터 교체 → `MANUAL_PUBLISHED` 알림(게시 시각에 유효한 접근의 ACTIVE 근무자, event_key=게시 버전 ID)을 한 트랜잭션으로 저장한다. 게시본 행은 이후 바뀌지 않는다.
- 정정 오류의 `retryable`은 저장 컬럼 없이 `AI_PROCESSING_FAILED`일 때만 true로 투영한다.

| 대상 | 상태 | 요청·사건 | 결과 |
| --- | --- | --- | --- |
| 초안 | READY rev r | 실질 내용 변경(편집·정정 성공) | rev r+1, content_revision+1, 부족 항목 동기화, 확인 무효화 |
| 초안 | READY rev r | 같은 내용 편집·정정 무변경 | 변화 없음(정정 resultRevision = r) |
| 초안 | READY rev r | 새 확인·메모 변경 | rev r+1(content_revision 유지) |
| 초안 | READY rev r | 같은 메모 재확인 | 변화 없음 |
| 초안 | READY rev r | 게시(OPEN 항목 전부 확인) | PUBLISHED, 포인터 교체, 알림 |
| 초안 | NOT_STARTED/RUNNING/ERROR | 편집·확인·정정 / 게시 | 409 `MANUAL_STATE_CONFLICT` / `MANUAL_NOT_READY` |
| 정정 | (신규) | 접수 | RUNNING attempt 1, 초안 변경 차단 |
| 정정 | RUNNING | APPLIED / NO_CHANGE | SUCCEEDED(resultRevision r+1 / r) |
| 정정 | RUNNING | CLARIFICATION_REQUIRED·REFERENCE_CONFLICT | ERROR(재시도 불가), 내용 보존 |
| 정정 | RUNNING | AI 실패(자동 재시도 소진)·규칙 위반 출력 | ERROR `AI_PROCESSING_FAILED`(재시도 가능) |
| 정정 | RUNNING | 초안 교체·revision 이동(방어) | ERROR `MANUAL_VERSION_CONFLICT`·`REVISION_CONFLICT` |
| 정정 | ERROR(재시도 가능, 최신, base = 현재 rev) | 재시도 | RUNNING, attempt+1, 새 task ID(이전 작업 결과는 버림) |

## 근무자 AI 업무 질문 (#121)

`app/qa/`가 대화(시작·목록·복원), 질문·답변·재시도, 질문 사진·음성 업로드와 전사를 제공한다. 계약은 [업무 질문](docs/qa-conversation-design.md)·[질문 미디어](docs/qa-media-design.md)다.

- 모든 요청은 ACTIVE WORKER 세션과 `app.qa.access.qa_store`(유효 접근, 매장 APPROVED·점주 ACTIVE)를 매번 확인한다. 접근 없음·다른 근무자·다른 매장·없는 대화는 같은 404 `RESOURCE_NOT_FOUND`, 유효 접근이 있는데 매장 승인이 없으면 403 `STORE_APPROVAL_REQUIRED`다.
- 질문은 접수 시점의 게시 버전에 고정되고, `QA_ANSWER` 작업이 그 불변 버전(`app.manual_content.structure_snapshot`)만 근거로 답한다. `ANSWERED`는 인용 1~10개, 발췌는 서버가 인용 단계 원문으로 만든다. 대화당 RUNNING 질문은 하나(409 `QA_BUSY`)이며, 적용 시 접근을 다시 확인해 그사이 접근을 잃은 근무자의 답은 저장하지 않는다.
- 질문 사진은 7일, 녹음은 24시간(전사 종료 후 24시간) 보관하고, 만료된 파일은 410 `QA_MEDIA_EXPIRED`다. 녹음·전사·`TRANSCRIPTION` 작업은 점주 흐름(`app.media.transcription`)과 공유한다.

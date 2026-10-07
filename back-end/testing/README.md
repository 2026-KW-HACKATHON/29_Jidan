# 수동 API·DB와 자동 HTTP E2E

운영·개발 서버와 분리된 로컬 환경이다. Docker Desktop 또는 Docker Engine의 Compose v2가 필요하다. 호스트 Python·MySQL·Google 자격 증명은 필요하지 않다. 모든 명령은 저장소 루트에서 실행한다.

## 수동 환경

```bash
docker compose -p jidan-sandbox -f back-end/testing/compose.yml --profile manual up --build -d --wait
```

- 요청 화면: <http://127.0.0.1:18000/sandbox>
- 실제 구현 Swagger: <http://127.0.0.1:18000/docs>
- API·DB 상태: <http://127.0.0.1:18000/api/health>

`근무자로 로그인` 또는 `점주로 로그인`을 누른 다음 API를 호출한다. 화면에서 JSON을 수정할 수 있으며 현재 쿠키에 맞는 CSRF 토큰을 자동으로 붙인다. `새 가입 세션 만들기`를 선택하면 매번 다른 검증된 Google 신원 fixture가 만들어지고 실제 근무자 가입 API를 호출할 수 있다. `이전 Idempotency-Key 재사용`으로 중복 요청과 body 변경을 검사한다.

테스트 계정 로그인은 **Google 인증을 대신하는 fixture**다. 이후 쿠키·세션·CSRF·가입·프로필 요청은 실제 구현을 사용한다. 점주 fixture는 계정만 생성하며 승인된 매장은 생성하지 않는다. 점주 가입의 주소 검증은 Kakao 외부 서비스가 필요하여 이 환경의 자동 E2E 대상에 포함하지 않았다.

`/docs`는 현재 서버에 실제 등록된 endpoint만 보여 준다. 개발 서버의 `/api/swagger/`는 앞으로 구현할 API까지 포함하는 설계 명세이므로 두 문서의 범위가 다르다.

이 환경의 API는 호스트 `127.0.0.1`에만 공개한다. MySQL은 호스트 포트를 공개하지 않는다. 18000 포트가 사용 중이면 다른 포트로 시작한다. 사용한 환경 변수는 이후 Compose 명령에도 유지한다.

```bash
export JIDAN_SANDBOX_PORT=18001
docker compose -p jidan-sandbox -f back-end/testing/compose.yml --profile manual up --build -d --wait
# http://127.0.0.1:18001/sandbox
```

쿠키의 호스트가 일치하도록 `localhost` 대신 `127.0.0.1`을 일관되게 사용한다. Swagger의 변경 요청에는 `/api/auth/csrf`에서 얻은 `X-CSRF-Token`을 입력한다. sandbox 요청 화면은 이를 자동 처리한다.

### DB를 직접 확인하기

```bash
docker compose -p jidan-sandbox -f back-end/testing/compose.yml exec mysql \
  sh -c 'MYSQL_PWD=$MYSQL_PASSWORD mysql --default-character-set=utf8mb4 -u "$MYSQL_USER" "$MYSQL_DATABASE"'
```

```sql
SHOW TABLES;
SELECT id, role, name, phone_number, updated_at FROM users;
SELECT * FROM worker_profiles;
SELECT * FROM worker_careers ORDER BY worker_id, sort_order;
SELECT * FROM availability_rules ORDER BY worker_id, sort_order;
SELECT * FROM availability_days;
SELECT user_id, expires_at, revoked_at FROM auth_sessions;
```

API를 호출한 뒤 위 조회로 commit 결과를 확인한다. 고정 자격 증명은 이 Compose 프로젝트의 DB에만 사용하며 운영용 자격 증명을 넣지 않는다.

중지 후에도 데이터는 보존된다.

```bash
docker compose -p jidan-sandbox -f back-end/testing/compose.yml --profile manual down
```

**수동 데이터까지 초기화할 때만** 아래 명령을 사용한다.

```bash
docker compose -p jidan-sandbox -f back-end/testing/compose.yml --profile manual down --volumes
```

sandbox 로그인은 `testing.sandbox:create_app`에서만 등록한다. `APP_ENV=local`, `DB_HOST=mysql`, `DB_NAME=jidan_sandbox`, `COOKIE_SECURE=false`가 아니면 시작을 거부한다. `app.main`은 이 도구를 import하지 않으며 production runtime 이미지에서 `tests/`·`testing/`·`e2e/`를 제외한다.

## 자동 검증

```bash
back-end/testing/run-e2e.sh
```

매번 고유한 `jidan-e2e-*` Compose 프로젝트와 `jidan_e2e_test` DB를 새로 만든다. MySQL은 tmpfs를 사용하며 API와 DB의 호스트 포트를 공개하지 않는다. 수동 sandbox의 DB와 볼륨은 사용하지 않는다.

실행 순서는 다음과 같다.

1. 실제 MySQL 8.4를 시작한다.
2. `checks` 컨테이너에서 Ruff, 도구 테스트, SQLite·MySQL을 포함한 전체 Python 테스트를 실행한다. 마이그레이션·스키마 초기화는 이 단계에서 마친다. `JIDAN_REQUIRE_MYSQL=1`로 MySQL 환경 누락을 거부한다.
3. 앞 검증이 성공한 뒤 Uvicorn으로 **실제 `app.main:app`**을 실행하고 Alembic head와 DB 연결을 확인한다. 스키마 초기화와 API 백그라운드 작업이 동시에 실행되지 않는다.
4. 실제 TCP HTTP 요청으로 가입·세션·프로필 E2E를 실행한다. 요청 서버의 의존성과 handler는 교체하지 않는다. 가입 요청의 검증된 Google 신원과 점주 계정·매장·추가 HTTP 클라이언트 세션의 초기 상태는 DB fixture로 생성한다.
5. 각 테스트에서 별도 DB 연결과 API 재조회로 저장·거부·폐기 결과를 확인하고 테스트 프로젝트를 정리한다.

각 단계의 JUnit XML을 별도로 검사한다. 테스트가 0개이거나 실패·오류·skip/xfail이 있거나 리포트가 없거나 불완전하면 성공으로 처리하지 않는다.
기존 MySQL 테스트 중에는 스키마를 초기화하는 테스트가 있다. 자동 프로젝트의 DB에서만 실행하며, 직접 `DB_*`를 개발·운영 DB에 지정하여 실행하지 않는다. HTTP E2E fixture는 정확히 `jidan_e2e_test` DB와 로컬 HTTP 서버만 허용한다.

실패 시 비정상 종료하며 `.local/test-results/jidan-e2e-*/`에 `tooling.xml`, `python.xml`, `http-e2e.xml`, `services.log`를 보관한다. 앞 단계가 실패하면 후속 XML은 없을 수 있다. 리포트는 Git에 추가하지 않는다. 정리가 실패해도 성공으로 처리하지 않는다. Ctrl+C/TERM은 정리를 시도하며, 강제 종료로 남은 프로젝트는 출력된 이름으로 직접 정리한다.

```bash
docker compose -p <남은-jidan-e2e-프로젝트> -f back-end/testing/compose.yml --profile e2e down --volumes
```

GitHub Actions `backend CI/CD`는 전체 Python 및 HTTP·MySQL E2E를 실행하지 않고 정적 검사와 이미지 빌드·배포를 진행한다. 배포 후 에이전트가 실제 서버를 검증하고, 격리 회귀가 필요하면 `back-end/testing/run-e2e.sh` 또는 수동 `Backend HTTP E2E`를 실행한다. 수동 워크플로우의 XML과 로그는 artifact로 7일 보관한다. 이 하네스의 DB 초기화·실패 주입은 테스트 DB에만 적용하며 배포 서버 DB에 실행하지 않는다.

## 검증 범위

독립 재점검 후 로컬 전체 실행에서 도구 테스트 43개, 전체 Python 테스트 1,331개, 실제 HTTP E2E 148개가 통과했으며 실패·오류·skip은 모두 0개였다. 전체 Python에는 `mysql` 표시 테스트 502개가 포함된다. 이 표시 중 등록 트랜잭션 테스트의 2개 매개변수는 SQLite이므로 표시 개수를 실제 MySQL 실행 수로 해석하지 않는다. 실행 결과는 JUnit 및 pytest 요약으로 확인하며 테스트 수는 구현 추가에 따라 달라질 수 있다.

| 범위 | 실제 HTTP + MySQL 검증 | 남은 범위 |
| --- | --- | --- |
| Health | DB 연결·환경·migration head | 운영 프록시·배포 서버 장애 |
| 근무자 가입 | NEW/EXPERIENCED aggregate·쿠키 전환, 실제 동시 가입·멱등 replay/PROCESSING/key 충돌·DB 실패 롤백 및 재시도 | 외부 Google 인증 |
| Auth 세션·CSRF·로그아웃 | 회원/가입 세션 우선순위·CSRF 회전/격리·유휴 만료·정지/폐기·다중 HTTP 클라이언트 logout·점주 혼합 승인 매장의 권한 | Google callback/logout 경합의 외부 브라우저 흐름 |
| 프로필 기본 정보 | 저장·no-op·UTC 갱신·입력/날짜 경계·모든 경로의 권한/CSRF/Origin 거부·두 사용자 격리·실제 동시 저장 | 실제 프론트엔드 폼 |
| 경력 | 목록 상한·전체 교체/삭제·순서·오류 시 전체 보존·실제 DB 부분 실패 롤백·경합 중 초기 목록 복원 | UI 연동 |
| 가용 시간 | 그룹 상한·전체 교체/자식 제거·주 경계/24시간·중복/중첩 거부·실제 DB 실패 롤백·경합 중 초기 목록 복원 | UI 연동 |
| 점주 가입·Google OAuth | 기존 Python/MySQL 테스트만 실행 | 외부 Kakao·Google HTTP와 브라우저 E2E |
| 매장·초대·공고·지원·근무 요청·매뉴얼·Q&A·알림 | 로컬 백엔드는 구현됨. 기존 33단계 시연 하네스가 실제 HTTP로 검증하며 이 Auth/Profile 148개 suite와 범위를 구분 | 각 구현 PR에 실제 시나리오 추가 |

명세 검사 통과는 endpoint의 구현·DB 저장·전체 사용자 흐름의 검증을 뜻하지 않는다. 이 suite도 프론트엔드부터 외부 제공자까지 모두 포함하는 E2E는 아니다. 새 endpoint는 HTTP 성공, DB commit, API 재조회, 권한/입력 실패 시 데이터 보존, 재시도·동시성 등 해당 도메인 시나리오를 추가한다.


## 독립 재점검 반영

테스트 작성에 참여하지 않은 Auth·Profile·도구 담당 에이전트 3명이 계약·구현·테스트·실행 결과를 직접 대조했다. 세션 전환/폐기, 두 사용자 격리, 입력 경계, 저장 필드/행 교체, 실제 동시 요청, DB 오류 롤백의 누락을 보완하고 다시 검토했다. 도구 검사에서는 숨겨진 sandbox 경로, 실제 factory 실행, 누락/skip 리포트의 성공 처리, runner 단독 종료 신호 전달, 스키마 초기화 단계의 충돌을 보완했다.

DB 실패는 폐기되는 E2E DB에만 조건부 trigger로 주입하며 `finally`에서 제거한다. 이 DB만 `log-bin-trust-function-creators=ON`을 사용한다. 동시성은 부모 행 잠금 중 두 실제 HTTP 요청의 도착/대기를 관측한다. 일부 도착 기록은 MEMORY 테이블을 사용하고, 오래된 읽기 snapshot의 no-op 회귀는 최근 활동 세션과 격리 DB root 계정의 `performance_schema.data_locks` 조회로 확인한다. root 계정은 잠금 관측에만 사용하며 서버 요청은 일반 DB 계정으로 처리한다.

점주 신원·매장·추가 HTTP 클라이언트 세션의 초기 상태는 DB fixture로 만든다. 따라서 점주 가입·Google 재로그인을 실제 외부 서비스로 검증한 것으로 해석하지 않는다. 외부 Google/Kakao, 배포 프록시·production 이미지의 실행, 프론트엔드 브라우저 흐름은 이 로컬 suite의 미검증 범위이며 각 연동 작업에서 추가 검증한다.


## PR #154 원형 흡수와 로컬 보완

원 작성자 `leehyowon14`의 PR #154 head `c48e48d` 커밋 이력을 기능 브랜치에 보존했다. 수동 sandbox UI·factory·로그인/가입 fixture·guard 시험, Compose manual/e2e 분리, runner 단계/중단/정리, JUnit 기본 strict gate와 백엔드 AGENTS 규칙을 채택한다. 제품 `app.main`에는 sandbox 경로를 추가하지 않았다.

로컬 보완은 다음으로 한정한다.

- 원본 HTTP 파일 10개 중 9개는 byte 동일하다. 프로필 시험 1개의 2개 저장 assertion만 로컬의 자식 row 재사용에 맞췄으며 내용/순서/옛 데이터 제거 검사와 기존 응답 보존 검사는 유지한다.
- 테스트 Dockerfile은 원본의 명시 COPY 방식에 docs/evals/README를 추가한다. 테스트 build context에는 testing/e2e가 필요하므로 runtime-source가 이 디렉터리를 제거하는 기존 운영 이미지 격리를 유지한다.
- 전체 Python report의 `--allow-skipped`는 같은 classname·case의 성공한 MySQL 대응이 있는 SQLite skip만 허용한다. xfail·미검증 MySQL·예상 밖 skip은 실패다. 도구/HTTP는 원본대로 모든 skip을 거부한다. 유료 OpenAI는 명시적으로 제외하고 대용량 MySQL은 격리 회귀 실행에서 확인한다.
- 고유 실행의 이미지도 cleanup하며 수동 Actions 실행의 timeout은 60분이다. 스키마 초기화 검사 뒤 실제 API를 띄우는 원본 순서를 유지한다.
- 실제 동시 첫 점주 fixture 로그인에서 MySQL deadlock을 재현해, sandbox에서만 1062/1205/1213에 전체 rollback 후 최대 3회 재시도한다. 원래 계정 생성·재사용·commit 뒤 쿠키 흐름은 그대로이며 다른 오류를 숨기지 않는다.

### sandbox 실제 HTTP/MySQL 재검증

아래 검사는 새 폐기용 Compose 프로젝트에만 실행한다. 기존 계정·프로필·회원/가입 세션이 있으면 검사기가 쓰기 전에 거부한다. 기존 `jidan-sandbox` 프로젝트나 공유 데모 DB를 지우지 않는다.

```bash
export JIDAN_SANDBOX_PORT=18001
review_project=jidan-sandbox-check-$(date +%s)
docker compose -p "$review_project" -f back-end/testing/compose.yml --profile manual up --build -d --wait
docker compose -p "$review_project" -f back-end/testing/compose.yml --profile manual exec -T api python -m testing.check_sandbox
# 위에서 새로 만든 폐기용 프로젝트만 정리한다.
docker compose -p "$review_project" -f back-end/testing/compose.yml --profile manual down --volumes --rmi local
```

9단계는 실제 HTTP/독립 MySQL 연결로 화면·Swagger·DB health, Origin/역할 거부 시 행 보존, worker/owner 각각 첫 로그인 4건 동시 처리, fixture 재사용·기본 프로필/가용 시간 저장, 점주의 근무자 프로필 접근 거부, 가입/회원 쿠키 전환, 실제 근무자 가입과 프로필 저장·API 재조회를 검사한다. Google 신원은 sandbox fixture이며 유료 AI와 외부 Google/Kakao는 호출하지 않는다. 실제 브라우저 조작은 별도 범위다.

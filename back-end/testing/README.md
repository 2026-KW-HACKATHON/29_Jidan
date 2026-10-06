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

sandbox 로그인은 `testing.sandbox:create_app`에서만 등록한다. `APP_ENV=local`, `DB_HOST=mysql`, `DB_NAME=jidan_sandbox`, `COOKIE_SECURE=false`가 아니면 시작을 거부한다. `app.main`은 이 도구를 import하지 않으며 production Docker 빌드 컨텍스트에서 `testing/`과 `e2e/`를 제외한다.

## 자동 검증

```bash
back-end/testing/run-e2e.sh
```

매번 고유한 `jidan-e2e-*` Compose 프로젝트와 `jidan_e2e_test` DB를 새로 만든다. MySQL은 tmpfs를 사용하며 API와 DB의 호스트 포트를 공개하지 않는다. 수동 sandbox의 DB와 볼륨은 사용하지 않는다.

실행 순서는 다음과 같다.

1. 실제 MySQL 8.4를 시작하고 Alembic으로 head까지 마이그레이션한다.
2. Uvicorn으로 **실제 `app.main:app`**을 실행하고 DB 연결을 확인한다.
3. Ruff와 도구 테스트, 기존 `mysql` 표시 테스트를 실행한다. `JIDAN_REQUIRE_MYSQL=1`로 MySQL 테스트의 환경 누락을 성공으로 처리하지 않는다.
4. 실제 TCP HTTP 요청으로 가입·세션·프로필 E2E를 실행한다. 요청 서버의 의존성과 handler는 교체하지 않는다. 검증된 Google 신원만 DB fixture로 생성한다.
5. 별도 DB 연결과 API 재조회로 저장·거부·폐기 결과를 확인하고 테스트 프로젝트를 정리한다.

기존 MySQL 테스트 중에는 스키마를 초기화하는 테스트가 있다. 자동 프로젝트의 DB에서만 실행하며, 직접 `DB_*`를 개발·운영 DB에 지정하여 실행하지 않는다. HTTP E2E fixture는 정확히 `jidan_e2e_test` DB와 로컬 HTTP 서버만 허용한다.

실패 시 비정상 종료하며 `.local/test-results/jidan-e2e-*/`에 `tooling.xml`, `mysql.xml`, `http-e2e.xml`, `services.log`를 보관한다. 앞 단계가 실패하면 후속 XML은 없을 수 있다. 리포트는 Git에 추가하지 않는다. 정리가 실패해도 성공으로 처리하지 않는다. Ctrl+C/TERM은 정리를 시도하며, 강제 종료로 남은 프로젝트는 출력된 이름으로 직접 정리한다.

```bash
docker compose -p <남은-jidan-e2e-프로젝트> -f back-end/testing/compose.yml --profile e2e down --volumes
```

GitHub Actions `backend CI/CD`는 GitHub-hosted `Backend HTTP E2E` 성공 후 기존 이미지 빌드·배포 단계를 진행한다. XML과 로그는 artifact로 7일 보관한다. `Backend HTTP E2E`를 수동 실행해 독립 검증할 수도 있다. 기존 배포 서버와 배포 방식은 이 변경에서 수정하지 않았다.

## 검증 범위

2026-10-06 기준 도구 테스트 20개, 실제 MySQL 표시 테스트 502개, HTTP E2E 41개가 로컬에서 통과했으며 skip은 0개였다. 테스트 수는 구현 추가에 따라 달라질 수 있다.

| 범위 | 실제 HTTP + MySQL 검증 | 남은 범위 |
| --- | --- | --- |
| Health | DB 연결·환경·migration head | 운영 프록시·배포 서버 장애 |
| 근무자 가입 | 계정·프로필·가용 시간·세션 저장, 멱등 재시도, key 충돌, 입력/Origin/CSRF 거부 후 DB 보존 | 외부 Google 인증 |
| Auth 세션·CSRF·로그아웃 | 쿠키 전환·조회·만료·정지·DB 세션 폐기·옛 쿠키 거부·반복 로그아웃 | Google callback/logout 경합의 외부 브라우저 흐름 |
| 프로필 기본 정보 | 저장·재조회·no-op·유효하지 않은 혼합 요청의 원자성·역할/CSRF/Origin 거부·동시 저장 | 실제 프론트엔드 폼 |
| 경력 | 전체 교체·빈 목록 삭제·순서·유효하지 않은 목록 전체 거부와 기존 행 보존 | UI 연동 |
| 가용 시간 | 전체 교체·이전 자식 행 제거·주 경계 야간/인접 시간·중복/중첩/30분 단위 거부와 기존 데이터 보존 | UI 연동 |
| 점주 가입·Google OAuth | 기존 Python/MySQL 테스트만 실행 | 외부 Kakao·Google HTTP와 브라우저 E2E |
| 매장·초대·공고·지원·근무 요청·매뉴얼·Q&A·알림 | 현재 기준 브랜치에 구현되지 않은 endpoint는 HTTP E2E 없음 | 각 구현 PR에 실제 시나리오 추가 |

명세 검사 통과는 endpoint의 구현·DB 저장·전체 사용자 흐름의 검증을 뜻하지 않는다. 이 suite도 프론트엔드부터 외부 제공자까지 모두 포함하는 E2E는 아니다. 새 endpoint는 HTTP 성공, DB commit, API 재조회, 권한/입력 실패 시 데이터 보존, 재시도·동시성 등 해당 도메인 시나리오를 추가한다.

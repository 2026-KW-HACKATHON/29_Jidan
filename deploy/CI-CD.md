# CI/CD 운영

## 실행 구조

- **GitHub-hosted**: `ubuntu-24.04-arm`에서 테스트와 ARM64 이미지 빌드, GHCR 업로드. QEMU 없이 배포 서버와 같은 아키텍처로 빌드한다.
- **RPi5**: `jidan-rpi5-deploy` self-hosted runner에서 이미지를 내려받아 Docker Compose 배포.
- 배포 서버는 소스를 빌드하지 않는다. 이미지는 커밋 SHA로 게시하고 실제 배포는 digest로 고정한다.
- FE와 BE는 독립적인 Compose 프로젝트로 배포한다. 한쪽 배포가 다른 쪽 이미지를 바꾸지 않는다.

| 브랜치 | 환경 | 도메인 | 배포 대상 |
| --- | --- | --- | --- |
| `front-end/dev` | `dev` | `dev-jidan.leehyowon14.dev` | FE |
| `back-end/dev` | `dev` | `dev-jidan.leehyowon14.dev` | BE |
| `main` | `production` | `jidan.leehyowon14.dev` | 변경된 FE/BE |

backend PR은 배포 설정·명세·정적 검사와 이미지 빌드만 수행한다. 전체 Python 및 HTTP·MySQL E2E는 기존 정책대로 수동 GitHub-hosted 워크플로우에서 실행한다. frontend 검사 흐름은 기존대로 유지한다. fork PR도 격리된 GitHub-hosted runner에서 검사하며 로그인·이미지 게시·배포를 수행하지 않는다. PR 갱신 시 이전 검사는 취소하고 push/CD는 취소하지 않는다. `feat/fix/hotfix → dev → main` 병합 규칙은 [BRANCHING.md](../BRANCHING.md)를 따른다.

두 앱은 별도로 배포되므로 `main`의 FE·BE 배포는 원자적이지 않다. API 변경은 기존 클라이언트와 호환되도록 준비한다.

## 앱 초기화 시 필요한 파일

애플리케이션 코드는 별도 작업에서 초기화한다. 해당 디렉터리가 없으면 워크플로우는 배포 로직 테스트만 수행하고 앱 빌드·배포를 생략했다고 실행 요약에 기록한다. 디렉터리가 생성된 이후에는 아래 계약이 충족되지 않으면 실패한다.

### React + Vite

`front-end/`에 다음을 준비한다.

- `package.json`, `package-lock.json`을 커밋한다. 빌드 환경은 Node.js 22다.
- `npm run lint`, `npm run test:ci`, `npm run build`를 제공한다.
- `test:ci`는 감시 모드 없이 종료해야 한다. Vitest를 사용한다면 `vitest run`으로 설정한다.
- Vite 빌드 결과는 `dist/`로 출력한다.
- API 기본 경로는 `/api`다. 빌드 시 `VITE_API_BASE_URL=/api`를 제공한다.
- `VITE_*` 값은 브라우저에 노출되므로 비밀 값을 넣지 않는다.
- 로컬 개발은 Vite의 `/api` 프록시를 `http://127.0.0.1:8000`에 연결한다.

운영 컨테이너는 Nginx의 8080 포트에서 정적 파일을 제공한다. SPA 경로는 `index.html`로 fallback하며, 없는 `/assets/*` 파일은 404를 반환한다.

### FastAPI

`back-end/`에 다음을 준비한다.

- `requirements.txt`: FastAPI, Uvicorn 및 런타임 의존성을 명시한다.
- `requirements-dev.txt`: Ruff, pytest 및 테스트 의존성을 명시한다.
- 재현성을 위해 의존성 버전을 고정한다. 빌드 환경은 Python 3.12다.
- 진입점은 `app/main.py`의 `app`이다. `python -m uvicorn app.main:app`으로 실행한다.
- CI에서는 `python -m ruff check .`와 명세 검사·이미지 빌드를 수행한다. 전체 `pytest`와 HTTP·MySQL E2E는 배포 후 에이전트가 별도 검증하고 결과를 PR에 기록한다.
- 인증 없이 `GET /api/health`가 200을 반환해야 한다. 앱 초기화 때 해당 endpoint와 테스트를 추가한다.
- DB 설정은 `DB_HOST`, `DB_PORT`, `DB_NAME`, `DB_USER`, `DB_PASSWORD` 환경 변수에서 읽는다.
- DB 대기 시간: 연결 3초, 행 잠금 대기 `DB_LOCK_WAIT_TIMEOUT_SECONDS`(기본 5초, 세션 `innodb_lock_wait_timeout`), 쿼리 읽기·쓰기 `DB_READ_TIMEOUT_SECONDS`(기본 15초). 둘 다 선택 값이며 `1 ≤ 잠금 대기 < 읽기 ≤ 55`(nginx `proxy_read_timeout 60s` 아래)를 어기거나 숫자가 아니면 앱이 시작하지 않는다(배포 헬스체크 실패 → 이전 릴리즈로 복구). 읽기 제한이 잠금 대기보다 짧으면 잠금 대기가 2013 연결 끊김으로 끝나 재시도·409 처리를 건너뛰고 500이 된다. `/api/health`는 별도 1개 연결 엔진에서 연결·읽기 3초로 `SELECT 1`만 하므로 컨테이너 헬스체크(2초 요청, 3초 제한)와 배포 `curl --max-time 10` 안에서 끝난다. 마이그레이션 세션의 `lock_wait_timeout = 15`(metadata lock)는 별개 설정이다.
- 환경 구분은 `APP_ENV=dev|production`을 사용한다.

### DB 마이그레이션

도구는 Alembic이며 리비전은 `back-end/migrations/versions/`에 있고 이미지에 함께 들어간다.

**dev 백엔드는 배포 시 자동 적용한다.** `deploy.sh`는 `dev/backend`일 때만 새 이미지 pull 직후, `pending` 기록과 컨테이너 교체 전에 새 릴리즈의 Compose 정의로 일회성 컨테이너를 실행한다.

```bash
docker compose run --rm --no-deps -T --name jidan-dev-backend-migrate-<릴리즈> backend python -m alembic upgrade head
```

- 같은 이미지·`runtime.env`·`shared-mysql_default` 네트워크를 쓰며 포트는 열지 않는다. 대상 DB는 `jidan_dev`다.
- 실패하면 배포를 즉시 중단한다. 실행 중인 이전 릴리즈와 `current`는 그대로 두고 새 컨테이너를 띄우지 않는다. 트랜잭션 DDL이 없는 MySQL 특성상 일부 리비전만 적용됐을 수 있으므로 `alembic current`와 실제 테이블을 확인해 정리한 뒤 다시 배포한다. 실패한 실행의 `migration.pending`도 남으므로(아래) 확인 후 함께 지운다.
- 마이그레이션 세션은 `lock_wait_timeout = 15`초를 쓴다. 이전 릴리즈나 운영자 세션의 열린 트랜잭션 때문에 DDL이 metadata lock을 기다리면, 그 뒤의 같은 테이블 조회도 모두 대기열에 묶인다. MySQL 기본값(1년) 대신 15초 뒤 실패시켜 실행 중인 릴리즈가 계속 응답하게 하고 배포를 중단한다. production 수동 적용에도 같은 설정이 적용된다.
- 실행 전에 릴리즈별 컨테이너 이름을 권한 `600`의 `<배포 루트>/migration.pending`에 원자적으로 기록한다(`migration.next` → `mv`). 러너가 SIGINT·SIGTERM·SIGKILL 등으로 끝나도 Docker는 일회성 컨테이너를 계속 실행한다. **실행 중인 migration은 어떤 경우에도 강제 종료하지 않는다.** MySQL DDL은 트랜잭션이 아니어서 중간에 끊으면 일부만 적용된 리비전 위에서 다음 upgrade가 돌 수 있기 때문이다. 중단 시 정리 단계는 기다리지도 지우지도 않고 기록을 남기며(종료 코드 130/143 유지), 이전 릴리즈는 그대로다.
- 다음 배포는 같은 `deploy.lock`을 잡은 뒤 이미지 pull과 다른 Compose 명령보다 먼저 기록된 컨테이너를 처리한다. `docker container inspect`로 상태를 보고, 실행 중이면 `docker wait`로 최대 `JIDAN_MIGRATION_WAIT_SECONDS`(기본 300초, 1~9999)까지 끝나기를 기다린다. 종료 코드 0이면(또는 생성만 되고 시작되지 않은 컨테이너면) 멈춘 컨테이너를 `docker rm`(강제 아님)으로 지우고 부재를 다시 조회한 뒤 기록을 정리하고 진행한다. 종료 코드가 0이 아니면 `Recorded migration failed`, 상한을 넘기면 `did not finish in time`으로 **기록을 유지하고 배포를 차단**한다. 두 마이그레이션이 같은 DB에 동시에 실행되지 않게 하기 위해서다. 이 복구는 컨테이너 교체용 `pending`보다 먼저, 별도로 처리한다.
- 기록 내용이 `jidan-dev-backend-migrate-<8자리 영숫자>`가 아니면 Docker를 호출하지 않고 배포를 멈춘다(관계없는 컨테이너 제거 방지). Docker 조회·대기·제거 실패 또는 제거 후 컨테이너가 남아 있으면 `migration.pending`을 유지하고 새 마이그레이션·배포를 차단한다(`MIGRATION RECOVERY FAILED`).
- 빈 조회는 생성 요청이 아직 처리 중일 수 있으므로 1초 간격으로 최대 10회 재조회한다. 끝까지 이름이 보이지 않아 생성·완료 여부가 불확실하면 기록을 지우지 않고 배포를 차단한다. `compose run --rm`이 성공을 반환해 실행 완료가 확인된 경우에만 기록을 직접 정리한다. 마이그레이션 명령이 실패한 경우도 같은 이유로 기록이 남는다.
- 기록이 남아 차단되면 운영자가 `docker ps -a --filter name=jidan-dev-backend-migrate`로 컨테이너 상태를 확인한다. 아직 실행 중이면 끝날 때까지 기다려 다시 배포한다(다음 배포가 다시 기다린다). 실패했거나 불확실하면 `alembic current`와 실제 테이블로 부분 DDL 적용 상태를 확인·정리하고, 멈춘 컨테이너를 지운 뒤 `rm <배포 루트>/migration.pending`으로 기록을 정리하고 다시 배포한다. 빈 조회만으로 기록을 삭제하지 않는다.
- 마이그레이션 출력은 Actions 로그에 남는다. 연결 정보는 `DB_*`에서 읽어 출력하지 않으며, 실패한 SQL의 바인딩 값도 출력하지 않는다(`hide_parameters=True`).

**production과 frontend는 자동 실행하지 않는다.** production은 dev에서 적용·검증된 리비전만 RPi5에서 수동으로 적용한다.

1. 새 리비전이 이전 버전 코드와 호환되면(아래 정책) 새 이미지 배포 전에 **배포할 새 이미지**로 일회성 컨테이너를 실행한다. 실행 중인 컨테이너의 `docker compose exec`는 이전 이미지에 들어 있는 리비전까지만 알아서 새 리비전을 적용하지 못한다.
   ```bash
   cd /home/ubuntu/apps/jidan/production/backend
   image='ghcr.io/2026-kw-hackathon/29_jidan-backend@sha256:<배포할 digest>'
   migrate() {
     docker run --rm --name jidan-production-backend-migrate --network shared-mysql_default \
       --env-file runtime.env "$image" python -m alembic "$@"
   }
   migrate current
   migrate upgrade head
   ```
   `runtime.env`의 `DB_*`를 컨테이너에만 주입한다. 자격 증명은 출력하거나 기록하지 않는다. 고정 이름은 두 마이그레이션의 동시 실행을 막는다.
2. 적용 뒤 `current`가 head 리비전이고 `GET /api/health`가 `database: ok`인지 확인한다.
3. 실패하면 일부만 적용될 수 있다. 새 배포를 진행하지 말고 `alembic current`와 실제 테이블을 확인해 수동으로 정리한다.

**expand-only 정책과 롤백 한계**: 이미지 롤백(배포 스크립트의 자동 복구, 커밋 revert 후 재배포)은 DB 스키마를 되돌리지 않는다. dev는 마이그레이션이 성공한 뒤 새 컨테이너 시작·헬스체크가 실패하면 이전 이미지가 새 스키마에서 다시 실행된다. 따라서 리비전은 이전 버전 코드와 호환되는 변경(테이블·nullable 또는 기본값 있는 컬럼·인덱스 추가)만 한다. 컬럼·테이블 삭제, 이름 변경, 타입 축소 같은 비호환 변경은 추가 → 코드 전환 → 제거의 별도 릴리즈로 나눈다. 스키마를 되돌려야 하면 이전 이미지로 복구하기 전에 `alembic downgrade -1`을 수동으로 실행하며, 데이터를 삭제하는 downgrade는 사전에 백업한 뒤에만 수행한다.

## RPi5 네트워크 및 DB

Cloudflare Tunnel → Nginx 80 → 환경별 localhost 포트로 전달한다. `/api`와 `/api/*`는 접두사를 제거하지 않고 BE로 전달하며, 나머지는 FE로 전달한다.

| 환경 | FE 포트 | BE 포트 | MySQL DB / 계정 |
| --- | --- | --- | --- |
| dev | `127.0.0.1:3020` | `127.0.0.1:3021` | `jidan_dev` |
| production | `127.0.0.1:3022` | `127.0.0.1:3023` | `jidan_production` |

- DB 컨테이너: 기존 `shared-mysql`.
- Docker 네트워크: 기존 `shared-mysql_default`.
- BE 내부 접속 주소: `shared-mysql:3306`.
- 각 계정은 해당 DB에만 권한을 가진다.
- 서버 환경 파일: `/home/ubuntu/apps/jidan/<환경>/backend/runtime.env` (권한 600).
- 환경 파일과 자격 증명은 Git이나 Actions 로그에 출력하지 않는다.

### 백엔드 runtime.env: 인증·Origin

FE는 같은 출처의 `/api`로 백엔드를 부르므로 브라우저가 보내는 `Origin`은 FE 도메인이다. 아래 값이 없거나 다르면 Google 로그인이 500 `INTERNAL_ERROR`, 모든 쓰기 요청(가입·로그아웃 포함)이 403 `CSRF_INVALID`가 된다. 값은 위 브랜치·환경 표의 도메인에서 정해지며 운영 값도 같은 규칙을 따른다.

| 이름 | dev | production |
| --- | --- | --- |
| `ALLOWED_ORIGINS` | `https://dev-jidan.leehyowon14.dev` | `https://jidan.leehyowon14.dev` |
| `FRONTEND_ORIGIN` | `https://dev-jidan.leehyowon14.dev` | `https://jidan.leehyowon14.dev` |
| `GOOGLE_REDIRECT_URI` | `https://dev-jidan.leehyowon14.dev/api/auth/google/callback` | `https://jidan.leehyowon14.dev/api/auth/google/callback` |
| `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET` | Google Cloud OAuth 클라이언트 값 | 같은 규칙 |

- `ALLOWED_ORIGINS`는 쉼표 구분이며 scheme·host·port가 정확히 같아야 한다. 경로·와일드카드는 쓰지 않는다. 비우면 모든 쓰기 요청이 거절된다.
- 세 주소는 ASCII여야 한다. 국제화 도메인은 punycode(`xn--…`)로 적는다. 비ASCII 값은 로그인 시작 전에 500으로 거절되고 `ALLOWED_ORIGINS`에서는 무시된다.
- `GOOGLE_REDIRECT_URI`는 Google Cloud 콘솔의 승인된 리디렉션 URI에 같은 값으로 등록한다.
- 로컬 개발(FE Vite 기본 `http://localhost:5173`, `/api`는 Vite proxy로 백엔드에 전달)에서는 `ALLOWED_ORIGINS`·`FRONTEND_ORIGIN`을 `http://localhost:5173`, `GOOGLE_REDIRECT_URI`를 `http://localhost:5173/api/auth/google/callback`으로 둔다. `http://127.0.0.1:5173`은 다른 Origin이므로 그 주소로 접속하면 함께 넣어야 한다.

### 백엔드 runtime.env: 초대 메일

초대 메일을 실제로 보내려면 백엔드 `runtime.env`에 아래 값을 둔다. 값이 형식에 맞지 않으면 앱이 시작하지 않으므로(배포 헬스체크 실패 → 이전 릴리즈로 복구) 배포 전에 확인한다. 세부 규칙은 [백엔드 README](../back-end/README.md#근무자-초대-108)를 따른다.

| 이름 | 예시·규칙 |
| --- | --- |
| `INVITATION_MAIL_BACKEND` | `smtp`. 비우면 dev·운영은 메일을 대기열에만 쌓고 보내지 않는다 |
| `INVITATION_MAIL_KEY` | outbox 암호화 Fernet 키. 교체는 `새키,이전키`로 새 키를 앞에 두고, 이전 키로 저장된 미발송 메일이 처리된 뒤 이전 키를 뺀다. 목록에서 빠진 키의 메일은 FAILED가 된다 |
| `FRONTEND_ORIGIN` | 초대 링크의 frontend origin(인증 설정과 같은 값) |
| `SMTP_HOST`, `SMTP_PORT` | 메일 제공자의 SMTP 주소·포트 |
| `SMTP_SECURITY` | `starttls`(587) 또는 `ssl`(465). `none`은 local 전용이라 dev·운영에서는 시작 실패 |
| `SMTP_USERNAME`, `SMTP_PASSWORD` | SMTP AUTH 계정(앱 비밀번호 권장). 둘 다 설정 |
| `SMTP_TIMEOUT_SECONDS` | 유한한 1~60, 기본 10. NaN·무한대는 시작 시 거부 |
| `MAIL_FROM` | 제공자에서 인증한 발신 주소 |

비밀번호·키는 `runtime.env`(권한 600)에만 두고 출력하거나 기록하지 않는다. 앱은 메일 주소·링크·비밀번호를 로그에 남기지 않는다.

### 백엔드 미디어 영속 저장소

점주 사진·음성과 근무자 질문 미디어는 DB가 아니라 파일로 저장된다(`MEDIA_ROOT`). 백엔드 Compose는 환경별 고정 이름의 Docker named volume `jidan-dev-media`·`jidan-production-media`를 컨테이너 `/var/lib/jidan/media`에 마운트하고 `MEDIA_ROOT`를 그 경로로 고정한다(`runtime.env`의 `MEDIA_ROOT`는 무시된다). 볼륨 이름은 배포 스크립트가 릴리즈 `.env`의 `MEDIA_VOLUME`으로 넣으므로 릴리즈·롤백이 바뀌어도 같은 볼륨을 쓴다.

- 권한: 이미지가 `/var/lib/jidan/media`를 실행 사용자 `app`(UID 10001) 소유 700으로 만들고, 처음 마운트되는 빈 볼륨에 Docker가 이 소유자·권한을 복사한다. 파일은 0600이다. 배포 스크립트는 새 컨테이너가 이 경로에 쓸 수 있는지 확인하고, 실패하면 이전 릴리즈로 복구한다.
- 보존: `docker compose down`(볼륨 옵션 없이)과 retention 정리는 볼륨을 지우지 않는다. 볼륨을 지우면 DB의 미디어 참조가 파일 없이 남으므로 `docker volume rm`은 하지 않는다.
- 백업: DB 백업만으로 업로드 파일은 복원되지 않는다. DB 백업과 같은 시점에 볼륨도 보관한다. 예: `docker run --rm -v jidan-production-media:/media:ro -v "$PWD":/backup alpine tar czf /backup/media-$(date +%F).tgz -C /media .` 복원은 빈 볼륨에 같은 명령의 역방향(`tar xzf`)으로 풀고 소유자를 `10001:10001`로 맞춘다.
- 이행: 이 설정 이전 이미지의 기본 저장 위치 `/app/.media`는 실행 사용자가 만들 수 없는 디렉터리라(업로드 500) 옮길 파일이 없는 것이 정상이다. 이전에 `runtime.env`로 컨테이너 내부의 다른 경로를 `MEDIA_ROOT`로 지정했다면, 첫 배포 전에 실행 중인 컨테이너에서 `docker cp <컨테이너>:<그 경로>/. <임시 디렉터리>`로 꺼낸 뒤 위 복원 절차로 볼륨에 넣는다.
- 롤백 한계: 이 설정 이전 릴리즈로 롤백하면 그 Compose는 볼륨을 마운트하지 않아 롤백 동안 파일이 보이지 않는다(볼륨의 파일은 남아 있다). 롤백 동안의 업로드는 다시 배포할 때 유지되지 않는다.

Nginx 설정 원본은 `deploy/nginx/`에 있고, 서버에서는 `/etc/nginx/sites-available/jidan-dev`, `jidan-production`을 사용한다. 프록시 변경은 앱 배포와 별도로 `nginx -t` 후 reload한다.

## backend Origin 및 관리자 비밀번호 환경 설정

비공개 `backend/runtime.env`에는 아래 값을 **따옴표 없이 한 번만** 설정한다.

| 환경 | 설정 |
| --- | --- |
| dev | `ALLOWED_ORIGINS=https://dev-jidan.leehyowon14.dev` |
| production | `ALLOWED_ORIGINS=https://jidan.leehyowon14.dev` |

Compose의 `env_file`은 `format: raw`로 값을 주입한다. 와일드카드, 두 환경의 도메인 혼합, localhost 추가, 따옴표, 변수 치환, 중복 키를 사용하지 않는다. 프론트엔드는 각 환경의 동일 출처 `/api`를 유지한다. 설정 변경 시 다른 값은 유지하고 파일 권한은 `600`으로 보존한다. 파일 내용 전체나 다른 환경 변수, 세션·CSRF 토큰을 Git/Actions/이슈에 출력하지 않는다.

같은 파일에 매장 승인 관리자 비밀번호의 `ADMIN_PASSWORD_HASH`도 따옴표 없이 한 번만 설정한다. `back-end`의 Python 환경에서 `python -m app.admin_password`를 실행해 비밀번호를 두 번 숨김 입력하고, 출력된 hash만 보호된 파일에 저장한다. 평문 비밀번호는 저장하지 않는다. 새 hash의 기본 형식은 승인 원격과 같은 `pbkdf2_sha256$<iterations>$<salt>$<key>`다. iterations는 600000~2000000, salt는 16~64바이트, key는 32바이트이며 salt·key는 canonical standard base64(패딩 포함)다. 기존 로컬 `scrypt$<log2 N>$<r>$<p>$<salt>$<key>`도 그대로 검증하므로 기존 설정을 재생성할 필요가 없다. scrypt 생성은 `python -m app.admin_password --scheme scrypt`를 쓴다. log2 N 14~20, r 1~32, p 1~16, salt 16바이트 이상, key 32바이트, 메모리 256 MiB 이내와 OpenSSL 제약을 검사한다. 로그인·health·배포 사전 검사는 모두 `back-end/app/admin_password_config.py`의 parser를 사용한다. `format: raw`가 `$`를 그대로 보존하므로 따옴표나 escaping을 추가하지 않는다. production에도 릴리즈 전에 별도로 설정한다. 누락·중복·지원하지 않는 형식·잘못된 파라미터는 배포가 pull 전에 차단되고 값은 출력하지 않는다.

배포 서버에서 파일을 수정한 뒤, 해당 배포 커밋의 검사기로 컨테이너 변경 없이 사전 점검한다. 성공 시 출력이 없고 실패 시 값이 포함되지 않은 안내와 종료 코드 `2`를 반환한다.

```bash
python3 deploy/scripts/check_runtime_env.py dev /home/ubuntu/apps/jidan/dev/backend/runtime.env
python3 deploy/scripts/check_runtime_env.py production /home/ubuntu/apps/jidan/production/backend/runtime.env
```

배포 스크립트는 권한 `600`으로 복사한 릴리즈의 환경 파일을 이미지 pull 전에 검사한다. 누락·빈 값·다른 Origin, 누락·중복·잘못된 형식의 관리자 hash는 이미지 pull과 migration 전에 배포를 중단한다. 검사기는 같은 checkout의 `back-end/app/admin_password_config.py`(표준 라이브러리만 사용)를 쓰므로 전체 소스 checkout에서 실행하며, `back-end/`가 없으면 통과시키지 않는다. 컨테이너 시작 뒤에도 실제 주입된 `ALLOWED_ORIGINS`와 관리자 hash 형식을 검사하며 값은 출력하지 않는다. 주입된 값이 환경과 다르거나 hash 검증에 실패하면 `current`를 확정하지 않고 기존 롤백 절차를 따른다. frontend 배포에는 이 검사를 적용하지 않는다.

파일 수정만으로 실행 컨테이너의 환경 변수는 바뀌지 않는다. 적용은 기존 backend GitHub Actions에서 `back-end/dev` 또는 `main`의 Run workflow로 수행하며 서버에서 직접 Compose를 재배포하지 않는다. PR은 테스트·빌드만 수행하므로 검사 코드도 각 배포 브랜치에 병합되어야 적용된다.

배포 성공 후 환경 파일과 릴리즈 사본의 권한 `600`, 컨테이너의 환경별 단일 Origin, 공개 `/api/health`를 확인한다. dev·production의 health는 관리자 hash가 없거나 형식이 틀리면 DB 확인 전에 값 없이 503 `{"detail": "Service unavailable"}`을 반환하므로, 컨테이너 헬스체크와 배포 헬스체크도 실패한다(local은 검사하지 않음). 인증 구현이 포함된 환경에서는 유효한 세션·CSRF 토큰과 자신의 Origin이 통과하고, 반대 환경·임의·누락 Origin 및 누락·오류 토큰이 `403 CSRF_INVALID`인지 확인한다. 인증 코드가 아직 없는 production 이미지에서는 설정 주입과 헬스체크만 검증 가능하며, 정상 인증 릴리즈 후 요청 검증을 별도로 수행한다. 실제 OAuth·가입→홈→로그아웃 E2E는 사용자 검증 결과와 함께 기록하고 자동 테스트로 대체했다고 보고하지 않는다.

검증 명령(Linux):

```bash
bash -n deploy/scripts/deploy.sh
python3 -m unittest discover -s deploy/tests -v
```

설정 복구도 비공개 파일을 권한 `600`으로 수정한 뒤 GitHub Actions로 적용한다. 이전 릴리즈 사본에 값이 누락되어 있으면 이미지 롤백만으로 설정 문제가 다시 나타날 수 있다. 설정 복구와 이전 코드의 인증 지원 여부를 함께 확인한다.

## 배포 및 복구

1. GitHub-hosted ARM64 runner에서 테스트 단계와 런타임 이미지를 각각 빌드한다.
2. 저장소의 `GITHUB_TOKEN`으로 GHCR에 게시한다. 별도의 레지스트리 비밀번호는 필요하지 않다.
3. RPi5에서 이미지 digest와 환경별 설정으로 새 릴리즈 디렉터리를 만든다.
4. 이미지를 pull한다. dev 백엔드는 이때 [DB 마이그레이션](#db-마이그레이션)을 적용하고, 실패하면 컨테이너를 바꾸지 않고 중단한다. 이후 `docker compose up -d --wait`로 시작한다.
5. 실행 이미지 ID, 로컬 헬스체크, 공개 도메인 응답을 확인한다.
6. 성공하면 `current` 심볼릭 링크를 새 릴리즈로 전환한다.
7. 오류·SIGINT·SIGTERM 종료 시 실행 중인 배포 명령을 멈추고 이전 Compose·이미지·환경 파일로 복구를 시도한다. 첫 배포라 이전 릴리즈가 없다면 실패한 컴포넌트를 내린다.
8. 컨테이너 변경 전에 `pending` 기록을 남긴다. SIGKILL·전원 차단처럼 즉시 복구할 수 없는 경우에는 다음 배포 실행 시 먼저 복구한다. 복구 실패 시 기록을 유지하며 새 배포를 진행하지 않는다. Actions가 복구 도중 프로세스를 강제 종료하는 경우도 같은 절차를 따른다.

릴리즈는 `/home/ubuntu/apps/jidan/<환경>/<frontend|backend>/releases/`에 보관한다. 백엔드 릴리즈에는 환경 파일 사본이 있으므로 해당 디렉터리도 비공개로 관리한다. 성공한 배포 뒤 해당 환경·컴포넌트의 검증된 최근 릴리즈 5개와 현재 릴리즈를 보관하고 나머지를 정리한다. 복구 중인 `pending` 기록이 있으면 릴리즈를 삭제하지 않는다.

GitHub-hosted 빌드는 실행별 임시 Buildx builder와 GHA 캐시를 사용한다. 캐시는 FE/BE, test/runtime, runtime 환경별로 분리하고 mode=max로 의존성 단계를 보관한다. runner의 로컬 이미지/캐시 정리는 필요하지 않다. 캐시가 없는 첫 실행은 전체 빌드하며 후속 실행에서 복원한다. 테스트 단계 실패 시 runtime 게시와 CD는 실행하지 않는다. 개발 frontend만 미리보기를 포함하며 backend 문서 revision은 현재 SHA로 전달한다.

RPi5에서는 보관 중인 모든 환경의 릴리즈 이미지와 실행·정지된 컨테이너의 이미지를 보호한다. 다른 프로젝트의 이미지·builder와 Docker volume은 정리하지 않으며 `--force` 이미지 삭제를 사용하지 않는다. GHCR 원격 이미지에는 이 로컬 보관 정책을 적용하지 않는다. 이전 RPi4 runner/캐시는 이번 전환에서 삭제하지 않으며 새 CI 작업을 배정하지 않는다.

정리 단계가 실패하면 CI에 실패가 표시된다. 앱 배포 후의 정리 실패는 경고를 남기며, 이미 검증·확정된 앱 배포를 롤백하지 않는다.

수동 재배포는 GitHub Actions에서 해당 frontend/backend 워크플로우의 Run workflow를 실행하고 `main` 또는 해당 `dev` 브랜치를 선택한다. 다른 브랜치는 배포하지 않는다.

## 개발 Swagger 자동 배포

Swagger는 별도 수동 배포 대상이 아니라 개발 백엔드 이미지에 포함된 문서입니다.

1. PR: backend CI에서 Node 명세 lint/계약 테스트/정적 빌드, Python 정적 검사, ARM64 이미지 빌드를 수행합니다. 전체 Python 및 HTTP·MySQL E2E는 CI 선행 조건이 아닙니다. PR에서는 CD가 실행되지 않습니다.
2. `back-end/dev` 반영: 같은 CI를 통과한 이미지를 GHCR에 게시하고 기존 RPi5 CD가 개발 backend Compose를 교체합니다.
3. 개발 앱은 `APP_ENV=dev`에서만 `/api/swagger/`를 제공합니다. 동일 이미지가 production에서 실행되어도 설계 Swagger 경로는 등록하지 않습니다.
4. CD는 기존 health 및 이미지 digest 확인에 더해 로컬/공개 Swagger UI와 JSON을 검증합니다. 실패하면 기존 복구 절차로 이전 backend 이미지에 돌아갑니다.

문서 주소는 `https://dev-jidan.leehyowon14.dev/api/swagger/`입니다. 같은 경로 아래 `openapi.json`, `openapi.yaml`, `build-info.json`을 제공합니다. API server base URL은 `https://dev-jidan.leehyowon14.dev`이며 각 명세 path에 `/api`가 이미 포함됩니다. `/api` 접두사는 두 번 붙이지 않습니다.

Nginx 변경·신규 포트·별도 문서 컨테이너는 필요하지 않습니다. 정적 문서는 Docker Node 단계에서 생성하며 런타임 이미지에는 Node 의존성을 설치하지 않습니다. `build-info.json`은 문서 버전, GitHub 커밋 revision, operation 수, 명세 해시를 제공하고 산출물은 Git에 추가하지 않습니다. 기존 로컬 5500 검수 서버는 계속 사용합니다.

## 초기 구성 검증 (2026-09-21)

- 두 runner의 GitHub online 상태 확인.
- RPi4에서 배포 스크립트 테스트 6개 통과: 성공, 잘못된 입력, pull 실패, 시작 실패, 이미지 불일치, 공개 URL 실패.
- actionlint 및 RPi5 Docker Compose 설정 검증 통과.
- 개발·운영 DB/계정 및 비공개 환경 파일 생성.
- RPi5 Nginx 설정 검증·reload 완료. 두 공개 도메인의 요청이 RPi5 Nginx에 도달하는 것 확인.
- 앱 초기화 전이므로 실제 React/FastAPI 이미지 빌드·GHCR 업로드·앱 배포는 미검증. 현재 앱 포트에 실행 중인 서비스가 없어 공개 도메인은 502를 반환한다.

검증 명령:

```bash
# Linux / GitHub-hosted와 동일한 환경
bash -n deploy/scripts/deploy.sh
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s deploy/tests -v

# actionlint 설치 환경
actionlint -shellcheck= -pyflakes= .github/workflows/*.yml
```

## OAuth callback 프록시 검증

backend CI는 `python3 deploy/scripts/verify_nginx.py`로 dev/production 원본 설정을
일회용 Nginx 컨테이너에서 `nginx -t` 검증한다. 임의의 localhost 포트를 사용하며
정상 upstream(200)과 연결 실패(502) callback 모두에서 가짜 code/state가 로그에
남지 않는지 확인한다. 일반 요청의 로그가 실제로 남는지도 대조한다.
이 검사는 서버 설정을 교체하거나 reload하지 않는다. 실제 서버의 설정 반영과
`nginx -t`/reload 및 로그 차단 확인은 배포 시 별도로 수행해야 한다.

요청 body 상한은 기본 10m이고, 매뉴얼·질문 미디어 업로드 경로
(`/api/stores/{storeId}/manual/media`, `/api/stores/{storeId}/manual/qa/media`)만 20 MiB 음성과
multipart 구분자를 위해 21m이다. 용도별 정확한 상한(사진 10 MiB, 음성 20 MiB·120초)은 앱이
413으로 판정한다. 같은 스크립트가 두 경로의 20 MiB 업로드 통과와 그 밖의 경로·상한 초과의
413을 확인한다.

## 배포 후 백엔드 검증

배포 스크립트의 컨테이너·공개 health 검사와 롤백은 유지한다. 개발 배포가 완료되면 에이전트가 배포된 커밋·문서 revision·health 및 실제 사용자 흐름을 확인하고, 저장 결과·API 재조회·권한 거부 결과와 미검증 범위를 PR에 기록한다. E2E의 스키마 초기화·실패 주입은 격리된 테스트 DB에서만 수행하며 개발·운영 DB에 테스트 하네스를 연결하지 않는다. 전체 회귀 검증은 해당 배포 소스로 로컬의 `back-end/testing/run-e2e.sh` 또는 수동 `Backend HTTP E2E` 워크플로우를 실행한다. 격리 회귀 결과와 배포 서버 검증 결과를 구분해 기록한다.

## 로컬 검증 후 dev CD만 실행

로컬 CI 검증을 완료한 백엔드 커밋은 `gh workflow run backend.yml --ref back-end/dev -f skip_ci=true`로 배포한다. 이 옵션은 dev의 수동 실행에서만 배포 로직 테스트·Nginx 테스트·test 이미지 단계를 생략한다. runtime 이미지 생성에 필요한 문서 빌드, 이미지 게시, migration, 환경 검증, health 검사와 롤백은 유지한다. PR·push·production은 기존 검사를 생략하지 않는다.

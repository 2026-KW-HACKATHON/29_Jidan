# 공개 데모 API

`demo/back-end/dev` 전용 계약. 기존 회원 OpenAPI와 별도로 관리한다.
회원 가입이나 Google 로그인 없이 기존 매뉴얼·Q&A API를 체험한다.

## 배포 설정

- `DEMO_ROLE=OWNER|WORKER`: 서버가 역할을 고정한다. 요청 본문으로 역할을 선택하지 않는다.
- `DEMO_HOST`: 기본값은 OWNER의 `jidan-jumju-demo.leehyowon14.dev`, WORKER의 `jidan-ilban-demo.leehyowon14.dev`.
- `APP_ENV=dev`, 별도의 `DB_NAME=*_demo`, 별도 `MEDIA_ROOT`가 필요하다.
- 한 역할당 uvicorn 프로세스 하나와 기존 DB 작업 실행기만 사용한다.
- 세션은 2시간 뒤 만료하며 host-only HttpOnly cookie와 기존 Origin·CSRF 검증을 사용한다.
- 기존 dev env의 AI/STT 설정은 재사용한다. 데모의 DB·미디어 volume은 dev와 분리한다.

## 세션 계약

`POST /api/demo/session`: 허용된 Origin 필요. 최초 요청은 방문자와 세션을 생성하고,
유효 cookie가 있으면 만료 시각과 작성 공간을 연장 없이 그대로 반환한다.
반대 역할 또는 일반 회원 cookie는 데모 권한을 갖지 않으며 새 방문자를 발급한다.

`POST /api/demo/reset`: 유효 방문자 cookie, Origin 및 `X-CSRF-Token` 필요.
기존 세션을 원자적으로 폐기하고 새 방문자 공간을 발급한다. 중복·동시 초기화는 하나만 성공한다.
이전 세션의 데이터는 새 방문자에게 보이지 않는다.

성공 응답(200):

```json
{"role":"OWNER","storeId":"UUID","expiresAt":"RFC3339 UTC","csrfToken":"string"}
```

실패는 기존 Error 계약을 따른다. 401 만료/폐기/데모로 인정되지 않는 cookie, 403 Origin·CSRF·host·범위,
429 요청 제한, 503 샘플 준비 실패. 실패 시 cookie와 부분 데이터는 발급하지 않는다.

OWNER는 본인 매뉴얼 API와 게시본 읽기만, WORKER는 고정 샘플 게시본 읽기와 본인 Q&A만
접근한다. 홈·회원·매장 관리·구직·Swagger 경로는 데모 서버에서 차단한다.

## 샘플 준비와 제한

격리 DB에 migration을 적용한 뒤 `python -m app.demo_seed_public`을 실행한다.
샘플 초기화는 멱등이며 기존 방문자의 대화·작성 공간을 초기화하지 않는다.
매뉴얼의 음료 레시피는 정보가 없는 예시이며 답변에서 내용을 만들어내면 안 된다.

쓰기 요청은 단일 서버 프로세스의 시간 구간당 전체 1000회·방문자 150회로 제한한다.
실패·멱등 재전송도 요청 수에 포함한다. GET polling은 제한에 포함하지 않는다.
프로세스 재시작 시 카운터는 초기화되며 금액 한도는 아니다. 작업 동시 실행은
`TASK_RUNNER_WORKERS`로 제한한다. 유료 실제 AI의 응답 품질은 별도 검증 대상이다.

`python -m app.demo_retention`은 만료 후 30분 유예가 지난 방문자만 정리한다.
진행 중 작업 결과의 저장을 막기 위해 작업 행을 먼저 삭제하고, 매뉴얼·대화·세션·계정은
같은 DB 트랜잭션으로 제거한다. commit 이후 사진·녹음 파일을 삭제하며 실패한 파일은
orphan sweep이 재시도한다. 고정 샘플과 유효 방문자는 보존한다. Compose의 별도
retention 서비스에서 5분마다 실행한다. 초기화로 폐기된 공간도 폐기 후 30분의
유예가 지나면 제거한다. 사용자 생성 시각 대신 세션의 실제 만료·폐기 시각을 기준으로 한다.

## 검증

- `tests/test_public_demo.py`: SQLite·MySQL 세션/설정/권한/초기화/요청 제한 회귀.
- `e2e/test_public_demo_http.py`: 실제 두 역할 TCP 서버, 전용 MySQL, 독립 DB 조회.
- `e2e/test_public_demo_retention_http.py`: 파일·작업 제거, 후속 enqueue 경합, 실패 롤백,
  유효 세션 보존, 유예 경계와 동시 정리.
- AI는 fake, background 작업은 manual이다. 실제 음성·AI 의미 품질·공개 배포 검증과 구분한다.

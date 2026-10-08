# 로그인·가입·세션 API

Refs #123. 계약 기준은 `back-end/dev`의 OpenAPI 0.10.0과 dev에 배포된 인증 API입니다.

- `/`, `/login`: `GET /api/auth/session` 조회. 회원 세션이 없으면 `GET /api/auth/registration`으로 가입 세션을 확인합니다. 둘 다 없으면 Google 로그인 버튼을 표시합니다.
- Google 버튼은 동일 출처 `GET /api/auth/google`로 **페이지 이동**합니다. OAuth code/state 처리는 백엔드 callback이 담당합니다.
- 백엔드 복귀 경로 `/__auth/session`, `/__auth/signup`은 모든 빌드에서 제품 인증 경계로 진입하고 `/home`, `/signup`으로 정리합니다. 기존 가입 유형 미리보기는 `/__preview/signup`으로 이동했습니다.
- URL/query/storage는 로그인 권한의 근거가 아닙니다. callback의 공개 `error`는 안내에만 사용하고 주소에서 제거합니다.
- `/signup`에서 유형을 선택합니다. 이메일은 서버 가입 컨텍스트의 읽기 전용 값입니다. 점주·일반회원 최종 제출은 각각 `POST /api/auth/registrations/owners`, `/workers`로 보냅니다.
- 가입 세션의 서버 `expiresAt`에 도달하면 로그인으로 복귀합니다. 임시 입력은 30분 동안 같은 탭에 보존하고, 재인증한 같은 Google 이메일에서만 복원합니다. 초안이나 이메일은 서버 인가에 쓰지 않습니다. 성공하면 초안을 삭제하고 가입 만료 타이머를 해제합니다.
- 기존 회원은 서버 `user.role`, `nextAction`으로 홈을 표시합니다. `OWNER_APPROVAL_PENDING`은 서버 매장 이름을 사용한 기존 승인 대기 화면입니다. 홈의 로그아웃은 CSRF 조회 후 `POST /api/auth/logout`을 호출합니다. 성공 후 로그인으로 돌아갑니다.
- 가입 유형 화면의 뒤로 가기는 가입 세션을 로그아웃한 뒤 로그인으로 돌아갑니다. 실패하면 안내하고 재시도할 수 있습니다.

공통 `api/client.ts`는 상대 `/api` 경로, `credentials: include`, `Cache-Control`을 고려한 `cache: no-store`, 쓰기 전 CSRF 조회와 `X-CSRF-Token`, 명세에 필요한 요청의 `Idempotency-Key`를 적용합니다. CSRF 토큰은 요청 메모리에만 보관합니다. 응답 본문 없이 성공하는 204를 처리하고, 오류 `code`별 문구·일반 오류 fallback·422 필드 오류를 제공합니다. 동일 내용 재시도는 기존 key를 유지하며 자동으로 쓰기 요청을 반복하지 않습니다.

CSRF 조회와 실제 쓰기를 합쳐 `async/deadline.ts`의 10초 제한을 적용합니다. 취소 또는 지연된 결과는 화면을 갱신하지 않습니다. 상태 조회 실패 시 다시 시도할 수 있고 버튼 중복 제출을 차단합니다.

## 환경과 실행

배포 FE는 dev·production 모두 **자신의 도메인 `/api`**를 호출합니다. 브라우저 API 주소로 반대 환경의 절대 URL을 지정하지 않습니다.

- dev: `https://dev-jidan.leehyowon14.dev`
- production: `https://jidan.leehyowon14.dev`
- API 명세: `https://dev-jidan.leehyowon14.dev/api/swagger/`
- 로컬: `VITE_API_PROXY_TARGET=http://127.0.0.1:8000 npm run dev -- --host 127.0.0.1 --port 5184` (`front-end`에서 실행). 기본 대상도 8000입니다. 로컬 OAuth에는 로컬 frontend origin/callback과 허용 Origin을 설정한 백엔드를 사용합니다.

PR에서는 배포하지 않습니다. `front-end/dev` 병합 후 dev CI/CD가 배포하고, production은 별도의 main 릴리즈 흐름을 따릅니다. 인증 이외 아직 연동하지 않은 도메인 서비스와 검수용 샘플 서비스는 유지합니다.

## 검증 경계

`npm run lint`, `npm run test:ci`, `npm run build`, `node scripts/verify-preview-build.mjs`로 검사합니다. 정상·401·403·422·서버/네트워크 실패·재시도·10초 지연·취소·중복 제출·callback 경로·만료·bfcache 복원을 테스트 대역으로 검증합니다.

실제 Google 로그인, dev에서 점주/일반회원 가입→홈→로그아웃, 실제 10분 만료와 모바일 화면의 E2E 검증은 사용자가 수행합니다. 단위 테스트 통과를 실제 E2E 통과로 해석하지 않습니다.

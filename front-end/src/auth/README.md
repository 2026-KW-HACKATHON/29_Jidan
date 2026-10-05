# 로그인·가입 유형 선택

Google을 기준으로 화면을 구성합니다. 인증 API 명세는 아직 없으며 제품 코드는 인증 HTTP 요청이나 외부 로그인 이동을 수행하지 않습니다.

- `/`, `/login`: Google 로그인 화면. 버튼은 연결 준비 안내를 표시합니다.
- `/signup`, `/signup/owner`, `/signup/worker`, `/home`: 기본 인증 경계는 미준비 상태이므로 로그인으로 돌아갑니다.
- `AuthService`: 프런트 화면 상태 주입용 인터페이스입니다. 서버 응답·쿠키·콜백 규약을 정의하지 않습니다.
- `Session`: 홈 표시용 이름·역할만 포함합니다. 서버의 세션 모델이 아닙니다.
- `/__auth/signup`, `/__auth/owner`, `/__auth/worker`, `/__home/owner`, `/__home/worker`: 개발 전용 mock 미리보기입니다.
- `/status`: 기존 health API 확인 화면입니다. 인증 흐름과 분리되어 있습니다.

향후 확정된 Google API 명세를 받은 뒤 `authService` 어댑터와 필요한 서버 계약 검증을 구현합니다. query parameter·cookie·storage로 인증 상태를 만들지 않습니다. 현재 mock의 등록·홈 분기는 실제 로그인 성공을 의미하지 않습니다.

## 검증

`npm run test:ci`, `npm run lint`, `npm run build`로 검증합니다. 인증 화면 진입·버튼 클릭의 무요청, 등록/홈 직접 진입 차단, mock 역할 분기, unmount 후 늦은 결과 무시, 브라우저 뒤로가기·bfcache 복원을 포함합니다.

공통 MobileLayout·Button·Modal을 사용하며 최대 너비 390px의 모바일 레이아웃을 유지합니다.

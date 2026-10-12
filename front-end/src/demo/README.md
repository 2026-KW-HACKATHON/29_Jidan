# 로그인 없는 매뉴얼 체험

두 데모 host는 역할을 고정하며 기존 홈·AuthFlow를 렌더링하지 않는다.
일반 사용자는 `/manual` → `/questions`, 점주는 `/author` → 게시본 `/manual`만 이동한다.
범위 밖 URL은 각 역할 시작으로 교체하며 URL의 store/role 값으로 권한을 선택하지 않는다.

방문자 세션은 `/api/demo/session`에서 발급·복원한다. StrictMode의 중복 mount는 같은
발급 요청을 공유한다. 새 cookie가 발급되면 이전 대화 URL을 지우며, 같은 세션의
새로고침은 인터뷰 서버 상태 또는 대화 URL을 복원한다. 초기화는 명시적 확인 후 실행하고,
실패 시 기존 화면·입력을 유지한다. 만료 응답은 로그인 화면 대신 새 체험을 안내한다.

서버 역할·API 제한·방문자 격리·만료 정리 계약은 백엔드 `docs/public-demo.md`에 있다.
음성 인터뷰, 사진, 부족 항목 확인, 게시, 근무자 미리보기와 AI 근거 모달은 기존
매뉴얼·Q&A 컴포넌트를 재사용한다. 샘플 조회에는 매장 선택·근무자 홈 API가 필요 없다.

데모 이미지 빌드에는 `VITE_ENABLE_DEMO=true`를 사용한다. 이 빌드는 알 수 없는 host에
AuthFlow를 열지 않는다. 로컬 Vite 검증은 `VITE_DEMO_ROLE=OWNER|WORKER`와
`VITE_API_PROXY_TARGET`을 설정한다. 두 역할을 동시에 확인할 때는 cookie host가
겹치지 않게 `localhost`와 `127.0.0.1`을 나누고 서버 `DEMO_HOST`·Origin도 맞춘다.

검증: `npm run lint`, `npm run test:ci`, `npm run build`.
`routes.test.ts`, `session.test.ts`, `DemoApp.test.tsx`는 도메인·라우트·발급 중복·응답 검증·
복원·초기화 취소/실패/성공·만료를 확인한다. 화면 테스트의 업무 컴포넌트는 fixture이며
실제 API·AI 연동이나 기기 마이크/카메라 검증과 구분한다.

# Jidan Frontend

React + TypeScript + Vite. Node.js 22 사용.

```bash
cd front-end
npm ci
npm run dev
```

개발 서버의 `/api` 요청은 `http://127.0.0.1:8000`으로 전달한다. 배포 환경에서는 동일 도메인의 `/api`를 사용한다.

```bash
npm run lint
npm run test:ci
npm run build
```

기본 경로는 로그인 화면이며 API·DB 연결 상태는 `/status`에서 확인합니다.
[로그인·가입 유형 선택의 연동 계약과 검증 범위](src/auth/README.md)를 확인합니다.
[점주 가입 흐름 및 서버 연동 경계](src/registration/owner/README.md)를 확인합니다.
[CI/CD 운영 문서](../deploy/CI-CD.md)

## 공통 UI

[Figma 원본 매핑 및 사용법](src/ui/README.md)을 확인합니다.
개발 서버의 `/__ui`에서 컴포넌트 상태와 키보드 동작을 확인합니다. 해당 경로는 production build에 포함되지 않습니다.

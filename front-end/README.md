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

초기 화면은 API·DB 연결 상태만 확인한다. 제품 기능은 후속 작업에서 구현한다.
[CI/CD 운영 문서](../deploy/CI-CD.md)

## 공통 UI

[Figma 원본 매핑 및 사용법](src/ui/README.md)을 확인합니다.
개발 서버의 `/__ui`에서 컴포넌트 상태와 키보드 동작을 확인합니다. 해당 경로는 production build에 포함되지 않습니다.

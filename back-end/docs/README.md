# OpenAPI 로컬 문서 도구

Node.js 22 이상 사용. 이 도구는 `../openapi.yaml`을 Swagger UI로 제공하는 문서 서버입니다.
인증 API 구현, OAuth 연결, DB 저장, 모의 API는 포함하지 않습니다. 실행 버튼은 비활성화되어 있습니다.
Swagger 자산은 npm 의존성에서 제공하므로 실행 중 외부 CDN 연결이 필요하지 않습니다.

```bash
cd back-end/docs
npm ci
npm run dev
```

http://127.0.0.1:5500 에 접속합니다. YAML은 `/openapi.yaml`, JSON은 `/openapi.json`으로 확인합니다.
명세 수정 후 브라우저를 새로고침하면 반영됩니다. `PORT=5501 npm run dev`로 포트를 바꿀 수 있습니다.
서버는 `127.0.0.1`에만 바인딩합니다. 종료는 실행한 터미널에서 `Ctrl+C`입니다.

```bash
npm run check  # OpenAPI lint 및 계약/문서 서버 테스트
npm test      # 명세가 없어도 문서 서버 테스트 실행 가능
```

문서 의존성은 백엔드 런타임과 별도로 설치합니다. 기존 FastAPI `/docs`는 구현된 API만 표시하며,
이 문서 서버의 설계 명세와 다릅니다.

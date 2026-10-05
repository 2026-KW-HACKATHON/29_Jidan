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

## 명세 검수

[화면별 챕터와 추가 endpoint 전체 목록](remaining-api-inventory.md)에서 Figma와 OpenAPI 대응을 확인합니다. 현재 `openapi.yaml` 0.7.0은 99개 operation이며, 실제 인증·DB·AI·알림·파일 저장 구현을 뜻하지 않습니다. 챕터별 설계 문서에 화면 근거와 보완 제안을 구분했습니다.

계약 테스트는 요청/응답 형식과 예시, 권한·상태·참조 규칙을 검사합니다. 날짜 간 비교, 트랜잭션 경합, 실제 AI 근거 충실도는 향후 백엔드 구현에서 검증해야 합니다. Redocly lint에는 작업 전부터 있던 매뉴얼 조합/예시 경고 20개가 남아 있으며 Ajv 계약 테스트와 구분해 확인합니다. OAuth 302 응답 2개의 기존 lint 예외는 유지합니다.

현재 검수 기준은 사용자 수정 디자인의 요청 철회·확정 철회 → 모집 마감 순서, 시작 전 확정 철회 제한, 철회 후 재지원, 전체 매장 대타 월별 캘린더, 시간 일치 우선 추천입니다. `npm run check --prefix back-end/docs`에서 계약/문서 서버 테스트 404개가 통과했습니다.

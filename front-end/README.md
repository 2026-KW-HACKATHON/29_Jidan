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
개발 서버의 `/__ui`에서 컴포넌트 상태와 키보드 동작을 확인합니다. 아래 검수용 빌드에도 포함하며, 미리보기 플래그가 비활성화된 운영 빌드에서는 제외합니다.

## dev 배포 화면 탐색

- 로컬: `npm run dev -- --host 127.0.0.1 --port 5184` 실행 후 `http://127.0.0.1:5184/__preview` 접속합니다.
- dev 배포: 이 변경을 `front-end/dev`에 병합하여 배포한 뒤 `https://dev-jidan.leehyowon14.dev/__preview`에서 확인합니다. Draft PR 생성만으로 배포하지 않습니다.
- 직접 주소 접근과 새로고침은 기존 nginx SPA fallback을 사용합니다.
- 검수용 배포 빌드: `VITE_ENABLE_PREVIEW=true npm run build`. 운영 빌드는 플래그를 생략하거나 `false`로 설정합니다.
- CI는 frontend + dev 조합에만 Docker build arg를 `true`로 전달합니다. backend와 production은 `false`입니다.
- `node scripts/verify-preview-build.mjs`로 두 빌드의 미리보기 청크와 mock 데이터 포함 여부를 검증합니다. 마지막 산출물은 운영 빌드입니다.
- 미리보기는 API 미연결 표시와 샘플 서비스만 사용합니다. 실제 인증 쿠키나 계정 데이터를 만들지 않습니다. 점주 초안은 `preview.v2.*` 전용 sessionStorage 영역에 저장합니다.
- 일반회원 프로필 수정은 같은 미리보기 화면 안에서 저장 후 재진입해도 유지합니다. 새로고침하거나 다른 주소로 이동하면 샘플 프로필로 초기화합니다. 점주 초안은 점주 미리보기에서만 읽고 변경합니다.
- 접근 종료의 `?fail=1`은 첫 요청 실패 후 재시도 성공을 재현합니다. 프로필·등록 실패 경로도 목록에서 선택할 수 있습니다.
- 실제 MVP 인증·진입 흐름을 검증한 후 #53에서 이 임시 기능과 빌드 플래그를 제거합니다.

[점주 공고 등록·관리·지원자 확인과 미리보기 주소](src/jobs/owner/README.md)를 확인합니다.

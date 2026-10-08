# 일반 회원 AI 업무 질문

일반 회원의 게시 매뉴얼 하단 `AI에게 업무 질문하기`에서 매장별 대화 목록, 새 질문, 기존 대화로 이동한다. 운영 화면은 Q&A API를 호출하며 샘플 답변으로 실패를 대체하지 않는다.

- 목록: `/home?view=qa&store={storeId}`
- 새 질문: `/home?view=qa&store={storeId}&new=1`
- 복원: `/home?view=qa&store={storeId}&id={conversationId}`

`getMyStoreAccess`의 `USE_AI_QA`와 게시본을 확인한 뒤 진입한다. 첫 질문 접수 후 대화 ID를 주소에 반영하므로 새로고침해도 복원된다. 목록은 20개씩, 대화는 최근 20개와 `beforeSequence`로 이전 질문을 조회한다.

## 계약과 상태

`generate-qa-contract.mjs`는 backend OpenAPI의 `/manual/qa/` 12개 operation만 생성한다. 점주 인터뷰 및 일반 업무 API 생성물은 변경하지 않는다. 현재 기준은 `back-end/dev`의 OpenAPI 0.11.0이다.

```sh
node scripts/generate-qa-contract.mjs /path/to/backend-openapi.json
```

- 서비스는 쿠키·CSRF·멱등 키와 요청 취소를 전달하고 요청 및 응답을 검증한다. 서버 오류 원문은 UI에 표시하지 않는다.
- 생성·질문·재시도·미디어 요청은 응답 유실 시 동일한 키를 재사용한다. 질문 접수 여부가 불명확하면 입력을 잠그고 재시도를 제공한다.
- 답변은 `RUNNING/READY/ERROR`를 표시한다. `Retry-After`를 참고해 최소 2초, 최대 60초 간격으로 조회하며 숨겨진 탭에서는 대기한다. 화면 이탈 시 요청을 중단한다.
- `ANSWERED`는 매뉴얼 근거를, `NEEDS_OWNER`는 점주 확인 안내를 표시한다. 근거 조회는 `expectedVersionId`를 보내며 `MANUAL_VERSION_CHANGED`이면 최신 매뉴얼로 이동한다.
- 권한 종료를 알리는 401/403/404 응답은 표시된 대화와 입력창을 제거한다. 사진·근거 조회에도 적용한다.
- 대화 이력의 모델 전달은 서버 책임이다. 프론트는 이전 질문/답변을 요청 body에 추가하지 않는다.

## 입력

텍스트는 공백이 아닌 2,000자 이하이다. 사진만 보내는 질문은 허용하지 않는다. JPG·PNG·WebP는 각 10 MiB 이하, 질문당 최대 3장이다. 미전송 사진 삭제는 QA 삭제 API에 연결된다. 전송된 사진은 인증된 보호 조회로 읽고 object URL을 해제한다. 보관 기한이 지난 사진은 만료 상태를 표시한다.

마이크는 기존 녹음기를 재사용하며 최대 2분·20 MiB이다. QA 음성 업로드→전사→내용 확인 후 질문을 제출한다. 전사 원문 그대로면 `VOICE`, 사용자가 수정하면 `TEXT`를 보낸다. 전사 실패는 전용 retries API로 재시도하며 응답 유실 시 이미 성공한 업로드 단계를 반복하지 않는다. 점주 작성 MVP의 음성 전용 제한을 근무자 질문에 적용하지 않는다.

질문·녹음·사진을 localStorage/sessionStorage에 저장하지 않는다. 화면 이탈로 버려진 미연결 업로드는 서버의 보관 기간 정책을 따른다.

## 디자인과 검증

[Figma 624:2626](https://www.figma.com/design/ZaFHresnBXJ1h98Xl1AUDj?node-id=624-2626)의 배경, 사용자 말풍선, AI 답변·근거 카드, 입력 도구를 공통 MobileLayout/AppBar/Button/Modal과 토큰으로 구현한다. 브라우저 화면에는 Figma의 기기 상태 표시줄을 중복 렌더링하지 않는다. 대화 목록·빈 상태·오류 복구·입력 제한 표시는 계약 동작에 맞춘 보완이다.

```sh
npm ci
npm run dev -- --host 127.0.0.1 --port 5190
npm run test:ci -- --maxWorkers=2 src/qa
CI=true npm run test:ci -- --maxWorkers=2
npm run lint
npm run build
```

2026-10-09 검증:

- Q&A 테스트: 공백/길이/파일 형식/크기, 중복 제출, 응답 유실, 권한 종료, 게시 버전 충돌, 음성 전사 및 수정, 사진 삭제/만료, 취소와 URL 해제.
- 별도 SQLite 및 backend `585fd1d`의 실제 HTTP API를 사용한 로컬 브라우저 검증: 매뉴얼 진입→대화 생성→텍스트 답변→근거→새로고침, 사진 업로드·삭제·질문·보호 조회, 녹음→음성 업로드→전사→VOICE 제출.
- AI/STT는 FakeAiProvider, 마이크는 Chromium의 테스트 음원 입력이다. API 응답과 DB 저장은 실제 구현을 사용했다. 별도 DB 연결로 TEXT/VOICE 질문의 READY 저장 및 사진 연결/삭제를 확인했다.
- 서버 저장 후 응답을 차단하는 시험에서 같은 키 재전송과 질문 1건 저장을 확인했다. fixture의 게시본 교체와 접근 철회 후 각각 버전 충돌 안내와 대화·입력 제거를 확인했다.
- 320/390/768px에서 가로 넘침 없음. 브라우저 실행 오류 없음.
- 실제 OpenAI/STT 품질, 물리 마이크·카메라, Safari, MySQL 및 인증된 dev 사용자 E2E는 이번 검증 범위에 포함하지 않았다.
- 기존 500 kB 초과 운영 번들 경고는 남아 있다. 이 변경은 frontend dev 대상이며 배포·main 릴리즈 완료를 뜻하지 않는다.

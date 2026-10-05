# 점주 매뉴얼 작성 MVP

OpenAPI 0.10.0(PR #136)을 기준으로 합니다. `types.ts`와 `contract.generated.ts`는 선택한 점주 매뉴얼 26개 operation 및 참조 schema의 스냅샷입니다. 전체 내용 편집 등 넓은 API 계약은 유지합니다. `ManualService`는 매장별로 생성하며 `createManualHttpService`에 #123의 CSRF 조회 함수를 주입합니다. 인증 수명주기와 운영 진입은 #131에서 연결합니다.

음성 완료 → 업로드 → 전사 READY 조회 → VOICE 답변 제출을 연속 수행하고 원문 확인은 제공하지 않습니다. 응답 유실 재시도는 같은 키와 본문을 사용합니다. 녹음 Blob은 메모리에만 보관하며 화면 이탈 시 마이크와 타이머를 해제합니다. 20 MiB/120초 상한을 허용하고 미지원·권한 거부·무음·빈 전사는 재녹음을 안내합니다. 백그라운드에서는 상태 조회를 멈춥니다.

`/__manual`과 `/__manual?resume=1`은 `/__preview`의 샘플 전용 진입입니다. 실제 마이크 녹음과 계약에 맞는 모의 서비스로 UI를 검수합니다. 음성 내용에 따른 실제 STT/AI 판단은 #119·#120·#131 구현 후 검증합니다. 샘플 코드·데이터는 운영 빌드에서 제외됩니다.

Figma: [시작](https://www.figma.com/design/ZaFHresnBXJ1h98Xl1AUDj?node-id=330-2670), [질문](https://www.figma.com/design/ZaFHresnBXJ1h98Xl1AUDj?node-id=330-2713), [녹음](https://www.figma.com/design/ZaFHresnBXJ1h98Xl1AUDj?node-id=376-1676), [처리 중](https://www.figma.com/design/ZaFHresnBXJ1h98Xl1AUDj?node-id=376-1711). 공통 AppBar·Button·MobileLayout 및 원본 back.svg(7×14)를 재사용합니다. 안내 카드·예시 등 현재 응답에 없는 presentation 데이터는 질문 문자열에서 추측하거나 임의로 생성하지 않습니다.

질문은 session.phase와 questionId로 렌더링하며 depth나 다음 주제를 프론트에서 계산하지 않습니다. 인텐트 검토의 revision은 세션 revision과 분리합니다. 검토 오류·정정 중에도 질문을 계속 진행하고 이전 요약은 별도로 재진입·확인·건너뛸 수 있습니다. 음성 답변 이력은 완료 여부만 표시하며 전사 원문을 노출하지 않습니다. 모든 검토 READY 여부는 생성 전 확인하고 confirmedAt은 생성 선행 조건으로 사용하지 않습니다. `?case=review`, `review-error`, `depth5`, `ready`로 계약 상태를 검수합니다.

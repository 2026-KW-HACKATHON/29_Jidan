# 공통 UI 사용법

Figma `ZaFHresnBXJ1h98Xl1AUDj`의 Design System 페이지를 기준으로 작성합니다.

## 컴포넌트와 원본

| 컴포넌트 | Figma 노드 | 적용 기준 |
| --- | --- | --- |
| tokens.css | [Design System](https://www.figma.com/design/ZaFHresnBXJ1h98Xl1AUDj?node-id=1-2) | 사용되는 색상·간격·모서리·그림자 및 Pretendard GOV 서체를 토큰으로 정의합니다. |
| Button | [347:1554](https://www.figma.com/design/ZaFHresnBXJ1h98Xl1AUDj?node-id=347-1554) | Primary·Secondary·Danger, 최소 높이 48px, padding 12px 16px, radius 8px를 적용합니다. |
| InputField / SelectField | [347:1549](https://www.figma.com/design/ZaFHresnBXJ1h98Xl1AUDj?node-id=347-1549) | 기본 테두리 없이 level-1 그림자를 적용하고 오류는 안내 문구 색상으로 표현합니다. |
| Choice | [347:1548](https://www.figma.com/design/ZaFHresnBXJ1h98Xl1AUDj?node-id=347-1548) | 기본·선택 상태, 12px padding, 15px bold를 적용합니다. |
| Checkbox | [395:1535](https://www.figma.com/design/ZaFHresnBXJ1h98Xl1AUDj?node-id=395-1535) | 20×26px 배치 공간에 16px 체크박스와 원본 체크 아이콘을 적용합니다. |
| Modal | [233:207](https://www.figma.com/design/ZaFHresnBXJ1h98Xl1AUDj?node-id=233-207) | Warning·Error·Information, 최대 너비 342px, padding 24px, radius 12px, 제목 19px를 적용합니다. [경고](https://www.figma.com/design/ZaFHresnBXJ1h98Xl1AUDj?node-id=233-208)·[오류](https://www.figma.com/design/ZaFHresnBXJ1h98Xl1AUDj?node-id=233-270)·[안내](https://www.figma.com/design/ZaFHresnBXJ1h98Xl1AUDj?node-id=233-332) 예시의 서로 다른 상태 아이콘을 사용합니다. |
| AppBar | [347:1560](https://www.figma.com/design/ZaFHresnBXJ1h98Xl1AUDj?node-id=347-1560) | Regular·Compact, 7×14px 뒤로가기 아이콘, 제목 17px를 적용합니다. |
| RegistrationProgress | [347:1550](https://www.figma.com/design/ZaFHresnBXJ1h98Xl1AUDj?node-id=347-1550) | 사장님·근무자의 3단계 라벨과 4px 진행 막대를 적용합니다. |

아이콘은 Figma export 자산을 사용합니다. 서체는 Pretendard GOV 배포 자산이며 `public/fonts/LICENSE.txt`를 함께 제공합니다. 기존 프로토타입의 로직을 가져오지 않습니다.

## 화면 구성

```tsx
import { MobileLayout } from './ui/MobileLayout'
import { AppBar } from './ui/AppBar'
import { Button } from './ui/Button'
import { InputField } from './ui/Field'

<MobileLayout
  header={<AppBar title="기본 정보" onBack={goBack} />}
  footer={<Button onClick={goNext}>다음</Button>}
>
  <InputField label="이름" value={name} onChange={e => setName(e.target.value)} />
</MobileLayout>
```

MobileLayout은 너비 100%, 최대 너비 390px, 중앙 정렬과 100dvh 높이를 적용합니다. 상하단은 프레임 내부에 유지하고 본문만 스크롤합니다. 상하단에는 safe-area 여백을 반영합니다. 화면별 배경이 다르면 해당 화면 CSS에서 `.ds-mobile` 배경을 지정합니다. 단독 컴포넌트도 필요한 토큰과 스타일을 직접 가져옵니다.

## 입력 및 선택

- InputField와 TextareaField는 native 입력 속성을 전달합니다. `label`은 필수이며 `helper`, `error`, `readOnly`, `disabled`를 사용할 수 있습니다. `error`가 있으면 helper 대신 오류 문구를 표시하고 aria-invalid와 설명 연결을 적용합니다.
- SelectField는 선택 화면을 여는 버튼입니다. `onClick`, `aria-haspopup`, `aria-expanded`와 실제 선택 화면은 호출 화면에서 연결합니다.
- Choice는 기본 radio입니다. 단일 선택은 같은 `name`을 공유하고 `value`를 지정합니다. 다중 선택은 `type="checkbox"`를 사용합니다. 그룹은 fieldset과 legend로 이름을 제공합니다.
- Checkbox는 label 내부에 배치하거나 id/htmlFor 또는 aria-label로 이름을 제공합니다.
- Button은 기본 `type="button"`입니다. 폼 제출은 명시적으로 `type="submit"`을 지정합니다. `busy`는 중복 활성화를 차단합니다.

Textarea의 기본 높이 120px, disabled의 opacity .45, 키보드 포커스 outline, 비동기 실패 안내는 Figma의 위 기본 컴포넌트에 정의되지 않은 동작 보완입니다. Textarea의 화면별 높이는 해당 화면 원본을 확인하여 className으로 지정합니다. 이 보완 값을 Figma 원본 규격으로 간주하지 않습니다.

## 모달

```tsx
<Modal
  open={open}
  state="warning"
  title="매장 접근을 종료할까요?"
  description="해당 근무자의 매장 접근 권한이 종료됩니다."
  confirmLabel="접근 종료"
  onConfirm={saveChange}
  onClose={() => setOpen(false)}
/>
```

- onConfirm이 성공하면 onClose를 호출합니다. 실패하면 모달과 입력 상태를 유지하며 재시도를 허용합니다. 연속 클릭을 차단하고 닫힌 모달의 늦은 응답은 무시합니다. 요청 자체의 취소는 호출 화면에서 관리합니다.
- Warning과 Error는 취소/닫기를, Information은 확인 버튼을 먼저 포커스합니다. Tab/Shift+Tab은 사용 가능한 모달 버튼 안에서 순환합니다.
- Escape는 onClose를 호출하고 배경 클릭은 닫지 않습니다. 닫거나 언마운트하면 기존 포커스로 복귀합니다. 공통 Button과 SelectField는 Safari에서도 클릭 시 포커스를 확보합니다. 다른 트리거를 쓰면 열기 전에 해당 트리거를 포커스합니다.
- native dialog의 top layer와 inert 동작을 사용합니다. scrim은 390px 프레임 내부에 적용합니다. 모바일 좌우 여백은 최소 24px입니다.

## 확인 및 검증

```bash
npm ci
npm run dev -- --host 127.0.0.1 --port 5184
# http://127.0.0.1:5184/__ui
npm run lint
npm run test:ci
npm run build
```

`/__ui`는 개발 모드 전용 확인 경로입니다. production build에는 확인 페이지 코드가 포함되지 않습니다. 제품의 `/` 경로는 로그인 화면을 제공합니다.

2026-09-24 검증 결과:

- lint와 build가 통과했으며 기존 상태 화면 5개를 포함한 테스트 29개가 통과했습니다.
- Safari의 390×844, 320×568, 1440×900 뷰포트에서 중앙 정렬, 스크롤, 하단 버튼, 모달 배치를 확인했습니다.
- Figma 원본 컴포넌트의 크기·간격·색상·서체·아이콘을 대조했고 초기 화면의 전역 CSS 충돌을 수정했습니다. 시각 비교는 수동이며 자동 이미지 픽셀 diff는 수행하지 않았습니다.
- Safari에서 방향키로 radio 선택, 모달 Tab 순환, Escape 취소, 트리거 포커스 복귀를 확인했습니다. jsdom 테스트의 dialog API는 모사합니다.
- 공통 UI에는 실제 API 호출이 없습니다. 실제 iOS 키보드·safe-area·음성 입력은 해당 화면 구현 후 실기기 검증이 필요합니다.

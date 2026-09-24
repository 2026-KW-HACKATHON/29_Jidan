import { useState } from 'react'
import { AppBar } from '../ui/AppBar'
import { Button } from '../ui/Button'
import { Checkbox } from '../ui/Checkbox'
import { Choice } from '../ui/Choice'
import { InputField, SelectField, TextareaField } from '../ui/Field'
import { MobileLayout } from '../ui/MobileLayout'
import { Modal } from '../ui/Modal'
import { RegistrationProgress } from '../ui/RegistrationProgress'
import './ComponentPreview.css'

export default function ComponentPreview() {
  const [modal, setModal] = useState<'warning' | 'error' | 'information' | null>(null)
  return <MobileLayout header={<AppBar title="공통 컴포넌트 확인" onBack={() => history.back()} />} footer={<Button onClick={() => setModal('information')}>확인</Button>}>
    <div className="ds-preview">
      <section aria-label="버튼"><h2>Button / Action</h2><div className="ds-preview-row"><Button intent="secondary">취소</Button><Button intent="danger">접근 종료</Button></div><Button>확인</Button><Button disabled>비활성</Button><Button busy>처리 중</Button></section>
      <section aria-label="입력 필드"><h2>Form / Field</h2>
        <InputField label="이름" defaultValue="김지수" />
        <InputField label="빈 입력" placeholder="이름을 입력해 주세요" />
        <InputField label="이메일" value="jisu@example.com" readOnly helper="계정에서 가져온 이메일입니다." />
        <InputField label="전화번호" defaultValue="010" error="전화번호를 확인해 주세요." />
        <InputField label="비활성 입력" disabled defaultValue="수정할 수 없음" />
        <SelectField label="업종" aria-haspopup="dialog" onClick={() => setModal('information')}>카페</SelectField>
        <TextareaField label="업무 설명" placeholder="업무를 설명해 주세요" />
      </section>
      <section aria-label="선택"><h2>Form / Choice</h2>
        <fieldset><legend>경력</legend><div className="ds-preview-row"><Choice name="experience" value="new" defaultChecked>신입</Choice><Choice name="experience" value="experienced">경력</Choice><Choice name="experience" value="disabled" disabled>비활성</Choice></div></fieldset>
        <fieldset><legend>가능 요일</legend><div className="ds-preview-row">{['월', '화', '수'].map(day => <Choice key={day} type="checkbox" name="day" value={day}>{day}</Choice>)}</div></fieldset>
        <label><Checkbox />동의합니다</label><label><Checkbox defaultChecked />선택됨</label><label><Checkbox disabled />비활성</label>
      </section>
      <section aria-label="가입 진행"><h2>Registration / Progress</h2>{(['member', 'owner'] as const).map(role => ([1, 2, 3] as const).map(step => <RegistrationProgress key={`${role}-${step}`} role={role} step={step} />))}</section>
      <section aria-label="모달"><h2>Overlay / Dialog</h2><Button intent="secondary" onClick={() => setModal('warning')}>경고 모달 열기</Button><Button intent="secondary" onClick={() => setModal('error')}>오류 모달 열기</Button><Button intent="secondary" onClick={() => setModal('information')}>안내 모달 열기</Button></section>
    </div>
    <Modal open={modal !== null} state={modal || 'information'} onClose={() => setModal(null)}
      title={modal === 'warning' ? '매장 접근을 종료할까요?' : modal === 'error' ? '요청을 처리하지 못했어요' : '접근 종료가 완료됐어요'}
      description={modal === 'warning' ? '김지수 님은 이 매장의 업무 매뉴얼,\n체크리스트, AI 질의응답을 이용할 수 없어요.' : modal === 'error' ? '일시적인 연결 문제로 변경 사항이\n저장되지 않았어요. 잠시 후 다시 시도해 주세요.' : '김지수 님의 매장 접근 권한이 종료됐어요.\n변경된 상태는 근무자 목록에서 확인할 수 있어요.'}
      cancelLabel={modal === 'error' ? '닫기' : '취소'} confirmLabel={modal === 'warning' ? '접근 종료' : modal === 'error' ? '다시 시도' : '확인'} />
  </MobileLayout>
}

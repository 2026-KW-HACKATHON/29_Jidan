import {cleanup,fireEvent,render,screen} from '@testing-library/react'
import {afterEach,expect,it,vi} from 'vitest'
import {OwnerApplicantPage} from './OwnerApplicantPage'
import {ownerJobFixtures} from '../../dev/ownerJobFixtures'
afterEach(cleanup)
it('서버 대기 시간과 철회 콜백·근무 시작 후 차단을 반영한다',()=>{
 const action=vi.fn(),props={state:'WAITING' as const,job:ownerJobFixtures[0],applicant:{id:'server',name:'김서버',experience:'',introduction:''},statusText:'수락 대기 · 요청한 지 3분',onAction:action,onBack:vi.fn(),onViewApplication:vi.fn(),onOtherApplicants:vi.fn(),onOnboarding:vi.fn(),onDismissAlert:vi.fn()}
 const {rerender}=render(<OwnerApplicantPage {...props}/>);expect(screen.getByText(props.statusText)).toBeInTheDocument();fireEvent.click(screen.getByRole('button',{name:'요청 철회하기'}));expect(action).toHaveBeenCalledOnce();rerender(<OwnerApplicantPage {...props} state="CONFIRMED" actionDisabled/>);expect(screen.getByRole('button',{name:'확정 철회하기'})).toBeDisabled()
 rerender(<OwnerApplicantPage {...props} state="NO_RESPONSE" statusText={undefined} responseWindow="15분"/>);expect(screen.getByText('15분 동안 미응답')).toBeInTheDocument();expect(screen.queryByText('1시간 동안 미응답')).toBeNull()
})

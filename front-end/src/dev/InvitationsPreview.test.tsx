import { fireEvent,render,screen } from '@testing-library/react'
import { expect,it } from 'vitest'
import { mockDialog } from '../jobs/dialogTestSupport'
import InvitationsPreview from './InvitationsPreview'
mockDialog()
it('샘플 취소 결과가 지난 초대 목록에 유지된다',async()=>{history.replaceState(null,'','/__invitations');render(<InvitationsPreview/>);fireEvent.click(screen.getByRole('button',{name:'초대 취소'}));const buttons=screen.getAllByRole('button',{name:'초대 취소'});fireEvent.click(buttons[buttons.length-1]);await screen.findByRole('tab',{name:'활성 초대 0'});fireEvent.click(screen.getByRole('tab',{name:'지난 초대'}));expect(screen.getByText('초대 취소')).toBeVisible()})
it('생성 결과가 같은 미리보기의 활성 초대에 반영된다',async()=>{history.replaceState(null,'','/__invitations?view=create');render(<InvitationsPreview/>);fireEvent.change(screen.getByLabelText('초대 대상 이메일 *'),{target:{value:'new@example.com'}});fireEvent.click(screen.getByRole('button',{name:'초대 생성'}));await screen.findByText('new@example.com');expect(screen.getByRole('tab',{name:'활성 초대 2'})).toBeVisible()})

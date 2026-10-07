import {cleanup,fireEvent,render,screen} from '@testing-library/react'
import {afterEach,expect,it,vi} from 'vitest'
import {UserApplyStatusPage} from './UserApplyStatusPage'
afterEach(cleanup)
it('서버 개수와 UUID를 반영하고 빈 목록·로딩·재시도를 구분한다',()=>{
 const open=vi.fn(),retry=vi.fn(),id='f1b71e6d-789d-4415-adc3-2f06e5a34d47'
 const props={tab:'APPLYING' as const,lists:{APPLYING:[{id,title:'서버 공고',desc:'실제 매장',time:'10월 8일',badgeText:'신청 중',badgeType:'blue' as const}],CONFIRMED:[],ENDED:[]},counts:{pending:101,confirmed:0,ended:2},onTabChange:vi.fn(),onBack:vi.fn(),onOpen:open,onRetry:retry}
 const {rerender}=render(<UserApplyStatusPage {...props}/>);expect(screen.getByRole('tab',{name:'신청 중 101'})).toBeInTheDocument();fireEvent.click(screen.getByRole('button',{name:'서버 공고'}));expect(open).toHaveBeenCalledWith(id)
 rerender(<UserApplyStatusPage {...props} tab="ENDED"/>);expect(screen.getByRole('status')).toHaveTextContent('이 상태의 신청 내역이 없어요.')
 rerender(<UserApplyStatusPage {...props} tab="ENDED" loading/>);expect(screen.getByRole('status')).toHaveTextContent('불러오고 있어요')
 rerender(<UserApplyStatusPage {...props} tab="ENDED" error="연결 실패"/>);expect(screen.getByRole('alert')).toHaveTextContent('연결 실패');fireEvent.click(screen.getByRole('button',{name:'다시 시도'}));expect(retry).toHaveBeenCalledOnce()
})

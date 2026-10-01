import { fireEvent,render,screen } from '@testing-library/react'
import { expect,it } from 'vitest'
import { mockDialog } from '../jobs/dialogTestSupport'
import StoreManagementPreview from './StoreManagementPreview'
mockDialog()
it('만료 예정의 배지와 강조 만료일을 표시한다',()=>{history.replaceState(null,'','/__store/manage?view=worker&id=yujin');render(<StoreManagementPreview/>);expect(screen.getByText('만료 예정')).toBeVisible();expect(screen.getByText('2025. 07. 26')).toHaveClass('employment-expiry')})
it('접근 종료 결과가 목록으로 돌아와도 유지된다',async()=>{history.replaceState(null,'','/__store/manage?view=worker&id=jisu');render(<StoreManagementPreview/>);fireEvent.click(screen.getByRole('button',{name:'접근 종료 처리'}));fireEvent.click(screen.getByRole('button',{name:'접근 종료'}));await screen.findByRole('dialog',{name:'접근 종료가 완료됐어요'});fireEvent.click(screen.getByRole('button',{name:'확인'}));fireEvent.click(screen.getByRole('button',{name:'뒤로 가기'}));expect(screen.getByText('명랑핫도그 광운대점 · 재직 중 3명')).toBeVisible();expect(screen.getByText('접근 종료')).toBeVisible()})

import { cleanup, render, screen } from '@testing-library/react'
import { afterEach, expect, it } from 'vitest'
import Preview from './OwnerHomePreview'
afterEach(cleanup)
it('공고와 일정이 비어도 점주의 등록 매장과 상태를 유지한다', () => {
 history.replaceState(null, '', '/__home/owner?empty=1')
 render(<Preview />)
 expect(screen.getByText('명랑핫도그 광운대점')).toBeVisible()
 expect(screen.getByText('운영 중')).toBeVisible()
 expect(screen.getByRole('button', { name: '매장 관리' })).toBeVisible()
 expect(screen.getByText('모집 중인 공고가 없어요.')).toBeVisible()
 expect(screen.getByText('이 달에 일정이 없어요.')).toBeVisible()
 expect(screen.queryByText('등록된 매장이 없어요.')).not.toBeInTheDocument()
})

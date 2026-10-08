import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, expect, it, vi } from 'vitest'
import { NotificationPreviewScreen, type NotificationPreviewItem } from './NotificationPreviewScreen'

const items: readonly NotificationPreviewItem[] = [
  { id: 1, title: '새 알림', desc: '매장 안내\n근무 시간', time: '방금 전', isUnread: true },
  { id: 2, title: '읽은 알림', desc: '확인한 안내', time: '어제', isUnread: false },
]
afterEach(cleanup)

it('UUID 카드의 키보드 클릭과 처리 중 차단, 빈 목록 및 실패 안내를 제공한다', () => {
  const open = vi.fn(), id = 'aa1827b4-720f-4251-a451-2b2a2811c2f3'
  const props = { items: [{ ...items[0], id }], state: 'ALL' as const, onFilter: vi.fn(), onBack: vi.fn(), onOpen: open }
  const { rerender } = render(<NotificationPreviewScreen {...props} />)
  fireEvent.click(screen.getByRole('button', { name: '새 알림' }))
  expect(open).toHaveBeenCalledWith(id)
  rerender(<NotificationPreviewScreen {...props} busy error="다시 눌러 주세요." />)
  expect(screen.getByRole('button', { name: '새 알림' })).toBeDisabled()
  expect(screen.getByRole('alert')).toHaveTextContent('다시 눌러 주세요.')
  rerender(<NotificationPreviewScreen {...props} items={[]} />)
  expect(screen.getByRole('status')).toHaveTextContent('도착한 알림이 없어요.')
})

it('읽음 필터와 홈 복귀를 제공하고 원본 데이터를 유지한다', () => {
  const filter = vi.fn(), back = vi.fn()
  const { rerender, container } = render(<NotificationPreviewScreen items={items} state="ALL" onFilter={filter} onBack={back} />)
  expect(screen.getByText('매장 안내 근무 시간').textContent).toBe('매장 안내\n근무 시간')
  fireEvent.click(screen.getByRole('button', { name: '안읽은 알림만 보기' }))
  expect(filter).toHaveBeenCalledWith('UNREAD')
  rerender(<NotificationPreviewScreen items={items} state="UNREAD" onFilter={filter} onBack={back} />)
  expect(screen.getByText('안 읽은 알림 1개')).toBeInTheDocument()
  expect(screen.queryByText('읽은 알림')).not.toBeInTheDocument()
  fireEvent.click(screen.getByRole('button', { name: '전체 알림 보기' }))
  expect(filter).toHaveBeenLastCalledWith('ALL')
  rerender(<NotificationPreviewScreen items={items} state="READ_ALL" onFilter={filter} onBack={back} />)
  expect(container.querySelectorAll('.notification-card')).toHaveLength(2)
  expect(screen.queryByText('안 읽음')).not.toBeInTheDocument()
  expect(items[0].isUnread).toBe(true)
  fireEvent.click(screen.getByRole('button', { name: '뒤로 가기' }))
  expect(back).toHaveBeenCalledOnce()
})

it.each([{ list: [] }, { list: items.filter(item => !item.isUnread) }])('안 읽은 알림이 없는 목록에서도 필터를 해제할 수 있다', ({ list }) => {
  const filter = vi.fn()
  render(<NotificationPreviewScreen items={list} state="UNREAD" onFilter={filter} onBack={vi.fn()} />)
  expect(screen.getByText('안 읽은 알림 0개')).toBeInTheDocument()
  fireEvent.click(screen.getByRole('button', { name: '전체 알림 보기' }))
  expect(filter).toHaveBeenCalledWith('ALL')
})

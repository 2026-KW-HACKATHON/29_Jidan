import { act, cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { afterEach, beforeAll, beforeEach, expect, it, vi } from 'vitest'
import PreviewApp from './PreviewApp'

beforeAll(async () => { await Promise.all([import('./UserNotificationPreview'), import('./WorkerHomePreview')]) })
beforeEach(() => { vi.stubGlobal('fetch', vi.fn()) })
afterEach(() => { cleanup(); vi.unstubAllGlobals() })

it.each([
  ['/__user/noti', '전체 알림', 4, 3],
  ['/__user/noti/unread', '안 읽은 알림 3개', 3, 3],
  ['/__user/noti/read-all', '모두 읽었어요', 4, 0],
  ['/__user/noti?case=unread', '전체 알림', 4, 3],
])('일반회원 알림 직접 진입 %s에서 경로 상태와 목록을 표시한다', async (url, title, cards, unread) => {
  history.replaceState(null, '', url)
  const { container } = render(<PreviewApp />)
  await screen.findByText(title)
  expect(container.querySelectorAll('.notification-card')).toHaveLength(cards as number)
  expect(container.querySelectorAll('.unread-badge')).toHaveLength(unread as number)
  expect(fetch).not.toHaveBeenCalled()
})

it('일반회원 홈 진입·필터·브라우저 뒤로·앞으로·홈 복귀를 문서 이동 없이 처리한다', async () => {
  history.replaceState(null, '', '/__home/worker')
  render(<PreviewApp />)
  fireEvent.click(await screen.findByRole('button', { name: '알림' }))
  await screen.findByText('전체 알림')
  expect(location.pathname).toBe('/__user/noti')
  const sidebar = screen.getByRole('navigation', { name: '미리보기 화면 목록' })
  fireEvent.click(screen.getByRole('button', { name: '안읽은 알림만 보기' }))
  await screen.findByText('안 읽은 알림 3개')
  expect(location.pathname).toBe('/__user/noti/unread')
  expect(screen.getByRole('navigation', { name: '미리보기 화면 목록' })).toBe(sidebar)
  await act(async () => history.back())
  await screen.findByText('전체 알림')
  await act(async () => history.forward())
  await screen.findByText('안 읽은 알림 3개')
  fireEvent.click(screen.getByRole('button', { name: '전체 알림 보기' }))
  await screen.findByText('전체 알림')
  expect(location.pathname).toBe('/__user/noti')
  fireEvent.click(screen.getByRole('button', { name: '뒤로 가기' }))
  await waitFor(() => expect(location.pathname).toBe('/__home/worker'))
  await screen.findByRole('button', { name: '알림' })
  expect(fetch).not.toHaveBeenCalled()
})

it.each(['/__user/noti/unread-extra', '/__user/noti/unknown'])('잘못된 세부 경로 %s를 알림으로 오인하지 않는다', async url => {
  history.replaceState(null, '', url)
  render(<PreviewApp />)
  await screen.findByRole('heading', { name: '화면 탐색' })
  expect(screen.queryByText('안 읽은 알림 3개')).not.toBeInTheDocument()
  const menu = within(screen.getByRole('navigation', { name: '미리보기 화면 목록' }))
  expect(menu.getByRole('link', { name: '일반회원 전체 알림' })).toHaveAttribute('href', '/__user/noti')
})

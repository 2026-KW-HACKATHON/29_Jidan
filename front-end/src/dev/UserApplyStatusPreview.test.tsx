import { act, cleanup, fireEvent, render, screen, within } from '@testing-library/react'
import { afterEach, beforeAll, beforeEach, expect, it, vi } from 'vitest'
import PreviewApp from './PreviewApp'

beforeAll(async () => { await Promise.all([import('./UserApplyStatusPreview'), import('./WorkerHomePreview')]) })
beforeEach(() => { vi.stubGlobal('fetch', vi.fn()) })
afterEach(() => { cleanup(); vi.unstubAllGlobals() })

it.each([
  ['/__user/status', '주말 오픈 대타', 3],
  ['/__user/status/confirmed', '주말 마감 대타', 1],
  ['/__user/status/ended', '오전 매장 정리', 2],
  ['/__user/status?tab=confirmed', '주말 오픈 대타', 3],
])('신청 공고 직접 진입 %s의 탭 상태를 표시한다', async (url, title, count) => {
  history.replaceState(null, '', url)
  const { container } = render(<PreviewApp />)
  await screen.findByText(title)
  expect(container.querySelectorAll('.application-card')).toHaveLength(count as number)
  expect(fetch).not.toHaveBeenCalled()
})

it('홈의 신청 공고에서 탭·동일 탭·브라우저 이력·홈 복귀를 처리한다', async () => {
  history.replaceState(null, '', '/__home/worker')
  render(<PreviewApp />)
  fireEvent.click(await screen.findByRole('button', { name: '신청 중 공고' }))
  await screen.findByText('주말 오픈 대타')
  expect(location.pathname).toBe('/__user/status')
  fireEvent.click(screen.getByRole('button', { name: '확정 1' }))
  await screen.findByText('주말 마감 대타')
  expect(location.pathname).toBe('/__user/status/confirmed')
  const length = history.length
  fireEvent.click(screen.getByRole('button', { name: '확정 1' }))
  expect(history.length).toBe(length)
  fireEvent.click(screen.getByRole('button', { name: '종료 2' }))
  await screen.findByText('미선정')
  expect(location.pathname).toBe('/__user/status/ended')
  await act(async () => history.back())
  await screen.findByText('주말 마감 대타')
  await act(async () => history.forward())
  await screen.findByText('미선정')
  fireEvent.click(screen.getByRole('button', { name: '신청 중 3' }))
  await screen.findByText('주말 오픈 대타')
  fireEvent.click(screen.getByRole('button', { name: '뒤로 가기' }))
  await screen.findByRole('button', { name: '신청 중 공고' })
  expect(location.pathname).toBe('/__home/worker')
  expect(fetch).not.toHaveBeenCalled()
})

it.each(['/__user/status/confirmed-extra', '/__user/status/ended/unknown'])('잘못된 신청 공고 경로 %s를 탭으로 오인하지 않는다', async url => {
  history.replaceState(null, '', url)
  render(<PreviewApp />)
  await screen.findByRole('heading', { name: '화면 탐색' })
  expect(screen.queryByText('신청 내역을 확인하세요')).not.toBeInTheDocument()
  const menu = within(screen.getByRole('navigation', { name: '미리보기 화면 목록' }))
  expect(menu.getByRole('link', { name: '확정 공고 조회' })).toHaveAttribute('href', '/__user/status/confirmed')
})

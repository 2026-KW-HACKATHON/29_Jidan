import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeAll, beforeEach, expect, it, vi } from 'vitest'
import PreviewApp from './PreviewApp'

beforeAll(async () => { await Promise.all([import('./OwnerApplicantPreview'), import('./OwnerJobsPreview')]) })
beforeEach(() => {
  vi.stubGlobal('fetch', vi.fn())
  Object.defineProperty(HTMLDialogElement.prototype, 'showModal', { configurable: true, value: function () { this.setAttribute('open', '') } })
  Object.defineProperty(HTMLDialogElement.prototype, 'close', { configurable: true, value: function () { this.removeAttribute('open') } })
})
afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
  Reflect.deleteProperty(HTMLDialogElement.prototype, 'showModal')
  Reflect.deleteProperty(HTMLDialogElement.prototype, 'close')
})

it('미응답 안내를 Escape로 닫아도 미응답 상태 화면으로 복귀한다', async () => {
  history.replaceState(null, '', '/__owner/applicant/no-response/alert')
  render(<PreviewApp />)
  fireEvent(await screen.findByRole('dialog'), new Event('cancel', { cancelable: true }))
  await waitFor(() => expect(location.pathname).toBe('/__owner/applicant/no-response'))
  expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
  expect(screen.getByText('1시간 동안 미응답')).toBeInTheDocument()
  expect(fetch).not.toHaveBeenCalled()
})

it.each([
  ['/__owner/applicant', '수락 대기 · 요청한 지 20분'],
  ['/__owner/applicant/no-response', '1시간 동안 미응답'],
  ['/__owner/applicant/confirmed', '근무 확정'],
  ['/__owner/applicant?case=confirmed', '수락 대기 · 요청한 지 20분'],
])('지원자 직접 진입 %s의 상태를 표시한다', async (url, status) => {
  history.replaceState(null, '', url)
  render(<PreviewApp />)
  await screen.findByText(status)
  expect(fetch).not.toHaveBeenCalled()
})

it.each(['/__owner/applicant', '/__owner/applicant/confirmed'])('지원서 보기 %s는 박지원 열람 전용 지원서와 복귀를 연결한다', async path => {
  history.replaceState(null, '', path)
  render(<PreviewApp />)
  fireEvent.click(await screen.findByRole('button', { name: '지원서 보기' }))
  await screen.findByText('자기소개')
  expect(location.pathname + location.search).toBe(`${path}?view=application`)
  expect(screen.queryByRole('button', { name: '근무 요청 보내기' })).not.toBeInTheDocument()
  expect(screen.getByText('박지원')).toBeInTheDocument()
  fireEvent.click(screen.getByRole('button', { name: '닫기' }))
  await screen.findByRole('button', { name: '지원서 보기' })
  expect(location.search).toBe('')
  expect(fetch).not.toHaveBeenCalled()
})

it('확정된 지원자의 온보딩은 샘플 근무 안내와 브라우저 이력·복귀를 연결한다', async () => {
  history.replaceState(null, '', '/__owner/applicant/confirmed')
  render(<PreviewApp />)
  fireEvent.click(await screen.findByRole('button', { name: '온보딩 보기' }))
  await screen.findByRole('heading', { name: '매장 업무 안내' })
  expect(location.search).toBe('?view=onboarding')
  expect(screen.queryByText('근무 요청을 보낼까요?')).not.toBeInTheDocument()
  expect(screen.queryByText('김래원님에게 근무 요청을 보내요.')).not.toBeInTheDocument()
  await act(async () => history.back())
  await screen.findByRole('button', { name: '온보딩 보기' })
  await act(async () => history.forward())
  await screen.findByRole('heading', { name: '매장 업무 안내' })
  fireEvent.click(screen.getByRole('button', { name: '뒤로 가기' }))
  await screen.findByRole('button', { name: '온보딩 보기' })
  expect(location.pathname + location.search).toBe('/__owner/applicant/confirmed')
  expect(fetch).not.toHaveBeenCalled()
})

it.each(['/__owner/applicant?view=onboarding', '/__owner/applicant/no-response?view=onboarding'])('확정되지 않은 경로 %s로 온보딩을 잘못 열지 않는다', async url => {
  history.replaceState(null, '', url)
  render(<PreviewApp />)
  await screen.findByRole('button', { name: '지원서 보기' })
  expect(screen.queryByRole('heading', { name: '매장 업무 안내' })).not.toBeInTheDocument()
})

it.each([
  ['/__owner/applicant', '뒤로 가기'],
  ['/__owner/applicant/no-response', '다른 지원자 보기'],
  ['/__owner/applicant/no-response/alert', '지원자 확인'],
])('지원자 상태 %s에서 지원자 목록으로 이동한다', async (url, action) => {
  history.replaceState(null, '', url)
  render(<PreviewApp />)
  fireEvent.click(await screen.findByRole('button', { name: action }))
  await waitFor(() => expect(location.pathname + location.search).toBe('/__owner/jobs?view=applicants&id=open'))
  await screen.findByRole('button', { name: '박지원 지원서 보기' })
  expect(fetch).not.toHaveBeenCalled()
})

it('미응답 안내의 내부 클릭은 유지하고 배경을 닫으면 상태 경로도 함께 변경한다', async () => {
  history.replaceState(null, '', '/__owner/applicant/no-response/alert')
  render(<PreviewApp />)
  const dialog = await screen.findByRole('dialog')
  vi.spyOn(dialog, 'getBoundingClientRect').mockReturnValue(new DOMRect(24, 300, 342, 207))
  fireEvent.click(dialog, { clientX: 30, clientY: 310 })
  expect(location.pathname).toBe('/__owner/applicant/no-response/alert')
  fireEvent.click(dialog, { clientX: 10, clientY: 310 })
  await waitFor(() => expect(location.pathname).toBe('/__owner/applicant/no-response'))
  expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
})

it.each(['/__owner/applicant/confirmed-extra', '/__owner/applicant/alert'])('잘못된 지원자 경로 %s를 상태나 알림으로 오인하지 않는다', async url => {
  history.replaceState(null, '', url)
  render(<PreviewApp />)
  await screen.findByRole('heading', { name: '화면 탐색' })
  expect(screen.queryByRole('button', { name: '온보딩 보기' })).not.toBeInTheDocument()
  expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
})

import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { AuthFlow, type AuthPath } from './AuthFlow'
import { SSO_START_PATH } from './session'

const me = { id: '00000000-0000-4000-8000-000000000001', displayName: '김근무', accountType: 'WORKER', sessionExpiresAt: '2099-01-01T00:00:00Z', csrfToken: 'test-csrf' }
const renderHome = vi.fn(() => <p>홈 진입점</p>)
const renderRegistration = vi.fn(role => <p>{role} 가입 진입점</p>)
const anonymous = () => new Response(null, { status: 401 })
const original = Object.getOwnPropertyDescriptors(HTMLDialogElement.prototype)
beforeEach(() => {
  document.cookie = 'signup_csrf=; Max-Age=0; Path=/'
  vi.clearAllMocks()
  Object.defineProperty(HTMLDialogElement.prototype, 'showModal', { configurable: true, value(this: HTMLDialogElement) { this.setAttribute('open', '') } })
  Object.defineProperty(HTMLDialogElement.prototype, 'close', { configurable: true, value(this: HTMLDialogElement) { this.removeAttribute('open') } })
})
afterEach(() => {
  cleanup()
  for (const key of ['showModal', 'close']) {
    if (original[key]) Object.defineProperty(HTMLDialogElement.prototype, key, original[key])
    else Reflect.deleteProperty(HTMLDialogElement.prototype, key)
  }
  document.cookie = 'signup_csrf=; Max-Age=0; Path=/'
})
function setup(path: AuthPath, search = '') {
  const navigate = vi.fn()
  const startSso = vi.fn()
  const view = render(<AuthFlow path={path} search={search} navigate={navigate} startSso={startSso} renderHome={renderHome} renderRegistration={renderRegistration} />)
  return { ...view, navigate, startSso }
}

describe('AuthFlow', () => {
  it('rechecks the session then redirects once to the backend SSO entry', async () => {
    vi.stubGlobal('fetch', vi.fn().mockImplementation(async () => anonymous()))
    const { startSso } = setup('/')
    const button = screen.getByRole('button', { name: 'SSO 계정으로 시작하기' })
    await waitFor(() => expect(button).toBeEnabled())
    fireEvent.click(button)
    fireEvent.click(button)
    await waitFor(() => expect(startSso).toHaveBeenCalledExactlyOnceWith(SSO_START_PATH))
    expect(button).toBeDisabled()
  })
  it.each(['OWNER', 'WORKER'])('routes an existing %s to home rather than signup', async accountType => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(JSON.stringify({ data: { ...me, accountType } }))))
    const { navigate } = setup('/signup')
    await waitFor(() => expect(navigate).toHaveBeenCalledWith('/home', true))
    expect(renderRegistration).not.toHaveBeenCalled()
  })
  it('renders verified session data at the home integration point', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(JSON.stringify({ data: me }))))
    setup('/home')
    expect(await screen.findByText('홈 진입점')).toBeInTheDocument()
    expect(renderHome).toHaveBeenCalledWith(me)
  })
  it('allows a new signup hint to choose either future signup path and return to login', async () => {
    vi.stubGlobal('fetch', vi.fn().mockImplementation(async () => anonymous()))
    document.cookie = 'signup_csrf=hint; Path=/'
    const { navigate } = setup('/signup')
    fireEvent.click(await screen.findByRole('button', { name: '점주로 가입' }))
    expect(navigate).toHaveBeenCalledWith('/signup/owner')
    fireEvent.click(screen.getByRole('button', { name: '일반회원으로 가입' }))
    expect(navigate).toHaveBeenCalledWith('/signup/worker')
    fireEvent.click(screen.getByRole('button', { name: '뒤로 가기' }))
    expect(navigate).toHaveBeenCalledWith('/login')
    expect(renderHome).not.toHaveBeenCalled()
  })
  it.each(['/signup', '/signup/owner', '/signup/worker'] as const)('rejects direct %s entry without a signup hint', async path => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(anonymous()))
    const { navigate } = setup(path)
    await waitFor(() => expect(navigate).toHaveBeenCalledWith('/login?error=signup_expired', true))
    expect(renderRegistration).not.toHaveBeenCalled()
  })
  it('does not derive a role from query parameters', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(anonymous()))
    const { navigate } = setup('/home', '?role=OWNER')
    await waitFor(() => expect(navigate).toHaveBeenCalledWith('/login', true))
    expect(renderHome).not.toHaveBeenCalled()
  })
  it('keeps a missing API as a retryable failure, without pretending login succeeded', async () => {
    vi.stubGlobal('fetch', vi.fn().mockImplementation(async () => new Response(null, { status: 404 })))
    const { startSso, navigate } = setup('/')
    const button = screen.getByRole('button', { name: 'SSO 계정으로 시작하기' })
    await waitFor(() => expect(button).toBeEnabled())
    fireEvent.click(button)
    expect(await screen.findByRole('alertdialog')).toHaveAccessibleName('로그인에 연결하지 못했어요')
    expect(startSso).not.toHaveBeenCalled()
    expect(navigate).not.toHaveBeenCalled()
    fireEvent.click(screen.getByRole('button', { name: '다시 시도' }))
    await waitFor(() => expect(fetch).toHaveBeenCalledTimes(3))
    expect(await screen.findByRole('alertdialog')).toHaveAccessibleName('로그인에 연결하지 못했어요')
    fireEvent.click(screen.getByRole('button', { name: '닫기' }))
    expect(button).toHaveFocus()
  })
  it('shows a safe cancellation without reflecting the provider text', () => {
    vi.stubGlobal('fetch', vi.fn())
    const { startSso } = setup('/login', '?error=access_denied&error_description=private-provider-message')
    expect(screen.getByRole('dialog')).toHaveAccessibleName('로그인이 취소됐어요')
    expect(screen.queryByText('private-provider-message')).not.toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: '확인' }))
    expect(startSso).not.toHaveBeenCalled()
  })
  it('ignores an initial session result after unmount', async () => {
    let resolve!: (response: Response) => void
    vi.stubGlobal('fetch', vi.fn(() => new Promise<Response>(done => { resolve = done })))
    const { unmount, navigate } = setup('/')
    unmount()
    await act(async () => resolve(new Response(JSON.stringify({ data: me }))))
    expect(navigate).not.toHaveBeenCalled()
  })
})

import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import App from './App'

// jsdom has no top layer. Focus trapping must additionally be checked in a browser.
const original = Object.getOwnPropertyDescriptors(HTMLDialogElement.prototype)
beforeEach(() => {
  Object.defineProperty(HTMLDialogElement.prototype, 'showModal', { configurable: true, value(this: HTMLDialogElement) { this.setAttribute('open', '') } })
  Object.defineProperty(HTMLDialogElement.prototype, 'close', { configurable: true, value(this: HTMLDialogElement) { this.removeAttribute('open') } })
})
afterEach(() => {
  cleanup()
  for (const key of ['showModal', 'close']) {
    if (original[key]) Object.defineProperty(HTMLDialogElement.prototype, key, original[key])
    else Reflect.deleteProperty(HTMLDialogElement.prototype, key)
  }
})

beforeEach(() => {
  window.history.replaceState(null, '', '/')
  document.cookie = 'signup_csrf=; Max-Age=0; Path=/'
  vi.stubGlobal('fetch', vi.fn(async () => new Response(null, { status: 401 })))
})
afterEach(() => { window.history.replaceState(null, '', '/'); document.cookie = 'signup_csrf=; Max-Age=0; Path=/' })
it('connects signup roles to distinct entry paths and back without creating an account', async () => {
  document.cookie = 'signup_csrf=hint; Path=/'
  window.history.replaceState(null, '', '/signup')
  render(<App />)
  fireEvent.click(await screen.findByRole('button', { name: '점주로 가입' }))
  expect(await screen.findByRole('heading', { name: '점주 가입' })).toBeInTheDocument()
  expect(window.location.pathname).toBe('/signup/owner')
  fireEvent.click(screen.getByRole('button', { name: '뒤로 가기' }))
  fireEvent.click(await screen.findByRole('button', { name: '일반회원으로 가입' }))
  expect(await screen.findByRole('heading', { name: '프로필 등록' })).toBeInTheDocument()
  expect(window.location.pathname).toBe('/signup/worker')
  expect(vi.mocked(fetch).mock.calls.every(([url]) => url === '/api/me')).toBe(true)
})
it('refreshes session state on browser back and bfcache restoration', async () => {
  render(<App />)
  await waitFor(() => expect(fetch).toHaveBeenCalledTimes(1))
  act(() => { window.history.replaceState(null, '', '/login'); window.dispatchEvent(new PopStateEvent('popstate')) })
  await waitFor(() => expect(fetch).toHaveBeenCalledTimes(2))
  act(() => { window.dispatchEvent(new PageTransitionEvent('pageshow', { persisted: true })) })
  await waitFor(() => expect(fetch).toHaveBeenCalledTimes(3))
})
it('does not route arbitrary paths into an authenticated area', () => {
  window.history.replaceState(null, '', '/unknown')
  render(<App />)
  expect(screen.getByRole('heading')).toHaveTextContent('페이지를 찾을 수 없어요')
  expect(fetch).not.toHaveBeenCalled()
})

it('routes an authenticated owner to the owner home without preview store data', async () => {
  window.history.replaceState(null, '', '/home')
  vi.stubGlobal('fetch', vi.fn(async () => new Response(JSON.stringify({ data: {
    id: '123e4567-e89b-12d3-a456-426614174000', displayName: '이하늘', accountType: 'OWNER',
    sessionExpiresAt: new Date(Date.now() + 60_000).toISOString(), csrfToken: 'token',
  } }), { status: 200, headers: { 'Content-Type': 'application/json' } })))
  render(<App />)
  expect(await screen.findByRole('heading', { name: '안녕하세요, 이하늘 점주님' })).toBeInTheDocument()
  expect(screen.getByText('등록된 매장이 없어요.')).toBeInTheDocument()
  expect(screen.queryByText('명랑핫도그 광운대점')).not.toBeInTheDocument()
})

it('routes an authenticated general member to the member home', async () => {
  window.history.replaceState(null, '', '/home')
  vi.stubGlobal('fetch', vi.fn(async () => new Response(JSON.stringify({ data: {
    id: '123e4567-e89b-12d3-a456-426614174000', displayName: '김지수', accountType: 'WORKER',
    sessionExpiresAt: new Date(Date.now() + 60_000).toISOString(), csrfToken: 'token',
  } }), { status: 200, headers: { 'Content-Type': 'application/json' } })))
  render(<App />)
  expect(await screen.findByRole('heading', { name: '안녕하세요, 김지수님' })).toBeInTheDocument()
  expect(screen.getByRole('heading', { name: '추천 공고' })).toBeInTheDocument()
  expect(screen.queryByText('명랑핫도그 광운대점')).not.toBeInTheDocument()
})

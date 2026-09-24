import { act, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import App from './App'

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
  expect(await screen.findByRole('heading', { name: '일반회원 가입' })).toBeInTheDocument()
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

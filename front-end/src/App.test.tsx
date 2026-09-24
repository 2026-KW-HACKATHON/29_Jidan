import { fireEvent, render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import App from './App'

const healthy = { status: 'ok', environment: 'dev', database: 'ok' }
function response(body: unknown = healthy, ok = true) {
  return { ok, json: async () => body }
}

describe('initialization status', () => {
  it('shows loading before the response', () => {
    vi.stubGlobal('fetch', vi.fn(() => new Promise(() => {})))
    render(<App />)
    expect(screen.getByText('연결 확인 중…')).toBeInTheDocument()
    expect(screen.getByRole('button')).toBeDisabled()
  })
  it('uses the same-origin API and displays the environment', async () => {
    const fetch = vi.fn().mockResolvedValue(response())
    vi.stubGlobal('fetch', fetch)
    render(<App />)
    expect(await screen.findByText('API 연결 정상')).toBeInTheDocument()
    expect(screen.getByText('dev')).toBeInTheDocument()
    expect(fetch).toHaveBeenCalledWith('/api/health', expect.objectContaining({ signal: expect.anything() }))
  })
  it.each([response({}, true), response(healthy, false)])('rejects invalid or failed responses', async (result) => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(result))
    render(<App />)
    expect(await screen.findByRole('alert')).toBeInTheDocument()
  })
  it('can retry after a network failure', async () => {
    vi.stubGlobal('fetch', vi.fn().mockRejectedValueOnce(new Error('network')).mockResolvedValueOnce(response()))
    render(<App />)
    await screen.findByRole('alert')
    fireEvent.click(screen.getByRole('button'))
    expect(await screen.findByText('API 연결 정상')).toBeInTheDocument()
  })
})

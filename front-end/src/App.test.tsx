import { fireEvent, render, screen, within } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import App from './App'
import { createDemoData, STORAGE_KEY } from './state'
import type { AppData } from './state'

function memoryStorage() {
  const values = new Map<string, string>()
  return {
    getItem: (key: string) => values.get(key) ?? null,
    setItem: (key: string, value: string) => { values.set(key, value) },
    removeItem: (key: string) => { values.delete(key) },
    clear: () => values.clear(),
  }
}

function openApp(path = '/', data: AppData = createDemoData()) {
  localStorage.setItem(STORAGE_KEY, JSON.stringify(data))
  window.history.replaceState(null, '', `/#${path}`)
  return render(<App />)
}

beforeEach(() => {
  vi.stubGlobal('localStorage', memoryStorage())
  vi.stubGlobal('sessionStorage', memoryStorage())
  vi.spyOn(window, 'scrollTo').mockImplementation(() => {})
})
afterEach(() => { vi.restoreAllMocks() })

describe('connected prototype navigation', () => {
  it('starts with login, selects the owner role, and opens the owner home', async () => {
    openApp()
    fireEvent.click(screen.getByRole('button', { name: 'SSO 계정으로 시작하기' }))
    expect(await screen.findByRole('heading', { name: /어떤 역할로/ })).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: '점주로 가입' }))
    expect(await screen.findByRole('heading', { name: '안녕하세요, 김민지 점주님' })).toBeInTheDocument()
    expect(within(screen.getByRole('main')).getByRole('button', { name: '매장 관리' })).toBeInTheDocument()
    expect(screen.getByRole('navigation', { name: '주요 메뉴' })).toBeInTheDocument()
    expect(JSON.parse(localStorage.getItem(STORAGE_KEY)!).role).toBe('owner')
  })

  it('carries member signup input through review, correction, completion, and home', async () => {
    openApp('/roles')
    fireEvent.click(screen.getByRole('button', { name: '일반회원으로 가입' }))
    expect(await screen.findByRole('heading', { name: '반가워요! 기본 정보를 알려주세요' })).toBeInTheDocument()
    fireEvent.change(screen.getByLabelText('이름 *'), { target: { value: '이수연' } })
    fireEvent.click(screen.getByRole('button', { name: '다음' }))
    expect(await screen.findByRole('heading', { name: '어떤 일을 해보셨나요?' })).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: '신입' }))
    fireEvent.click(screen.getByRole('button', { name: '다음' }))
    expect(await screen.findByRole('heading', { name: '마지막 단계예요!' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: '월요일 09:00–09:30' })).toHaveAttribute('aria-pressed', 'true')
    fireEvent.click(screen.getByRole('button', { name: '입력 내용 확인' }))
    expect(await screen.findByRole('heading', { name: '프로필을 확인해 주세요' })).toBeInTheDocument()
    expect(within(screen.getByRole('main')).getByText('이수연')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: '기본 정보 수정' }))
    expect(await screen.findByLabelText('이름 *')).toHaveValue('이수연')
    fireEvent.change(screen.getByLabelText('이름 *'), { target: { value: '이수연 수정' } })
    fireEvent.click(screen.getByRole('button', { name: '다음' }))
    expect(await screen.findByRole('heading', { name: '프로필을 확인해 주세요' })).toBeInTheDocument()
    expect(within(screen.getByRole('main')).getByText('이수연 수정')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: '프로필 등록 완료' }))
    expect(await screen.findByRole('heading', { name: '프로필 등록이 완료됐어요' })).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: '지단 시작하기' }))
    expect(await screen.findByRole('heading', { name: '안녕하세요, 이수연 수정님' })).toBeInTheDocument()
    expect(screen.getByRole('link', { name: '홈' })).toHaveAttribute('aria-current', 'page')
    expect(JSON.parse(localStorage.getItem(STORAGE_KEY)!).profile).toMatchObject({ name: '이수연 수정', experience: 'new' })
  })

  it.each(['/store', '/invite', '/workers/worker-1', '/jobs/new', '/manuals/new', '/manuals/manual-1/edit', '/manuals/manual-1/review'])(
    'blocks a member from opening the owner-only route %s', async path => {
      openApp(path, { ...createDemoData(), role: 'member' })
      expect(await screen.findByRole('heading', { name: '점주 전용 화면이에요' })).toBeInTheDocument()
      expect(within(screen.getByRole('main')).queryByRole('textbox')).not.toBeInTheDocument()
      fireEvent.click(screen.getByRole('button', { name: '홈으로' }))
      expect(await screen.findByRole('heading', { name: '안녕하세요, 김지수님' })).toBeInTheDocument()
    },
  )

  it('offers recovery from an unknown route and returns to the correct role home', async () => {
    openApp('/missing-page', { ...createDemoData(), role: 'owner' })
    expect(await screen.findByRole('heading', { name: '페이지를 찾을 수 없어요' })).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: '홈으로 돌아가기' }))
    expect(await screen.findByRole('heading', { name: '안녕하세요, 김민지 점주님' })).toBeInTheDocument()
    expect(window.location.hash).toBe('#/home')
  })
})

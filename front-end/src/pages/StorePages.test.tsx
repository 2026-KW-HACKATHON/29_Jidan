import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { AppProvider, createDemoData, STORAGE_KEY, type AppData } from '../state'
import { StorePages } from './StorePages'

function open(route: string, change?: (data: AppData) => void) {
  const data = createDemoData()
  data.role = 'owner'
  change?.(data)
  localStorage.setItem(STORAGE_KEY, JSON.stringify(data))
  render(<AppProvider><StorePages route={route} /></AppProvider>)
  return data
}
const saved = () => JSON.parse(localStorage.getItem(STORAGE_KEY)!) as AppData
beforeEach(() => {
  const values = new Map<string, string>()
  vi.stubGlobal('localStorage', {
    getItem: (key: string) => values.get(key) ?? null,
    setItem: (key: string, value: string) => { values.set(key, value) },
    removeItem: (key: string) => { values.delete(key) },
    clear: () => values.clear(),
  })
  window.location.hash = ''
})

describe('store and worker prototype flows', () => {
  it('blocks invitation acceptance from a different email account', () => {
    open('/invitation/invite-demo', data => { data.profile.email = 'another@example.com' })
    expect(screen.getByRole('button', { name: '초대 수락' })).toBeDisabled()
    expect(screen.getByRole('button', { name: '거절' })).toBeDisabled()
    expect(screen.getByRole('alert')).toHaveTextContent('초대받은 이메일 계정으로 로그인')
    expect(saved().invites[0].status).toBe('pending')
  })

  it('accepts the invitation and adds its task access without replacing an existing relation', async () => {
    const initial = open('/invitation/invite-demo')
    fireEvent.click(screen.getByRole('button', { name: '초대 수락' }))
    await waitFor(() => expect(saved().invites[0].status).toBe('accepted'))
    const data = saved()
    expect(data.role).toBe('member')
    expect(data.workers).toHaveLength(initial.workers.length + 1)
    expect(data.workers.find(worker => worker.id === 'worker-1')).toEqual(initial.workers[0])
    expect(data.workers.find(worker => worker.id === 'worker-invite-demo')).toMatchObject({ name: '김지수', email: 'jisu@example.com', task: '매장 마감', status: 'active' })
    expect(window.location.hash).toBe('#/manuals')
  })

  it('rejects an expired invitation without granting access', () => {
    open('/invitation/invite-demo', data => { data.invites[0].createdAt = new Date(Date.now() - 8 * 86400000).toISOString() })
    expect(screen.getByText('이 초대는 사용할 수 없어요')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: '초대 수락' })).not.toBeInTheDocument()
    expect(saved().workers).toHaveLength(4)
  })

  it('creates a reusable invitation and prevents a duplicate pending invitation', async () => {
    open('/invite')
    fireEvent.change(screen.getByLabelText('초대 대상 이메일 *'), { target: { value: 'new@example.com' } })
    fireEvent.click(screen.getByRole('button', { name: '초대 생성' }))
    await waitFor(() => expect(saved().invites).toHaveLength(2))
    expect(screen.getByRole('dialog')).toHaveTextContent('프로토타입에서는 이메일을 보내지 않아요')
    fireEvent.click(screen.getByRole('button', { name: '닫기' }))
    fireEvent.change(screen.getByLabelText('초대 대상 이메일 *'), { target: { value: 'new@example.com' } })
    fireEvent.click(screen.getByRole('button', { name: '초대 생성' }))
    expect(screen.getByRole('alert')).toHaveTextContent('같은 업무의 대기 중인 초대')
    expect(saved().invites).toHaveLength(2)
  })

  it('revokes only the selected worker relation after confirmation', async () => {
    const initial = open('/workers/worker-1')
    fireEvent.click(screen.getByRole('button', { name: '접근 종료 처리' }))
    expect(saved().workers[0].status).toBe('active')
    fireEvent.click(screen.getByRole('button', { name: '접근 종료' }))
    await waitFor(() => expect(saved().workers[0].status).toBe('ended'))
    expect(saved().workers.slice(1)).toEqual(initial.workers.slice(1))
    expect(screen.getByRole('button', { name: '접근이 종료되었어요' })).toBeDisabled()
  })

  it('requires confirmation before replacing store data, then leaves the new store pending', async () => {
    const initial = open('/store/new')
    fireEvent.change(screen.getByLabelText('매장명 *'), { target: { value: '월계 작은 카페' } })
    fireEvent.change(screen.getByLabelText('점주 성명 *'), { target: { value: '김민지' } })
    fireEvent.change(screen.getByRole('combobox'), { target: { value: '카페' } })
    fireEvent.click(screen.getByRole('button', { name: '주소 검색' }))
    fireEvent.click(screen.getByRole('button', { name: /서울특별시 노원구 광운로 20/ }))
    fireEvent.change(screen.getByLabelText('사업자 번호 *'), { target: { value: '123-45-67890' } })
    fireEvent.change(screen.getByLabelText('연락처 *'), { target: { value: '010-1234-5678' } })
    fireEvent.click(screen.getByRole('button', { name: '매장 등록' }))
    expect(screen.getByRole('dialog')).toHaveTextContent('새 매장으로 전환할까요?')
    expect(saved().store).toEqual(initial.store)
    fireEvent.click(screen.getByRole('button', { name: '새 매장 등록' }))
    await waitFor(() => expect(saved().store.status).toBe('pending'))
    expect(saved().store.name).toBe('월계 작은 카페')
    expect(saved().workers).toEqual([])
    expect(saved().invites).toEqual([])
    expect(saved().manuals).toEqual([])
    expect(saved().jobs).toEqual([])
    expect(window.location.hash).toBe('#/store')
  })
})

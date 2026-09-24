import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { AppProvider, createDemoData, STORAGE_KEY, type AppData } from '../state'
import { ManualPages } from './ManualPages'

const png = 'data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+lmN8AAAAASUVORK5CYII='

function seed(role: 'owner' | 'member' = 'owner') {
  const data = createDemoData()
  data.role = role
  return data
}

function saved(): AppData {
  return JSON.parse(localStorage.getItem(STORAGE_KEY) || '{}') as AppData
}

function mount(data: AppData, route: string) {
  localStorage.setItem(STORAGE_KEY, JSON.stringify(data))
  const view = render(<AppProvider><ManualPages route={route} /></AppProvider>)
  return { ...view, route: (path: string) => view.rerender(<AppProvider><ManualPages route={path} /></AppProvider>) }
}

beforeEach(() => {
  const values = new Map<string, string>()
  vi.stubGlobal('localStorage', {
    getItem: vi.fn((key: string) => values.get(key) ?? null),
    setItem: vi.fn((key: string, value: string) => values.set(key, value)),
    removeItem: vi.fn((key: string) => values.delete(key)),
    clear: vi.fn(() => values.clear()),
  })
  window.location.hash = ''
})

describe('manual publication and permissions', () => {
  it('keeps the approved text and photo available to a member while the owner edits a draft', () => {
    const data = seed()
    data.manuals[0].attachments = [{ step: 0, name: 'approved.png', dataUrl: png }]
    const original = structuredClone(data.manuals[0])
    const owner = mount(data, '/manuals/manual-1/edit')
    fireEvent.change(screen.getByRole('textbox', { name: /매뉴얼 제목/ }), { target: { value: '미승인 제목' } })
    fireEvent.change(screen.getByRole('textbox', { name: '1단계 내용' }), { target: { value: '미승인 업무 지침' } })
    fireEvent.click(screen.getByRole('button', { name: '1단계 사진 삭제' }))
    fireEvent.click(screen.getByRole('button', { name: '초안 저장' }))

    const updated = saved()
    expect(updated.manuals[0].status).toBe('draft')
    expect(updated.manuals[0].title).toBe('미승인 제목')
    expect(updated.manuals[0].publishedSteps).toEqual(original.steps)
    expect(updated.manuals[0].publishedTitle).toBe(original.title)
    expect(updated.manuals[0].attachments).toEqual([])
    expect(updated.manuals[0].publishedAttachments).toEqual(original.attachments)
    owner.unmount()

    mount({ ...updated, role: 'member' }, '/manuals/manual-1')
    expect(screen.getByRole('heading', { name: original.title })).toBeInTheDocument()
    expect(screen.getByText(original.steps[0])).toBeInTheDocument()
    expect(screen.queryByText('미승인 업무 지침')).not.toBeInTheDocument()
    expect(screen.queryByText('미승인 제목')).not.toBeInTheDocument()
    expect(screen.getByRole('img', { name: '1단계 참고 사진: approved.png' })).toHaveAttribute('src', png)
  })

  it('enforces task access in the member list and direct manual URLs', () => {
    const data = seed('member')
    const view = mount(data, '/manuals')
    expect(screen.getByRole('heading', { name: '처음 시작하는 홀 서빙' })).toBeInTheDocument()
    expect(screen.queryByText('매장 마감 체크리스트')).not.toBeInTheDocument()
    expect(screen.queryByText('상품 입고·진열 방법')).not.toBeInTheDocument()
    view.route('/manuals/manual-2')
    expect(screen.getByRole('heading', { name: '매뉴얼을 찾을 수 없어요' })).toBeInTheDocument()
    view.route('/manuals/manual-1/edit')
    expect(screen.getByRole('heading', { name: '점주만 수정할 수 있어요' })).toBeInTheDocument()
    view.route('/manuals/manual-3/review')
    expect(screen.getByRole('heading', { name: '매뉴얼을 찾을 수 없어요' })).toBeInTheDocument()
  })

  it('requires an explicit review confirmation before publishing exactly the displayed steps and photo', () => {
    const data = seed()
    data.manuals[2].attachments = [{ step: 1, name: 'stock.png', dataUrl: png }]
    mount(data, '/manuals/manual-3/review')
    const publish = screen.getByRole('button', { name: '승인하고 게시하기' })
    expect(publish).toBeDisabled()
    fireEvent.click(publish)
    expect(saved().manuals[2].status).toBe('draft')
    expect(screen.getByRole('img', { name: '2단계 참고 사진: stock.png' })).toHaveAttribute('src', png)

    fireEvent.click(screen.getByRole('checkbox', { name: /각 단계가 우리 매장의 실제 업무/ }))
    fireEvent.click(publish)
    const published = saved().manuals[2]
    expect(published.status).toBe('published')
    expect(published.publishedSteps).toEqual(data.manuals[2].steps)
    expect(published.publishedAttachments).toEqual(data.manuals[2].attachments)
    expect(window.location.hash).toBe('#/manuals/manual-3')
  })

  it('blocks expired temporary relations even when navigating directly to a known manual', () => {
    const data = seed('member')
    data.workers = [{ ...data.workers[0], type: 'temporary', startDate: '2020-01-01', endDate: '2020-01-02' }]
    mount(data, '/manuals/manual-1')
    expect(screen.getByRole('heading', { name: '매장 연결이 필요해요' })).toBeInTheDocument()
    expect(screen.queryByText(data.manuals[0].steps[0])).not.toBeInTheDocument()
  })

  it('keeps checklist checks out of stored learning data and resets them after leaving the page', () => {
    const data = seed('member')
    const view = mount(data, '/manuals/manual-1')
    const before = localStorage.getItem(STORAGE_KEY)
    fireEvent.click(screen.getByRole('checkbox', { name: '1단계 확인' }))
    expect(screen.getByRole('checkbox', { name: '1단계 확인' })).toBeChecked()
    expect(localStorage.getItem(STORAGE_KEY)).toBe(before)
    view.route('/manuals')
    view.route('/manuals/manual-1')
    expect(screen.getByRole('checkbox', { name: '1단계 확인' })).not.toBeChecked()
  })
})

describe('local reference photos', () => {
  it('previews and saves a real selected photo on its step, then follows that step when an earlier one is removed', async () => {
    const data = seed()
    mount(data, '/manuals/manual-3/edit')
    const file = new File([Uint8Array.from(atob(png.split(',')[1]), char => char.charCodeAt(0))], 'shelf.png', { type: 'image/png' })
    fireEvent.change(screen.getByLabelText('2단계 참고 사진'), { target: { files: [file] } })
    expect(await screen.findByRole('img', { name: '2단계 참고 사진: shelf.png' })).toHaveAttribute('src', png)
    fireEvent.click(screen.getByRole('button', { name: '1단계 삭제' }))
    expect(screen.getByRole('img', { name: '1단계 참고 사진: shelf.png' })).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: '초안 저장' }))
    expect(saved().manuals[2].attachments).toEqual([{ step: 0, name: 'shelf.png', dataUrl: png }])
  })

  it('rejects unsupported files and files over 2 MB before storing them', () => {
    mount(seed(), '/manuals/manual-3/edit')
    const input = screen.getByLabelText('1단계 참고 사진')
    fireEvent.change(input, { target: { files: [new File(['text'], 'notes.txt', { type: 'text/plain' })] } })
    expect(screen.getByRole('alert')).toHaveTextContent('JPG, PNG, WebP 또는 GIF')
    fireEvent.change(input, { target: { files: [new File([new Uint8Array(2 * 1024 * 1024 + 1)], 'large.png', { type: 'image/png' })] } })
    expect(screen.getByRole('alert')).toHaveTextContent('2MB 이하')
    expect(saved().manuals[2].attachments).toBeUndefined()
  })

  it('keeps the draft unapproved and reports a storage failure when photos cannot be saved', () => {
    const data = seed()
    data.manuals[2].attachments = [{ step: 0, name: 'stock.png', dataUrl: png }]
    mount(data, '/manuals/manual-3/review')
    vi.mocked(localStorage.setItem).mockImplementation(() => { throw new DOMException('Quota exceeded', 'QuotaExceededError') })
    fireEvent.click(screen.getByRole('checkbox', { name: /각 단계가 우리 매장의 실제 업무/ }))
    fireEvent.click(screen.getByRole('button', { name: '승인하고 게시하기' }))
    expect(screen.getByRole('alert')).toHaveTextContent('저장 공간이 부족')
    expect(saved().manuals[2].status).toBe('draft')
    expect(window.location.hash).toBe('')
  })
})

describe('grounded manual questions', () => {
  it('declines questions with no evidence and never offers an inaccessible manual', () => {
    mount(seed('member'), '/chat')
    expect(screen.queryByRole('button', { name: /매장 마감 체크리스트/ })).not.toBeInTheDocument()
    fireEvent.change(screen.getByRole('textbox', { name: '궁금한 업무를 질문해 보세요' }), { target: { value: '급여일이 언제인가요?' } })
    fireEvent.click(screen.getByRole('button', { name: '질문 보내기' }))
    expect(screen.getByText('확인할 수 있는 근거가 없어요.')).toBeInTheDocument()
    expect(screen.getByText(/점주 또는 담당자에게 직접 확인/)).toBeInTheDocument()
    expect(screen.queryByText('근거 매뉴얼')).not.toBeInTheDocument()
  })

  it('answers from the previous approved copy while new draft content remains unpublished', async () => {
    const data = seed('member')
    const original = structuredClone(data.manuals[0])
    data.manuals[0] = { ...original, status: 'draft', title: '승인 대기 비공개 제목', steps: ['비공개 변경 지침'], publishedTitle: original.title, publishedSteps: original.steps }
    mount(data, '/chat')
    expect(screen.queryByText('승인 대기 비공개 제목')).not.toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: /처음 시작하는 홀 서빙/ }))
    await waitFor(() => expect(screen.getByText(original.steps[0])).toBeInTheDocument())
    expect(screen.queryByText('비공개 변경 지침')).not.toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: /근거 매뉴얼: 처음 시작하는 홀 서빙/ }))
    expect(window.location.hash).toBe('#/manuals/manual-1')
  })
})

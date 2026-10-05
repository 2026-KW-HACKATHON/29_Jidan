import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { Modal } from './Modal'

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
const base = { title: '종료할까요?', description: '접근 권한이 종료됩니다.', state: 'warning' as const, confirmLabel: '접근 종료' }

describe('Modal', () => {
  it('uses a distinct Figma status icon for each overlay example', () => {
    const { rerender } = render(<Modal {...base} open onClose={vi.fn()} />)
    const icon = () => document.querySelector<HTMLImageElement>('.ds-modal-icon img')?.getAttribute('src')
    const warning = icon()
    rerender(<Modal {...base} state="error" open onClose={vi.fn()} />)
    const error = icon()
    rerender(<Modal {...base} state="information" open onClose={vi.fn()} />)
    const information = icon()
    expect(warning).toBeTruthy()
    expect(new Set([warning, error, information]).size).toBe(3)
  })
  it('focuses cancel, links its message, ignores background clicks, restores the trigger on close', () => {
    const close = vi.fn()
    const { rerender } = render(<><button>열기</button><Modal {...base} open={false} onClose={close} /></>)
    const trigger = screen.getByRole('button', { name: '열기' })
    trigger.focus()
    rerender(<><button>열기</button><Modal {...base} open onClose={close} /></>)
    const dialog = screen.getByRole('alertdialog')
    expect(dialog).toHaveAccessibleName(base.title)
    expect(dialog).toHaveAccessibleDescription(base.description)
    expect(screen.getByRole('button', { name: '취소' })).toHaveFocus()
    fireEvent.keyDown(dialog, { key: 'Tab' })
    expect(screen.getByRole('button', { name: '접근 종료' })).toHaveFocus()
    fireEvent.keyDown(dialog, { key: 'Tab' })
    expect(screen.getByRole('button', { name: '취소' })).toHaveFocus()
    fireEvent.keyDown(dialog, { key: 'Tab', shiftKey: true })
    expect(screen.getByRole('button', { name: '접근 종료' })).toHaveFocus()
    fireEvent.click(dialog)
    expect(close).not.toHaveBeenCalled()
    fireEvent(dialog, new Event('cancel', { cancelable: true }))
    expect(close).toHaveBeenCalledOnce()
    rerender(<><button>열기</button><Modal {...base} open={false} onClose={close} /></>)
    expect(trigger).toHaveFocus()
  })
  it('allows one async confirmation and closes on success', async () => {
    let resolve!: () => void
    const confirm = vi.fn(() => new Promise<void>(done => { resolve = done }))
    const close = vi.fn()
    render(<Modal {...base} open onClose={close} onConfirm={confirm} />)
    const button = screen.getByRole('button', { name: '접근 종료' })
    fireEvent.click(button)
    fireEvent.click(button)
    expect(confirm).toHaveBeenCalledOnce()
    expect(button).toBeDisabled()
    expect(close).not.toHaveBeenCalled()
    await act(async () => resolve())
    expect(close).toHaveBeenCalledOnce()
  })
  it('keeps failures open and permits retry', async () => {
    const close = vi.fn()
    const confirm = vi.fn().mockRejectedValueOnce(new Error('private backend message')).mockResolvedValueOnce(undefined)
    render(<Modal {...base} open onClose={close} onConfirm={confirm} />)
    fireEvent.click(screen.getByRole('button', { name: '접근 종료' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('다시 시도해 주세요')
    expect(close).not.toHaveBeenCalled()
    expect(screen.queryByText('private backend message')).not.toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: '접근 종료' }))
    await waitFor(() => expect(close).toHaveBeenCalledOnce())
  })
  it('ignores a late result after closing and reopening', async () => {
    let resolve!: () => void
    const confirm = () => new Promise<void>(done => { resolve = done })
    const close = vi.fn()
    const { rerender } = render(<Modal {...base} open onClose={close} onConfirm={confirm} />)
    fireEvent.click(screen.getByRole('button', { name: '접근 종료' }))
    rerender(<Modal {...base} open={false} onClose={close} onConfirm={confirm} />)
    rerender(<Modal {...base} open onClose={close} onConfirm={confirm} />)
    await act(async () => resolve())
    expect(close).not.toHaveBeenCalled()
    expect(screen.getByRole('button', { name: '접근 종료' })).toBeEnabled()
  })
  it('uses a single confirmation for information and restores focus when unmounted', () => {
    const trigger = document.createElement('button')
    document.body.append(trigger)
    trigger.focus()
    const { unmount } = render(<Modal open title="완료" description="저장했습니다" onClose={vi.fn()} />)
    expect(screen.getByRole('dialog')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: '확인' })).toHaveFocus()
    unmount()
    expect(trigger).toHaveFocus()
    trigger.remove()
  })
})

it('안내 모달의 보조 동작으로 닫고 주 동작을 실행하지 않는다', () => {
 const close=vi.fn(),confirm=vi.fn()
 render(<Modal open title="등록 완료" description="공고를 확인하세요" showCancel cancelLabel="닫기" confirmLabel="공고 보기" onClose={close} onConfirm={confirm}/>)
 fireEvent.click(screen.getByRole('button',{name:'닫기'}))
 expect(close).toHaveBeenCalledOnce();expect(confirm).not.toHaveBeenCalled()
})

it('아이콘 없는 업무 확인에서도 제목과 설명 연결을 유지한다',()=>{render(<Modal open title="모집 마감" description="설명" showIcon={false} summary={<div>공고 요약</div>} onClose={()=>{}}/>);expect(screen.getByRole('dialog',{name:'모집 마감'})).toHaveAccessibleDescription('설명');expect(screen.getByText('공고 요약')).toBeVisible();expect(document.querySelector('.ds-modal-icon')).toBeNull()})

it('확인 후 결과 화면으로 전환하는 모달은 자동으로 닫지 않는다',async()=>{const close=vi.fn();render(<Modal open closeOnConfirm={false} title="처리" description="설명" onConfirm={async()=>{}} onClose={close}/>);fireEvent.click(screen.getByRole('button',{name:'확인'}));await act(async()=>{});expect(close).not.toHaveBeenCalled();expect(screen.getByRole('dialog')).toBeVisible()})

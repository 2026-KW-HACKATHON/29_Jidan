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

import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { beforeEach, afterEach, expect, it, vi } from 'vitest'
import { PickerDialog } from './PickerDialog'
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
it('제목을 연결하고 닫기 및 Escape 후 트리거 포커스를 복원한다', () => {
  const trigger = document.createElement('button'); document.body.append(trigger); trigger.focus()
  const close = vi.fn()
  const view = render(<PickerDialog title="업종 선택" onClose={close}><button>음식점</button></PickerDialog>)
  const dialog = screen.getByRole('dialog', { name: '업종 선택' })
  expect(screen.getByRole('button', { name: '닫기' })).toHaveFocus()
  fireEvent.click(dialog); expect(close).not.toHaveBeenCalled()
  fireEvent(dialog, new Event('cancel', { bubbles: true, cancelable: true })); expect(close).toHaveBeenCalledOnce()
  view.unmount(); expect(trigger).toHaveFocus(); trigger.remove()
})

import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { beforeEach, afterEach, expect, it, vi } from 'vitest'
import { IndustryPicker } from './IndustryPicker'

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

it('선택하지 않으면 완료를 막고 선택 후 확인할 때만 반영한다', () => {
  const confirm = vi.fn()
  render(<IndustryPicker value="" onClose={vi.fn()} onConfirm={confirm} />)
  expect(screen.getByRole('button', { name: '선택 완료' })).toBeDisabled()
  fireEvent.click(screen.getByRole('radio', { name: '카페' }))
  expect(confirm).not.toHaveBeenCalled()
  expect(screen.getByRole('radio', { name: '카페' })).toBeChecked()
  fireEvent.click(screen.getByRole('button', { name: '선택 완료' }))
  expect(confirm).toHaveBeenCalledWith('카페')
})
it('기존 업종을 표시하고 변경 후 닫아도 반영하지 않는다', () => {
  const confirm = vi.fn(), close = vi.fn()
  render(<IndustryPicker value="음식점" onClose={close} onConfirm={confirm} />)
  expect(screen.getByRole('radio', { name: '음식점' })).toBeChecked()
  fireEvent.click(screen.getByRole('radio', { name: '기타' }))
  fireEvent.click(screen.getByRole('button', { name: '닫기' }))
  expect(close).toHaveBeenCalledOnce()
  expect(confirm).not.toHaveBeenCalled()
})

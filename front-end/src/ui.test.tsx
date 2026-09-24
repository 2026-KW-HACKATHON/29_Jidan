import { fireEvent, render, screen } from '@testing-library/react'
import { expect, it, vi } from 'vitest'
import { Modal } from './ui'

it('preserves input focus on a clock-driven rerender and uses the latest close callback', () => {
  const oldClose = vi.fn(); const latestClose = vi.fn()
  const { rerender } = render(<Modal title="주소 검색" onClose={oldClose}><input aria-label="주소" /><button>검색</button></Modal>)
  const input = screen.getByRole('textbox', { name: '주소' }); input.focus(); fireEvent.change(input, { target: { value: '광운로' } })
  rerender(<Modal title="주소 검색" onClose={latestClose}><input aria-label="주소" /><button>검색</button></Modal>)
  expect(input).toHaveFocus(); expect(input).toHaveValue('광운로')
  fireEvent.keyDown(input, { key: 'Escape' }); expect(latestClose).toHaveBeenCalledOnce(); expect(oldClose).not.toHaveBeenCalled()
})

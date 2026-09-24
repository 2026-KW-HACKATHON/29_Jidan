import { fireEvent, render, screen } from '@testing-library/react'
import { expect, it, vi } from 'vitest'
import { RoleSelectionScreen } from './RoleSelectionScreen'

it('hands each role to its signup entry without creating an account', () => {
  const select = vi.fn()
  const back = vi.fn()
  render(<RoleSelectionScreen onBack={back} onSelect={select} />)
  fireEvent.click(screen.getByRole('button', { name: '점주로 가입' }))
  fireEvent.click(screen.getByRole('button', { name: '일반회원으로 가입' }))
  expect(select.mock.calls).toEqual([['owner'], ['worker']])
  fireEvent.click(screen.getByRole('button', { name: '뒤로 가기' }))
  expect(back).toHaveBeenCalledOnce()
})

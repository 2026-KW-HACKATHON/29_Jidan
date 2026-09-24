import { fireEvent, render, screen } from '@testing-library/react'
import { expect, it, vi } from 'vitest'
import { Checkbox } from './Checkbox'

it('toggles from its accessible label and blocks disabled changes', () => {
  const change = vi.fn()
  const { rerender } = render(<label><Checkbox onChange={change} />동의</label>)
  fireEvent.click(screen.getByText('동의'))
  expect(screen.getByRole('checkbox')).toBeChecked()
  expect(change).toHaveBeenCalledOnce()
  rerender(<label><Checkbox disabled onChange={change} />동의</label>)
  fireEvent.click(screen.getByText('동의'))
  expect(screen.getByRole('checkbox')).toBeChecked()
  expect(change).toHaveBeenCalledOnce()
})

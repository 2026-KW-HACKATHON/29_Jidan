import { fireEvent, render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { Choice } from './Choice'

describe('Choice', () => {
  it('keeps radio choices mutually exclusive and submits the selected value', () => {
    render(<form aria-label="선택"><Choice name="role" value="owner" defaultChecked>사장님</Choice><Choice name="role" value="member">근무자</Choice></form>)
    fireEvent.click(screen.getByLabelText('근무자'))
    expect(screen.getByLabelText('사장님')).not.toBeChecked()
    expect(screen.getByLabelText('근무자')).toBeChecked()
    expect(new FormData(screen.getByRole('form') as HTMLFormElement).get('role')).toBe('member')
  })
  it('supports independent multiple selections', () => {
    render(<><Choice type="checkbox" name="day" value="mon">월</Choice><Choice type="checkbox" name="day" value="tue">화</Choice></>)
    fireEvent.click(screen.getByLabelText('월'))
    fireEvent.click(screen.getByLabelText('화'))
    expect(screen.getByLabelText('월')).toBeChecked()
    expect(screen.getByLabelText('화')).toBeChecked()
  })
  it('does not activate disabled choices through the label', () => {
    const change = vi.fn()
    render(<Choice disabled onChange={change}>마감</Choice>)
    fireEvent.click(screen.getByText('마감'))
    expect(change).not.toHaveBeenCalled()
    expect(screen.getByRole('radio')).not.toBeChecked()
  })
})

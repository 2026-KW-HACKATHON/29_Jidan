import { fireEvent, render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { Button } from './Button'

describe('Button', () => {
  it('does not submit a surrounding form unless explicitly requested', () => {
    const submit = vi.fn()
    render(<form onSubmit={event => { event.preventDefault(); submit() }}><Button>취소</Button><Button type="submit">저장</Button></form>)
    fireEvent.click(screen.getByRole('button', { name: '취소' }))
    expect(submit).not.toHaveBeenCalled()
    expect(screen.getByRole('button', { name: '취소' })).toHaveFocus()
    fireEvent.click(screen.getByRole('button', { name: '저장' }))
    expect(submit).toHaveBeenCalledOnce()
  })
  it.each([{ disabled: true }, { busy: true }])('blocks activation when %j', props => {
    const click = vi.fn()
    render(<Button {...props} onClick={click}>다음</Button>)
    fireEvent.click(screen.getByRole('button'))
    expect(click).not.toHaveBeenCalled()
    expect(screen.getByRole('button')).toBeDisabled()
  })
})

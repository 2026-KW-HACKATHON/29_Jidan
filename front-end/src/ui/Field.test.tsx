import { fireEvent, render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { InputField, SelectField, TextareaField } from './Field'

describe('fields', () => {
  it('connects a unique label and combines error and external descriptions', () => {
    render(<><p id="extra">외부 설명</p><InputField label="이름" helper="입력 안내" error="이름을 입력해 주세요" aria-describedby="extra" /><InputField label="별명" /></>)
    const input = screen.getByLabelText('이름')
    expect(input).toHaveAttribute('aria-invalid', 'true')
    expect(input).toHaveAccessibleDescription('이름을 입력해 주세요 외부 설명')
    expect(input.id).not.toBe(screen.getByLabelText('별명').id)
    expect(screen.queryByText('입력 안내')).not.toBeInTheDocument()
  })
  it('clears obsolete error descriptions when the input becomes valid', () => {
    const { rerender } = render(<InputField label="이름" error="필수 항목" />)
    rerender(<InputField label="이름" helper="확인했습니다" />)
    expect(screen.getByLabelText('이름')).not.toHaveAttribute('aria-invalid')
    expect(screen.getByLabelText('이름')).toHaveAccessibleDescription('확인했습니다')
  })
  it('keeps read-only values focusable and excludes disabled fields from form data', () => {
    render(<form aria-label="프로필"><InputField label="이메일" name="email" readOnly value="a@example.com" /><InputField label="미사용" name="unused" disabled defaultValue="제외" /></form>)
    const email = screen.getByLabelText('이메일')
    email.focus()
    expect(email).toHaveFocus()
    expect(email).toHaveAttribute('readonly')
    const data = new FormData(screen.getByRole('form') as HTMLFormElement)
    expect(data.get('email')).toBe('a@example.com')
    expect(data.has('unused')).toBe(false)
  })
  it('preserves multiline content and forwards changes', () => {
    const change = vi.fn()
    render(<TextareaField label="업무 설명" onChange={change} />)
    fireEvent.change(screen.getByLabelText('업무 설명'), { target: { value: '첫 단계\n다음 단계' } })
    expect(screen.getByLabelText('업무 설명')).toHaveValue('첫 단계\n다음 단계')
    expect(change).toHaveBeenCalledOnce()
  })
  it('opens a picker without submitting and respects disabled state', () => {
    const open = vi.fn()
    const { rerender } = render(<SelectField label="업종" onClick={open}>카페</SelectField>)
    const field = screen.getByRole('button', { name: '업종' })
    expect(field).toHaveAttribute('type', 'button')
    fireEvent.click(field)
    expect(open).toHaveBeenCalledOnce()
    expect(field).toHaveFocus()
    rerender(<SelectField label="업종" disabled onClick={open}>카페</SelectField>)
    fireEvent.click(field)
    expect(open).toHaveBeenCalledOnce()
  })
})

import { useState } from 'react'
import { fireEvent, render, screen } from '@testing-library/react'
import { expect, it } from 'vitest'
import { NumberField } from './NumberField'
import { formatNumber } from './numberFormat'
it.each([
  ['010122222222222222', 'mobile', '010-1222-2222'],
  ['０１０ abc １２３４ ５６７８', 'mobile', '010-1234-5678'],
  ['021234567890', 'telephone', '02-1234-5678'],
  ['021234567', 'telephone', '02-123-4567'],
  ['0311234567', 'telephone', '031-123-4567'],
  ['07012345678', 'telephone', '070-1234-5678'],
  ['2208162517999', 'business', '220-81-62517'],
  ['0101234', 'mobile', '010-1234'],
  ['', 'business', ''], ['010', 'mobile', '010'],
] as const)('%s 입력을 제한하고 형식화한다', (value, format, expected) => expect(formatNumber(value, format)).toBe(expected))
it('붙여넣기와 전체 삭제 및 중간 커서 편집을 지원한다', () => {
  function Form() { const [value, setValue] = useState(''); return <NumberField label="번호" format="mobile" value={value} onValueChange={setValue} /> }
  render(<Form />)
  const input = screen.getByLabelText('번호') as HTMLInputElement
  fireEvent.change(input, { target: { value: '010 1234 56789999' } })
  expect(input).toHaveValue('010-1234-5678')
  fireEvent.change(input, { target: { value: '010-9234-5678', selectionStart: 5, selectionEnd: 5 } })
  expect(input.selectionStart).toBe(5)
  input.setSelectionRange(4, 4); fireEvent.keyDown(input, { key: 'Backspace' })
  expect([input.selectionStart, input.selectionEnd]).toEqual([2, 4])
  input.setSelectionRange(3, 3); fireEvent.keyDown(input, { key: 'Delete' })
  expect([input.selectionStart, input.selectionEnd]).toEqual([3, 5])
  fireEvent.change(input, { target: { value: '' } }); expect(input).toHaveValue('')
})

import type { ComponentProps } from 'react'
import { InputField } from '../../ui/Field'

import { formatNumber, onlyDigits, type NumberFormat } from './numberFormat'

export function NumberField({ format, value, onValueChange, ...props }: Omit<ComponentProps<typeof InputField>, 'value' | 'onChange' | 'maxLength' | 'type'> & {
  format: NumberFormat; value: string; onValueChange: (value: string) => void
}) {
  return <InputField {...props} type="tel" inputMode="numeric" value={value}
    onKeyDown={event => {
      const input = event.currentTarget, start = input.selectionStart
      if (start !== null && start === input.selectionEnd) {
        if (event.key === 'Backspace' && input.value[start - 1] === '-') input.setSelectionRange(Math.max(0, start - 2), start)
        if (event.key === 'Delete' && input.value[start] === '-') input.setSelectionRange(start, start + 2)
      }
      props.onKeyDown?.(event)
    }}
    onChange={event => {
      const input = event.currentTarget
      const count = onlyDigits(input.value.slice(0, input.selectionStart ?? input.value.length)).length
      const formatted = formatNumber(input.value, format)
      let caret = 0, seen = 0
      while (caret < formatted.length && seen < count) { if (/\d/.test(formatted[caret])) seen++; caret++ }
      input.value = formatted
      onValueChange(formatted)
      input.setSelectionRange(caret, caret)
    }} />
}

import type { ComponentProps, ReactNode } from 'react'
import './tokens.css'
import './Choice.css'

type ChoiceProps = Omit<ComponentProps<'input'>, 'type' | 'children'> & {
  children: ReactNode
  type?: 'radio' | 'checkbox'
}
/** Share name for single-choice radio groups; use checkbox for multiple values. */
export function Choice({ children, type = 'radio', className = '', ...props }: ChoiceProps) {
  return <label className={`ds-ui ds-choice ${className}`}>
    <input {...props} type={type} /><span>{children}</span>
  </label>
}

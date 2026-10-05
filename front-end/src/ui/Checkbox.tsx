import type { ComponentProps } from 'react'
import './tokens.css'
import './Checkbox.css'

export function Checkbox({ className = '', ...props }: Omit<ComponentProps<'input'>, 'type'>) {
  return <input {...props} type="checkbox" className={`ds-ui ds-checkbox ${className}`} />
}

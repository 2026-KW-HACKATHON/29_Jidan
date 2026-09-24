import type { ComponentProps } from 'react'
import './tokens.css'
import './Button.css'

export type ButtonProps = ComponentProps<'button'> & {
  intent?: 'primary' | 'secondary' | 'danger'
  busy?: boolean
}

export function Button({ intent = 'primary', busy = false, disabled, type = 'button', className = '', children, ...props }: ButtonProps) {
  return <button {...props} type={type} className={`ds-ui ds-button ds-button-${intent} ${className}`}
    disabled={disabled || busy} aria-busy={busy || undefined}>{children}</button>
}

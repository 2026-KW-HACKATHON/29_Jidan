import { useId, type ComponentProps, type ReactNode } from 'react'
import chevron from './assets/chevron.svg'
import './tokens.css'
import './Field.css'

type FieldText = { label: ReactNode; helper?: string; error?: string }
function FieldFrame({ id, label, helper, error, children }: FieldText & { id: string; children: ReactNode }) {
  return <div className="ds-ui ds-field">
    <label htmlFor={id}>{label}</label>
    {children}
    {(error || helper) && <p id={`${id}-hint`} className={`ds-field-hint ${error ? 'ds-field-error' : ''}`}>{error || helper}</p>}
  </div>
}
function describedBy(id: string, hint: string | undefined, external?: string) {
  return [hint ? `${id}-hint` : '', external].filter(Boolean).join(' ') || undefined
}
export function InputField({ label, helper, error, id, className = '', 'aria-describedby': description, ...props }: ComponentProps<'input'> & FieldText) {
  const generated = useId()
  const fieldId = id || generated
  return <FieldFrame id={fieldId} label={label} helper={helper} error={error}>
    <input {...props} id={fieldId} className={`ds-field-control ${className}`}
      aria-invalid={error ? true : props['aria-invalid']} aria-describedby={describedBy(fieldId, error || helper, description)} />
  </FieldFrame>
}
export function TextareaField({ label, helper, error, id, className = '', 'aria-describedby': description, ...props }: ComponentProps<'textarea'> & FieldText) {
  const generated = useId()
  const fieldId = id || generated
  return <FieldFrame id={fieldId} label={label} helper={helper} error={error}>
    <textarea {...props} id={fieldId} className={`ds-field-control ds-field-textarea ${className}`}
      aria-invalid={error ? true : props['aria-invalid']} aria-describedby={describedBy(fieldId, error || helper, description)} />
  </FieldFrame>
}
/** Figma State=Select opens a separate picker rather than an editable input. */
export function SelectField({ label, helper, error, id, children, onClick, className = '', indicatorSrc = chevron, 'aria-describedby': description, ...props }: Omit<ComponentProps<'button'>, 'type'> & FieldText & { indicatorSrc?: string }) {
  const generated = useId()
  const fieldId = id || generated
  return <FieldFrame id={fieldId} label={label} helper={helper} error={error}>
    <button {...props} id={fieldId} type="button" onClick={event => { event.currentTarget.focus(); onClick?.(event) }} className={`ds-field-control ds-field-select ${className}`}
      aria-invalid={error ? true : props['aria-invalid']} aria-describedby={describedBy(fieldId, error || helper, description)}>
      <span>{children}</span><img src={indicatorSrc} alt="" width="5.833" height="11.667" />
    </button>
  </FieldFrame>
}

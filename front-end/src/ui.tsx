import { useEffect, useId, useRef } from 'react'
import type { ButtonHTMLAttributes, HTMLAttributes, InputHTMLAttributes, ReactNode } from 'react'
const icons: Record<string, [string, number, number]> = {
  home: ['42ce1.svg', 24, 24], book: ['64606.svg', 24, 24], brief: ['d992f.svg', 24, 24], user: ['5987c.svg', 24, 24], 'user-muted': ['26cfb.svg', 24, 24],
  bell: ['6107e.svg', 24, 24], store: ['2a0a1.svg', 24, 24], brand: ['392c0.svg', 23.3333, 21], arrow: ['ab581.svg', 7, 14],
  chevron: ['43e7c.svg', 5.83333, 11.6667], 'calendar-back': ['baa62.svg', 5.83333, 11.6667], check: ['7fc9e.svg', 11.6667, 8.33333], plus: ['store-plus.svg', 16, 16],
}
export function Icon({ name, className = '' }: { name: string; className?: string }) {
  const icon = icons[name]
  if (icon) return <span className={`icon ${className}`} aria-hidden="true"><img src={`/figma/${icon[0]}`} alt="" width={icon[1]} height={icon[2]} /></span>
  return <span aria-hidden="true" className={`icon text-icon ${className}`}>{({ search: '⌕', chat: '…', close: '×', clock: '◷', calendar: '▦', mic: '♩' } as Record<string, string>)[name] || '·'}</span>
}
export function Button({ variant = 'primary', className = '', type = 'button', ...props }: ButtonHTMLAttributes<HTMLButtonElement> & { variant?: 'primary' | 'secondary' | 'danger' | 'ghost' }) { return <button type={type} className={`button button-${variant} ${className}`} {...props} /> }
export function Card({ className = '', ...props }: HTMLAttributes<HTMLDivElement>) { return <div className={`card ${className}`} {...props} /> }
export function Heading({ title, description }: { title: ReactNode; description?: ReactNode }) { return <div className="heading"><h1>{title}</h1>{description && <p className="muted">{description}</p>}</div> }
export function Badge({ children, tone = 'blue' }: { children: ReactNode; tone?: 'blue' | 'gray' | 'green' | 'red' | 'yellow' }) { return <span className={`badge badge-${tone}`}>{children}</span> }
export function Field({ label, id, error, hint, className = '', ...props }: InputHTMLAttributes<HTMLInputElement> & { label: ReactNode; error?: string; hint?: string }) {
  const uid = useId(); const fieldId = id || uid
  return <div className={`field ${className}`}><label htmlFor={fieldId}>{label}{props.required && <span className="required"> *</span>}</label><input id={fieldId} aria-invalid={!!error} aria-describedby={error || hint ? `${fieldId}-hint` : undefined} {...props} />{(error || hint) && <p id={`${fieldId}-hint`} className={error ? 'field-error' : 'small muted'}>{error || hint}</p>}</div>
}
export function Empty({ title, description }: { title: string; description?: string }) { return <Card className="empty"><span className="empty-symbol" aria-hidden="true">○</span><h2>{title}</h2>{description && <p className="muted">{description}</p>}</Card> }
export function Modal({ title, children, onClose }: { title: string; children: ReactNode; onClose: () => void }) {
  const ref = useRef<HTMLDivElement>(null); const titleId = useId(); const closeRef = useRef(onClose)
  useEffect(() => { closeRef.current = onClose }, [onClose])
  useEffect(() => {
    const previous = document.activeElement as HTMLElement | null
    const overflow = document.body.style.overflow; document.body.style.overflow = 'hidden'; ref.current?.focus()
    const listener = (event: KeyboardEvent) => {
      if (event.key === 'Escape') closeRef.current()
      if (event.key !== 'Tab') return
      const items = ref.current?.querySelectorAll<HTMLElement>('button:not(:disabled), input:not(:disabled), select:not(:disabled), a[href], [tabindex="0"]')
      if (!items?.length) { event.preventDefault(); return }
      const first = items[0]; const last = items[items.length - 1]
      if (event.shiftKey && (document.activeElement === first || document.activeElement === ref.current)) { event.preventDefault(); last.focus() }
      else if (!event.shiftKey && (document.activeElement === last || document.activeElement === ref.current)) { event.preventDefault(); first.focus() }
    }
    document.addEventListener('keydown', listener)
    return () => { document.body.style.overflow = overflow; document.removeEventListener('keydown', listener); previous?.focus() }
  }, [])
  return <div className="modal-backdrop" onClick={onClose}><div ref={ref} tabIndex={-1} className="modal" role="dialog" aria-modal="true" aria-labelledby={titleId} onClick={event => event.stopPropagation()}><div className="row between"><h2 id={titleId}>{title}</h2><button className="icon-button" aria-label="닫기" onClick={onClose}>×</button></div>{children}</div></div>
}
export function DetailRow({ label, children }: { label: string; children: ReactNode }) { return <div className="detail-row"><span className="muted">{label}</span><span>{children}</span></div> }

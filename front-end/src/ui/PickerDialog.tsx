import { useEffect, useId, useRef, type ReactNode } from 'react'
import { Button } from './Button'
import './PickerDialog.css'

/** Uses the native dialog focus boundary so embedded address-search frames stay keyboard accessible. */
export function PickerDialog({ title, onClose, children, className = '' }: { title: string; onClose: () => void; children: ReactNode; className?: string }) {
  const dialog = useRef<HTMLDialogElement>(null)
  const close = useRef<HTMLButtonElement>(null)
  const id = useId()
  useEffect(() => {
    const trigger = document.activeElement instanceof HTMLElement ? document.activeElement : null
    const element = dialog.current!
    element.showModal()
    close.current?.focus()
    return () => { element.close(); if (trigger?.isConnected) trigger.focus() }
  }, [])
  return <dialog ref={dialog} className={`ds-ui ds-picker ${className}`} aria-labelledby={id} onCancel={event => { event.preventDefault(); onClose() }}>
    <header><h2 id={id}>{title}</h2><Button ref={close} intent="secondary" onClick={onClose}>닫기</Button></header>
    <div className="ds-picker-body">{children}</div>
  </dialog>
}

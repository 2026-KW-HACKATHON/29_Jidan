import { useEffect, useId, useRef, useState } from 'react'
import { Button } from './Button'
import warningIcon from './assets/warning.svg'
import './Modal.css'

type ModalProps = {
  open: boolean
  title: string
  description: string
  state?: 'warning' | 'error' | 'information'
  confirmLabel?: string
  cancelLabel?: string
  busy?: boolean
  onClose: () => void
  /** Resolve to close; reject to keep the dialog open and allow retry. */
  onConfirm?: () => void | Promise<void>
}

export function Modal({ open, ...props }: ModalProps) {
  return open ? <OpenModal {...props} /> : null
}

function OpenModal({ title, description, state = 'information', confirmLabel = '확인', cancelLabel = '취소', busy = false, onClose, onConfirm }: Omit<ModalProps, 'open'>) {
  const dialog = useRef<HTMLDialogElement>(null)
  const initialFocus = useRef<HTMLButtonElement>(null)
  const generation = useRef(0)
  const inFlight = useRef(false)
  const [pending, setPending] = useState(false)
  const [failed, setFailed] = useState(false)
  const id = useId()
  const information = state === 'information'

  useEffect(() => {
    generation.current += 1
    const element = dialog.current!
    const trigger = document.activeElement instanceof HTMLElement ? document.activeElement : null
    element.showModal()
    initialFocus.current?.focus()
    return () => {
      generation.current += 1
      element.close()
      if (trigger?.isConnected) trigger.focus()
    }
  }, [])

  async function confirm() {
    if (busy || inFlight.current) return
    inFlight.current = true
    setPending(true)
    setFailed(false)
    const current = generation.current
    try {
      await onConfirm?.()
      if (current === generation.current) onClose()
    } catch {
      if (current === generation.current) setFailed(true)
    } finally {
      if (current === generation.current) {
        inFlight.current = false
        setPending(false)
      }
    }
  }

  return <dialog ref={dialog} className="ds-ui ds-modal" role={information ? 'dialog' : 'alertdialog'}
    aria-modal="true" aria-labelledby={`${id}-title`} aria-describedby={`${id}-description`}
    aria-busy={busy || pending || undefined} tabIndex={-1}
    onCancel={event => { event.preventDefault(); onClose() }}>
    <div className="ds-modal-content">
      <div className={`ds-modal-icon ds-modal-icon-${state}`}><img src={warningIcon} alt="" /></div>
      <div className="ds-modal-message">
        <h2 id={`${id}-title`}>{title}</h2>
        <p id={`${id}-description`}>{description}</p>
        {failed && <p className="ds-modal-failure" role="alert">처리하지 못했어요. 다시 시도해 주세요.</p>}
      </div>
    </div>
    <div className="ds-modal-actions">
      {!information && <Button ref={initialFocus} intent="secondary" onClick={onClose}>{cancelLabel}</Button>}
      <Button ref={information ? initialFocus : undefined} intent={state === 'warning' ? 'danger' : 'primary'}
        busy={busy || pending} onClick={confirm}>{confirmLabel}</Button>
    </div>
  </dialog>
}

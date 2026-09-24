import { useEffect, useRef, useState } from 'react'
import { Button } from '../../ui/Button'
import { AppBar } from '../../ui/AppBar'
import { MobileLayout } from '../../ui/MobileLayout'
import { Modal } from '../../ui/Modal'
import { OwnerStep } from './OwnerStep'
import { OwnerBasic } from './OwnerBasic'
import { OwnerStore } from './OwnerStore'
import { OwnerReview } from './OwnerReview'
import { OwnerPending } from './OwnerPending'
import { clearDraft, restoreDraft, saveDraft, type DraftState } from './draft'
import { emptyDraft, normalizeDraft, validateOwner, type OwnerDraft, type OwnerErrors } from './model'
import { isOwnerReceipt, OwnerFailure, ownerService, type OwnerIdentity, type OwnerReceipt, type OwnerService } from './service'
import type { AddressSearch } from '../postcode'

const initialDraft = (): DraftState => ({ draft: { ...emptyDraft }, step: 1, requestKey: crypto.randomUUID() })
const messages = {
  unavailable: ['가입 정보를 불러올 수 없어요', '잠시 후 다시 시도해 주세요.'],
  expired: ['다시 로그인해 주세요', '가입 세션이 만료됐어요. 다시 로그인한 뒤 진행해 주세요.'],
  duplicate: ['이미 등록된 매장이에요', '중복 등록하지 않고 운영자에게 관리 권한을 문의해 주세요.'],
  validation: ['입력 내용을 확인해 주세요', '입력 내용을 수정한 뒤 다시 신청해 주세요.'],
  network: ['요청을 처리하지 못했어요', '입력 내용은 유지돼요. 잠시 후 다시 시도해 주세요.'],
} as const

export function OwnerRegistration({ onBack, onExpired, service = ownerService, addressSearch, homeMode = false, storage = sessionStorage }: {
  onBack: () => void; onExpired: () => void; service?: OwnerService; addressSearch?: AddressSearch; homeMode?: boolean; storage?: Storage
}) {
  const [identity, setIdentity] = useState<OwnerIdentity | null>(null)
  const [state, setState] = useState(initialDraft)
  const [errors, setErrors] = useState<OwnerErrors>({})
  const [editing, setEditing] = useState(false)
  const [receipt, setReceipt] = useState<OwnerReceipt | null>(null)
  const [failure, setFailure] = useState<OwnerFailure | null>(null)
  const [busy, setBusy] = useState(false)
  const [attempt, setAttempt] = useState(0)
  const [checking, setChecking] = useState(true)
  const locked = useRef(false)
  const submission = useRef<AbortController | null>(null)
  const live = useRef(true)

  useEffect(() => {
    live.current = true
    const controller = new AbortController()
    const timer = setTimeout(() => { controller.abort(); if (live.current) { setChecking(false); setFailure(new OwnerFailure('network')) } }, 10000)
    void service.identity(controller.signal).then(value => {
      if (controller.signal.aborted) return
      if (!value.draftScope || !/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(value.email)) throw new OwnerFailure('unavailable')
      if (value.receipt && !isOwnerReceipt(value.receipt)) throw new OwnerFailure('unavailable')
      setIdentity(value)
      if (value.receipt) { clearDraft(storage); setReceipt(value.receipt); return }
      const restored = restoreDraft(value.draftScope, storage)
      const next = restored || initialDraft()
      if (!restored && value.name) next.draft.name = value.name
      // Stored progress is a UX hint, never evidence that required input is valid.
      if (next.step > 1 && Object.keys(validateOwner(next.draft, 1)).length) next.step = 1
      else if (next.step === 3 && Object.keys(validateOwner(next.draft, 2)).length) next.step = 2
      setState(next)
    }).catch(error => {
      if (!controller.signal.aborted) {
        const reason = error instanceof OwnerFailure ? error : new OwnerFailure('network')
        if (reason.code === 'expired') clearDraft(storage)
        setFailure(reason)
      }
    }).finally(() => { clearTimeout(timer); if (!controller.signal.aborted) setChecking(false) })
    return () => { live.current = false; controller.abort(); submission.current?.abort(); clearTimeout(timer) }
  }, [service, attempt, storage])

  useEffect(() => {
    if (identity && !receipt && !homeMode) saveDraft(identity.draftScope, state, storage)
  }, [identity, receipt, state, homeMode, storage])

  useEffect(() => {
    if (!identity || receipt || homeMode) return
    const warn = (event: BeforeUnloadEvent) => { if (Object.values(state.draft).some(Boolean)) event.preventDefault() }
    window.addEventListener('beforeunload', warn)
    return () => window.removeEventListener('beforeunload', warn)
  }, [identity, receipt, state.draft, homeMode])

  function move(step: 1 | 2 | 3) {
    setState(previous => ({ ...previous, step }))
    setErrors({})
    requestAnimationFrame(() => {
      document.querySelector('.owner-signup .ds-mobile-body')?.scrollTo({ top: 0 })
      const heading = document.querySelector<HTMLElement>('.owner-intro h2')
      heading?.setAttribute('tabindex', '-1'); heading?.focus({ preventScroll: true })
    })
  }
  function rejectInput(nextErrors: OwnerErrors) {
    setErrors(nextErrors)
    requestAnimationFrame(() => document.getElementById(`owner-${Object.keys(nextErrors)[0]}`)?.focus())
  }
  function change(key: keyof OwnerDraft, value: string) {
    setState(previous => ({ ...previous, draft: { ...previous.draft, [key]: value }, requestKey: crypto.randomUUID() }))
    setErrors(previous => ({ ...previous, [key]: undefined }))
  }
  async function submit() {
    if (!identity || locked.current) return
    const basicErrors = validateOwner(state.draft, 1), storeErrors = validateOwner(state.draft, 2)
    if (Object.keys(basicErrors).length || Object.keys(storeErrors).length) {
      move(Object.keys(basicErrors).length ? 1 : 2); rejectInput(Object.keys(basicErrors).length ? basicErrors : storeErrors); return
    }
    locked.current = true; setBusy(true)
    const controller = new AbortController(); submission.current = controller
    const timer = setTimeout(() => { controller.abort(); if (live.current) { locked.current = false; setBusy(false); setFailure(new OwnerFailure('network')) } }, 15000)
    try {
      const result = await service.submit(normalizeDraft(state.draft), state.requestKey, controller.signal)
      if (!live.current || controller.signal.aborted) return
      if (!isOwnerReceipt(result)) throw new OwnerFailure('network')
      clearDraft(storage); setReceipt(result); setFailure(null)
    } catch (error) {
      if (!live.current || controller.signal.aborted) return
      const reason = error instanceof OwnerFailure ? error : new OwnerFailure('network')
      if (reason.code === 'expired') clearDraft(storage)
      if (reason.code === 'validation' && Object.keys(reason.fields).length) {
        move(reason.fields.name || reason.fields.phone ? 1 : 2); rejectInput(reason.fields)
      }
      setFailure(reason)
    } finally {
      clearTimeout(timer)
      if (live.current && submission.current === controller) { locked.current = false; setBusy(false) }
    }
  }
  function next() {
    if (state.step === 3) { void submit(); return }
    const nextErrors = validateOwner(state.draft, state.step)
    if (Object.keys(nextErrors).length) { rejectInput(nextErrors); return }
    move(editing ? 3 : state.step === 1 ? 2 : 3); setEditing(false)
  }
  function back() {
    if (locked.current) return
    if (editing) { move(3); setEditing(false) }
    else if (state.step === 1) onBack()
    else move(state.step === 3 ? 2 : 1)
  }
  function closeError() { setFailure(null) }
  function retry() {
    if (failure?.code === 'expired') { setFailure(null); onExpired(); return }
    const reason = failure
    setFailure(null)
    if (!identity) { setChecking(true); setAttempt(value => value + 1) }
    else if (reason?.code === 'duplicate' || reason?.code === 'validation') {
      move(reason.fields.name || reason.fields.phone ? 1 : 2)
      rejectInput(reason.fields)
    } else void submit()
  }

  if (receipt) return <OwnerPending receipt={receipt} />
  const titles = ['점주님의 정보를 알려주세요', '우리 매장을 등록해 주세요', '입력한 내용을 확인해 주세요']
  const descriptions = ['매장 운영에 사용할 기본 정보를 입력해 주세요.', '월계1동에 있는 매장만 등록할 수 있어요.', '확인 후 매장 등록을 신청해 주세요.']
  const fields = { draft: state.draft, errors, onChange: change, onBlur: (key: keyof OwnerDraft) => {
    const fieldErrors = validateOwner(state.draft, key === 'name' || key === 'phone' ? 1 : 2)
    setErrors(previous => ({ ...previous, [key]: fieldErrors[key] }))
  } }
  return <>
    {identity && !homeMode ? <OwnerStep step={state.step} title={titles[state.step - 1]} description={descriptions[state.step - 1]} action={state.step === 3 ? '매장 등록 신청' : state.step === 2 || editing ? '입력 내용 확인' : '다음'} busy={busy} onBack={back} onNext={next}>
      {state.step === 1 ? <OwnerBasic {...fields} email={identity.email} /> : state.step === 2 ? <OwnerStore {...fields} addressSearch={addressSearch} /> : <OwnerReview draft={state.draft} email={identity.email} busy={busy} onEdit={step => { setEditing(true); move(step) }} />}
    </OwnerStep> : <MobileLayout className="owner-signup" header={<AppBar title={homeMode ? '지단' : '점주 가입'} onBack={onBack} />}><p role="status">{identity ? '점주 홈을 준비하고 있어요.' : checking ? '가입 정보를 확인하고 있어요.' : '가입 정보를 불러오지 못했어요.'}</p>{!identity && !checking && <Button onClick={retry}>다시 시도</Button>}</MobileLayout>}
    <Modal open={failure !== null} state="error" title={messages[failure?.code || 'network'][0]} description={messages[failure?.code || 'network'][1]} cancelLabel="닫기" confirmLabel={failure?.code === 'expired' ? '다시 로그인' : failure?.code === 'duplicate' || failure?.code === 'validation' ? '입력 수정' : '다시 시도'} onClose={closeError} onConfirm={retry} />
  </>
}

import { useEffect, useRef, useState } from 'react'
import { DeadlineExceeded, withDeadline } from '../async/deadline'
import { AppBar } from '../ui/AppBar'
import { Button } from '../ui/Button'
import { MobileLayout } from '../ui/MobileLayout'
import { WorkerBasic } from '../registration/worker/WorkerBasic'
import { WorkerCompleteContent } from '../registration/worker/WorkerReview'
import { WorkerFrame } from '../registration/worker/WorkerFrame'
import { CareerEditor, WorkerExperience } from '../registration/worker/WorkerExperience'
import { AvailabilityEditor, WorkerAvailability } from '../registration/worker/WorkerAvailability'
import { careerPeriod, daysText, emptyAvailability, emptyCareer, rangeText, validateWorker, weekHours, type Errors, type WorkerDraft } from '../registration/worker/model'
import { profileService, type ProfileService, type WorkerProfileData } from './service'
import back from '../registration/worker/assets/back.svg'
import './WorkerProfile.css'
type Section = 1 | 2 | 3
type Editor = { kind: 'career' | 'time'; index: number } | null
const descriptions = {
  1: ['기본 정보 수정', '반가워요! 기본 정보를 알려주세요', '매장과 연락할 때 필요한 정보예요.'],
  2: ['근무 정보 수정', '어떤 일을 해보셨나요?', '근무 경력을 알려주세요.'],
  3: ['가능 시간 수정', '마지막 단계예요!', '근무 가능 시간을 선택해 주세요.'],
} as const
export function WorkerProfile({ initialProfile, onBack, onSaved, service = profileService }: {
  initialProfile: WorkerProfileData; onBack: () => void; onSaved?: (profile:WorkerProfileData)=>void; service?: ProfileService
}) {
  const [complete, setComplete] = useState(false)
  const [profile, setProfile] = useState(() => structuredClone(initialProfile))
  const [section, setSection] = useState<Section | null>(null)
  const [draft, setDraft] = useState<WorkerDraft | null>(null)
  const [editor, setEditor] = useState<Editor>(null)
  const [errors, setErrors] = useState<Errors>({})
  const [busy, setBusy] = useState(false), [failure, setFailure] = useState('')
  const request = useRef<AbortController | null>(null), locked = useRef(false)
  useEffect(() => () => { request.current?.abort() }, [])
  function edit(value: Section) {
    setSection(value); setDraft(structuredClone(profile.draft)); setErrors({}); setFailure('')
  }
  function cancel() {
    request.current?.abort(); locked.current = false; setBusy(false)
    setSection(null); setDraft(null); setEditor(null); setErrors({}); setFailure('')
  }
  function change(patch: Partial<WorkerDraft>) {
    if (locked.current) return
    setDraft(value => value ? { ...value, ...patch } : null); setErrors({}); setFailure('')
  }
  async function save() {
    if (!draft || !section || locked.current) return
    const validation = validateWorker(draft, 4)
    setErrors(validation)
    if (Object.keys(validation).length) { setFailure('입력 내용과 등록된 정보를 확인해 주세요.'); return }
    const next = { ...profile, draft: { ...draft, name: draft.name.trim(), careers: draft.experience === '신입' ? [] : draft.careers } }
    locked.current = true; setBusy(true); setFailure('')
    const controller = new AbortController(); request.current = controller
    try {
      await withDeadline(signal => service.save(structuredClone(next), signal), controller)
      if (controller.signal.aborted) return
      setProfile(next); cancel(); setComplete(true); onSaved?.(structuredClone(next))
    } catch (failure) { if (!controller.signal.aborted || failure instanceof DeadlineExceeded) setFailure('변경 사항을 저장하지 못했어요. 다시 시도해 주세요.') }
    finally { if (request.current === controller && (!controller.signal.aborted || controller.signal.reason instanceof DeadlineExceeded)) { locked.current = false; setBusy(false) } }
  }
  if (draft && editor?.kind === 'career') return <CareerEditor key={editor.index} value={draft.careers[editor.index] || emptyCareer} birth={draft.birth} onBack={() => setEditor(null)} onSave={career => { change({ careers: editor.index < 0 ? [...draft.careers, career] : draft.careers.map((c,i) => i === editor.index ? career : c) }); setEditor(null) }} />
  if (draft && editor?.kind === 'time') return <AvailabilityEditor key={editor.index} value={draft.availability[editor.index] || emptyAvailability} others={draft.availability.filter((_,i) => i !== editor.index)} onBack={() => setEditor(null)} onSave={value => { change({ availability: editor.index < 0 ? [...draft.availability,value] : draft.availability.map((a,i) => i === editor.index ? value : a) }); setEditor(null) }} />
  if (section && draft) return <WorkerFrame title={descriptions[section][0]} heading={descriptions[section][1]} description={descriptions[section][2]} action="변경 사항 저장" busy={busy} onBack={cancel} onNext={() => void save()}>
    <fieldset disabled={busy} className="worker-fields-boundary">
      {section === 1 && <WorkerBasic reserveEmailSpace={false} draft={draft} email={profile.email} errors={errors} change={change} blur={key => setErrors(e => ({ ...e, [key]: validateWorker(draft,1)[key] || '' }))} />}
      {section === 2 && <WorkerExperience draft={draft} errors={errors} change={change} edit={index => setEditor({ kind:'career',index })} />}
      {section === 3 && <WorkerAvailability values={draft.availability} error={errors.availability} change={availability => change({ availability })} edit={index => setEditor({ kind:'time',index })} />}
    </fieldset>
    {failure && <p role="alert" className="worker-error">{failure}</p>}
  </WorkerFrame>
  if (complete) return <WorkerFrame complete title="등록 완료" heading="프로필 수정이 완료됐어요" description="이제 내 일정에 맞는 공고를 찾아보세요." action="돌아가기" onBack={()=>setComplete(false)} onNext={()=>setComplete(false)}><WorkerCompleteContent draft={profile.draft}/></WorkerFrame>
  const value = profile.draft
  return <MobileLayout className="worker-profile worker-signup" header={<AppBar compact backIcon={back} title="내 프로필" onBack={onBack} />} footer={<Button onClick={() => edit(1)}>프로필 수정</Button>}>
    <div className="worker-profile-content">
      <div className="worker-profile-intro"><h2>{value.name} 님</h2><p>{profile.email}</p></div>
      <article className="worker-card"><header><h3>기본 정보</h3><button type="button" className="worker-link" aria-label="기본 정보 수정" onClick={() => edit(1)}>수정</button></header><dl>{[['전화번호',value.phone],['생년월일',value.birth.replaceAll('-','. ')],['성별',value.gender]].map(([label,text]) => <div key={label}><dt>{label}</dt><dd>{text}</dd></div>)}</dl></article>
      <article className="worker-card"><header><h3>근무 정보</h3><button type="button" className="worker-link" aria-label="근무 정보 수정" onClick={() => edit(2)}>수정</button></header><dl><div><dt>경력</dt><dd>{value.experience}</dd></div></dl>{value.experience === '경력 있음' && value.careers.map(c => <div key={c.id}><p>{c.industry} · {c.duties}</p><small>{careerPeriod(c)}{c.store && ` · ${c.store}`}</small></div>)}</article>
      <article className="worker-card"><header><h3>가능한 시간</h3><button type="button" className="worker-link" aria-label="가능한 시간 수정" onClick={() => edit(3)}>수정</button></header>{value.availability.map(a => <p key={a.id}>{daysText(a.days)}　{rangeText(a)}</p>)}<small>매주 반복 · 주 {weekHours(value.availability)}시간</small></article>
    </div>
  </MobileLayout>
}

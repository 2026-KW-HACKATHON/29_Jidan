import { useRef, useState } from 'react'
import type { FormEvent, ReactNode } from 'react'
import { useApp } from '../state'
import type { Availability } from '../state'
import { days, dayNumbers, dayName, minutes, time, normalizeAvailability, weeklyHours, groupedTimes } from './availability'
import { Button, Card, Field, Heading } from '../ui'
import './MemberPages.css'

type Career = { id: string; industry: string; task: string; start: string; end: string; current: boolean; storeName?: string }
const uid = () => crypto.randomUUID()
const dateLabel = (value: string) => value.replaceAll('-', '. ')
const phoneLabel = (value: string) => value.replace(/\D/g, '').replace(/^(\d{3})(\d{3,4})(\d{4})$/, '$1-$2-$3')
function careerDuration(career: Career) {
  const [startYear, startMonth] = career.start.split('-').map(Number)
  const [endYear, endMonth] = (career.current ? new Date().toISOString().slice(0, 7) : career.end).split('-').map(Number)
  const months = (endYear - startYear) * 12 + endMonth - startMonth + 1
  if (!Number.isFinite(months) || months < 1) return ''
  return `${Math.floor(months / 12) ? `${Math.floor(months / 12)}년` : ''}${months % 12 ? ` ${months % 12}개월` : ''}`.trim()
}

function Progress({ step }: { step: number }) {
  return <div className="member-progress" aria-label={`프로필 등록 ${step}/3단계`}>
    {['기본 정보', '근무 정보', '가능 시간'].map((label, index) => <div key={label} className={index + 1 === step ? 'current' : ''}>
      <span>{index + 1}. {label}</span><i className={index < step ? 'filled' : ''} />
    </div>)}
  </div>
}

function Page({ children, action, complete = false }: { children: ReactNode; action?: ReactNode; complete?: boolean }) {
  return <div className={`member-page${complete ? ' member-complete' : ''}`}>
    <div className="page-content member-content">{children}</div>
    {action && <div className="member-actions">{action}</div>}
  </div>
}

function ErrorText({ children }: { children: ReactNode }) {
  return children ? <p role="alert" className="member-error">{children}</p> : null
}

function BasicPage({ editing }: { editing: boolean }) {
  const { data, update, go, notify } = useApp()
  const [values, setValues] = useState(data.profile)
  const [error, setError] = useState('')
  const change = (key: 'name' | 'phone' | 'birthDate' | 'gender', value: string) => {
    setValues(old => ({ ...old, [key]: value })); setError('')
  }
  function save(event: FormEvent) {
    event.preventDefault()
    if (!values.name.trim()) return setError('이름을 입력해 주세요.')
    if (!/^01[016789]\d{7,8}$/.test(values.phone.replace(/\D/g, ''))) return setError('전화번호를 확인해 주세요. 예: 010-1234-5678')
    const birth = new Date(`${values.birthDate}T00:00:00`)
    if (!/^\d{4}-\d{2}-\d{2}$/.test(values.birthDate) || Number.isNaN(birth.getTime()) || birth > new Date() || birth.getFullYear() < 1900 || `${birth.getFullYear()}-${String(birth.getMonth() + 1).padStart(2, '0')}-${String(birth.getDate()).padStart(2, '0')}` !== values.birthDate) return setError('올바른 생년월일을 입력해 주세요.')
    if (!values.gender) return setError('성별을 선택해 주세요.')
    update(old => ({ ...old, profile: { ...old.profile, name: values.name.trim(), phone: phoneLabel(values.phone), birthDate: values.birthDate, gender: values.gender } }))
    if (editing) notify('기본 정보를 저장했어요.')
    go(editing ? '/profile' : finishReviewEdit('/signup/work'))
  }
  return <form onSubmit={save} noValidate>
    <Page action={<Button type="submit">{editing ? '변경 사항 저장' : '다음'}</Button>}>
      {!editing && <Progress step={1} />}
      <Heading title="반가워요! 기본 정보를 알려주세요" description="매장과 연락할 때 필요한 정보예요." />
      <div className="member-fields">
        <Field label="이름 *" name="name" autoComplete="name" value={values.name} onChange={event => change('name', event.target.value)} />
        <Field label="이메일 | Google 연동" name="email" value={values.email} readOnly />
        <Field label="전화번호 *" name="phone" type="tel" autoComplete="tel" placeholder="010-1234-5678" value={values.phone} onChange={event => change('phone', event.target.value)} />
        <Field label="생년월일 *" name="birthDate" type="date" min="1900-01-01" max={new Date().toISOString().slice(0, 10)} value={values.birthDate} onChange={event => change('birthDate', event.target.value)} />
        <fieldset className="member-choice-field"><legend>성별 *</legend><div className="member-choices">
          {['남성', '여성'].map(gender => <button type="button" key={gender} aria-pressed={values.gender === gender} className={`member-choice ${values.gender === gender ? 'selected' : ''}`} onClick={() => change('gender', gender)}>{gender}</button>)}
        </div></fieldset>
      </div>
      <ErrorText>{error}</ErrorText>
    </Page>
  </form>
}

function WorkPage({ editing }: { editing: boolean }) {
  const { data, update, go, notify } = useApp()
  const [error, setError] = useState('')
  const profile = data.profile
  function openCareer(career?: Career) {
    sessionStorage.setItem('jidan-career-edit', career?.id || '')
    go(editing ? '/profile/career' : '/signup/career')
  }
  function save() {
    if (profile.experience === 'experienced' && !profile.careers.length) return setError('경력을 한 건 이상 추가해 주세요.')
    if (editing) notify('근무 정보를 저장했어요.')
    go(editing ? '/profile' : finishReviewEdit('/signup/availability'))
  }
  return <Page action={<Button onClick={save}>{editing ? '변경 사항 저장' : '다음'}</Button>}>
    {!editing && <Progress step={2} />}
    <Heading title="어떤 일을 해보셨나요?" description="근무 경력을 알려주세요." />
    <div className="member-fields">
      <fieldset className="member-choice-field"><legend>근무 경력 *</legend><div className="member-choices">
        {([['new', '신입'], ['experienced', '경력 있음']] as const).map(([value, label]) => <button type="button" key={value} className={`member-choice ${profile.experience === value ? 'selected' : ''}`} aria-pressed={profile.experience === value} onClick={() => { update(old => ({ ...old, profile: { ...old.profile, experience: value } })); setError('') }}>{label}</button>)}
      </div></fieldset>
      {profile.experience === 'new' ? <p className="member-help">처음이어도 괜찮아요.<br />배우고 싶은 일부터 시작해 보세요.</p> : <>
        {profile.careers.map(career => <Card key={career.id} className="member-summary-card">
          <strong>{career.industry} · {career.task}</strong>
          <p className="muted">{dateLabel(career.start)} – {career.current ? '현재 근무 중' : dateLabel(career.end)} · {careerDuration(career)}</p>
          <div className="member-inline-actions"><button type="button" className="member-text-button" onClick={() => openCareer(career)}>경력 수정</button><button type="button" className="member-text-button muted" aria-label={`${career.industry} 경력 삭제`} onClick={() => { update(old => ({ ...old, profile: { ...old.profile, careers: old.profile.careers.filter(item => item.id !== career.id) } })); notify('경력을 삭제했어요.') }}>삭제</button></div>
        </Card>)}
        <Button variant="secondary" onClick={() => openCareer()}>+ 경력 추가</Button>
      </>}
    </div>
    <ErrorText>{error}</ErrorText>
  </Page>
}

function CareerPage({ editing }: { editing: boolean }) {
  const { data, update, go, notify } = useApp()
  const id = sessionStorage.getItem('jidan-career-edit')
  const existing = data.profile.careers.find(value => value.id === id) as Career | undefined
  const [career, setCareer] = useState<Career>(existing || { id: uid(), industry: '카페', task: '', start: '', end: '', current: false, storeName: '' })
  const [error, setError] = useState('')
  const change = (field: keyof Career, value: string | boolean) => { setCareer(old => ({ ...old, [field]: value })); setError('') }
  function save(event: FormEvent) {
    event.preventDefault()
    if (!career.task.trim()) return setError('담당 업무를 입력해 주세요.')
    const now = new Date().toISOString().slice(0, 7)
    if (!career.start || career.start > now) return setError('시작 연월을 확인해 주세요.')
    if (!career.current && (!career.end || career.end < career.start || career.end > now)) return setError('종료 연월은 시작 연월 이후, 현재 이전으로 입력해 주세요.')
    const saved = { ...career, task: career.task.trim(), end: career.current ? '' : career.end }
    update(old => ({ ...old, profile: { ...old.profile, experience: 'experienced', careers: [...old.profile.careers.filter(value => value.id !== saved.id), saved] } }))
    notify('경력을 저장했어요.')
    go(editing ? '/profile/work' : '/signup/work')
  }
  return <form onSubmit={save} noValidate><Page action={<Button type="submit">경력 저장</Button>}>
    <Heading title="해보신 일을 알려주세요" description="업종과 담당 업무를 중심으로 작성해 주세요." />
    <label className="member-select-field">업종 *<select value={career.industry} onChange={event => change('industry', event.target.value)}>{['카페', '편의점', '음식점', '베이커리', '판매·서비스', '기타'].map(value => <option key={value}>{value}</option>)}</select></label>
    <Field label="담당 업무 *" name="task" value={career.task} placeholder="음료 제조, 고객 응대" onChange={event => change('task', event.target.value)} />
    <Field label="매장명 · 선택" name="storeName" value={career.storeName || ''} placeholder="근무했던 매장명" onChange={event => change('storeName', event.target.value)} />
    <div className="member-field-pair"><Field label="시작 연월 *" name="startMonth" type="month" max={new Date().toISOString().slice(0, 7)} value={career.start} onChange={event => change('start', event.target.value)} /><Field label="종료 연월 *" name="endMonth" type="month" value={career.end} disabled={career.current} min={career.start} max={new Date().toISOString().slice(0, 7)} onChange={event => change('end', event.target.value)} /></div>
    <label className="member-checkbox"><input type="checkbox" checked={career.current} onChange={event => change('current', event.target.checked)} />현재 근무 중</label>
    <p className="member-help">다른 업종의 경험도 등록할 수 있어요.<br />여러 경력은 한 건씩 추가해 주세요.</p>
    <ErrorText>{error}</ErrorText>
  </Page></form>
}

function AvailabilityGrid({ values, onChange }: { values: Availability[]; onChange: (values: Availability[]) => void }) {
  const drag = useRef<{ day: number; start: number; end: number; remove: boolean; initial: Availability[] } | null>(null)
  const normalized = normalizeAvailability(values)
  const selected = (day: number, slot: number) => normalized.some(value => value.day === day && minutes(value.start) <= slot * 30 && minutes(value.end) > slot * 30)
  function paint(day: number, end: number) {
    const current = drag.current
    if (!current || day !== current.day) return
    current.end = end
    const from = Math.min(current.start, end), to = Math.max(current.start, end) + 1
    if (!current.remove) return onChange(normalizeAvailability([...current.initial, { id: uid(), day, start: time(from * 30), end: time(to * 30) }]))
    onChange(normalizeAvailability(current.initial).flatMap(value => {
      if (value.day !== day || minutes(value.end) <= from * 30 || minutes(value.start) >= to * 30) return [value]
      const left = minutes(value.start) < from * 30 ? [{ ...value, end: time(from * 30) }] : []
      const right = minutes(value.end) > to * 30 ? [{ ...value, id: uid(), start: time(to * 30) }] : []
      return [...left, ...right]
    }))
  }
  return <Card className="member-time-card"><div className="member-time-head"><span>시간</span>{days.map(day => <strong key={day}>{day}</strong>)}</div>
    <div className="member-time-grid" aria-label="주간 근무 가능 시간표" onPointerUp={() => { drag.current = null }} onPointerCancel={() => { drag.current = null }} onPointerMove={event => {
      if (!drag.current) return
      const cell = document.elementFromPoint(event.clientX, event.clientY)?.closest<HTMLButtonElement>('[data-time-slot]')
      if (cell) paint(Number(cell.dataset.day), Number(cell.dataset.timeSlot))
    }}>
      {Array.from({ length: 36 }, (_, index) => index + 12).map(slot => <div className="member-time-row" key={slot}>
        <span>{slot % 2 === 0 ? String(slot / 2).padStart(2, '0') : ''}</span>
        {dayNumbers.map(index => <button type="button" key={index} data-day={index} data-time-slot={slot} className={selected(index, slot) ? 'selected' : ''} aria-label={`${dayName(index)}요일 ${time(slot * 30)}–${time((slot + 1) * 30)}`} aria-pressed={selected(index, slot)} onPointerDown={event => {
          event.preventDefault(); event.currentTarget.setPointerCapture(event.pointerId)
          drag.current = { day: index, start: slot, end: slot, remove: selected(index, slot), initial: values }; paint(index, slot)
        }} onClick={event => { if (event.detail === 0) { drag.current = { day: index, start: slot, end: slot, remove: selected(index, slot), initial: values }; paint(index, slot); drag.current = null } }} />)}
      </div>)}
    </div><p className="member-grid-key">파란색 · 근무 가능한 시간</p>
  </Card>
}

function AvailabilityPage({ editing }: { editing: boolean }) {
  const { data, update, go, notify } = useApp()
  const [error, setError] = useState('')
  const [showTimes, setShowTimes] = useState(false)
  const values = data.profile.availability
  function change(availability: Availability[]) { update(old => ({ ...old, profile: { ...old.profile, availability } })); setError('') }
  function edit(value?: Availability) { sessionStorage.setItem('jidan-time-edit', value?.id || ''); go(editing ? '/profile/time' : '/signup/time') }
  const add = <Button variant="secondary" onClick={() => edit()}>+ 시간 추가</Button>
  const help = <p className="muted small">드래그해 30분 단위로 선택할 수 있어요.<br />심야 시간은 시간 추가에서 입력 가능해요.</p>
  return <Page action={<Button onClick={() => {
    if (!values.length) return setError('근무 가능한 시간을 한 구간 이상 선택해 주세요.')
    if (editing) notify('가능 시간을 저장했어요.')
    go(editing ? '/profile' : finishReviewEdit('/signup/review'))
  }}>{editing ? '변경 사항 저장' : '입력 내용 확인'}</Button>}>
    {!editing && <Progress step={3} />}
    <Heading title="마지막 단계예요!" description="근무 가능 시간을 선택해 주세요." />
    {!editing && <>{add}{help}</>}
    <AvailabilityGrid values={values} onChange={change} />
    {editing && <>{add}{help}</>}
    <Card className="member-summary-card"><strong>{editing ? '근무 가능 시간' : `선택한 시간 · 주 ${weeklyHours(values)}시간`}</strong>
      {values.length ? groupedTimes(values).map(value => <p className="muted" key={value}>{value}</p>) : <p className="muted">아직 선택한 시간이 없어요.</p>}
      {!!values.length && <button type="button" className="member-text-button" onClick={() => setShowTimes(!showTimes)}>{showTimes ? '접기' : '시간 수정'}</button>}
      {showTimes && <div className="member-time-list">{values.map(value => <div key={value.id}><span>{dayName(value.day)} {value.start}–{value.end}{minutes(value.end) <= minutes(value.start) ? ' (다음 날)' : ''}</span><div className="member-inline-actions"><button type="button" className="member-text-button" aria-label={`${dayName(value.day)} ${value.start} 시간 수정`} onClick={() => edit(value)}>수정</button><button type="button" className="member-text-button muted" aria-label={`${dayName(value.day)} ${value.start} 시간 삭제`} onClick={() => change(values.filter(item => item.id !== value.id))}>삭제</button></div></div>)}</div>}
    </Card>
    <ErrorText>{error}</ErrorText>
  </Page>
}

function TimePage({ editing }: { editing: boolean }) {
  const { data, update, go, notify } = useApp()
  const id = sessionStorage.getItem('jidan-time-edit')
  const existing = data.profile.availability.find(value => value.id === id)
  const [selectedDays, setSelectedDays] = useState(existing ? [existing.day] : [1, 3, 5])
  const [start, setStart] = useState(existing?.start || '09:00')
  const [end, setEnd] = useState(existing?.end === '24:00' ? '00:00' : existing?.end || '14:00')
  const [overnight, setOvernight] = useState(!!existing && (existing.end === '24:00' || minutes(existing.end) <= minutes(existing.start)))
  const [error, setError] = useState('')
  const duration = (minutes(end) + (overnight ? 1440 : 0) - minutes(start)) / 60
  function save(event: FormEvent) {
    event.preventDefault()
    if (!selectedDays.length) return setError('가능한 요일을 선택해 주세요.')
    if (!start || !end || minutes(start) % 30 || minutes(end) % 30) return setError('시간은 30분 단위로 입력해 주세요.')
    if (duration <= 0 || duration > 24) return setError('종료 시간은 시작 이후, 24시간 이내로 선택해 주세요. 자정을 넘으면 다음 날 종료를 선택하세요.')
    const added = selectedDays.map(day => ({ id: uid(), day, start, end: overnight && end === '00:00' ? '24:00' : end }))
    const availability = normalizeAvailability([...data.profile.availability.filter(value => value.id !== id), ...added])
    update(old => ({ ...old, profile: { ...old.profile, availability } }))
    notify(existing ? '시간을 수정했어요.' : '시간을 추가했어요. 겹치는 시간은 합쳐집니다.')
    go(editing ? '/profile/availability' : '/signup/availability')
  }
  return <form onSubmit={save} noValidate><Page action={<Button type="submit">{existing ? '시간 수정' : '시간 추가'}</Button>}>
    <Heading title="요일과 시간을 선택해 주세요" description="같은 시간을 여러 요일에 한 번에 적용할 수 있어요." />
    <fieldset className="member-choice-field"><legend>가능한 요일 *</legend><div className="member-days">{dayNumbers.map(index => <button type="button" key={index} className={`member-choice ${selectedDays.includes(index) ? 'selected' : ''}`} aria-pressed={selectedDays.includes(index)} onClick={() => { setSelectedDays(old => old.includes(index) ? old.filter(value => value !== index) : [...old, index].sort((a, b) => dayNumbers.indexOf(a) - dayNumbers.indexOf(b))); setError('') }}>{dayName(index)}</button>)}</div></fieldset>
    <div className="member-field-pair"><Field label="시작 시간 *" name="startTime" type="time" step="1800" value={start} onChange={event => { setStart(event.target.value); setError('') }} /><Field label="종료 시간 *" name="endTime" type="time" step="1800" value={end} onChange={event => { setEnd(event.target.value); setError('') }} /></div>
    <label className="member-checkbox"><input type="checkbox" checked={overnight} onChange={event => { setOvernight(event.target.checked); setError('') }} />다음 날 종료</label>
    <p className="member-help">30분 단위로 등록해 주세요.<br />자정을 넘겨 일할 수 있다면 ‘다음 날 종료’를 선택하세요.</p>
    <Card className="member-summary-card"><strong>매주 {selectedDays.map(dayName).join(' · ') || '요일 선택'}</strong><p className="muted">{start}–{overnight ? '다음 날 ' : ''}{end}{duration > 0 && duration <= 24 ? ` · 하루 ${duration}시간` : ''}</p></Card>
    <ErrorText>{error}</ErrorText>
  </Page></form>
}

function DetailRow({ label, value }: { label: string; value: string }) {
  return <div className="member-detail-row"><span className="muted">{label}</span><span>{value || '미입력'}</span></div>
}

function finishReviewEdit(fallback: string) {
  const reviewing = sessionStorage.getItem('jidan-review-edit') === 'true'
  sessionStorage.removeItem('jidan-review-edit')
  return reviewing ? '/signup/review' : fallback
}

function ProfileCards({ review = false }: { review?: boolean }) {
  const { data, go } = useApp()
  const profile = data.profile
  const path = review ? '/signup' : '/profile'
  const edit = (section: string) => <button type="button" className="member-text-button" aria-label={`${section === 'basic' ? '기본 정보' : section === 'work' ? '근무 정보' : '가능한 시간'} 수정`} onClick={() => { if (review) sessionStorage.setItem('jidan-review-edit', 'true'); go(`${path}/${section}`) }}>수정</button>
  return <>
    <Card className="member-summary-card"><div className="row between"><h2>기본 정보</h2>{edit('basic')}</div>
      {review && <DetailRow label="이름" value={profile.name} />}
      <DetailRow label="전화번호" value={phoneLabel(profile.phone)} /><DetailRow label="생년월일" value={dateLabel(profile.birthDate)} /><DetailRow label="성별" value={profile.gender} />
      {review && <p className="muted small">Google · {profile.email}</p>}
    </Card>
    <Card className="member-summary-card"><div className="row between"><h2>근무 정보</h2>{edit('work')}</div><DetailRow label="경력" value={profile.experience === 'new' ? '신입' : `경력 ${profile.careers.length}건`} />
      {profile.experience === 'experienced' && profile.careers.map(career => <div className="member-career-detail" key={career.id}><strong>{career.industry} · {career.task}</strong><p className="muted small">{dateLabel(career.start)} – {career.current ? '현재' : dateLabel(career.end)}</p></div>)}
    </Card>
    <Card className="member-summary-card"><div className="row between"><h2>가능한 시간</h2>{edit('availability')}</div>
      {groupedTimes(profile.availability).map(value => <p key={value}>{value}</p>)}
      {!profile.availability.length && <p className="muted">등록된 시간이 없어요.</p>}
      <p className="muted small">매주 반복 · 주 {weeklyHours(profile.availability)}시간</p>
    </Card>
  </>
}

function ReviewPage() {
  const { update, go, notify } = useApp()
  return <Page action={<Button onClick={() => { update(old => ({ ...old, role: 'member' })); notify('프로필 등록을 완료했어요.'); go('/signup/complete') }}>프로필 등록 완료</Button>}>
    <Heading title="프로필을 확인해 주세요" description="등록 후에도 내 프로필에서 수정할 수 있어요." />
    <ProfileCards review />
  </Page>
}

function CompletePage() {
  const { data, go } = useApp()
  return <Page complete action={<Button onClick={() => go('/home')}>지단 시작하기</Button>}>
    <div className="member-status-icon"><img src="/figma/member-information.svg" alt="" /></div>
    <Heading title="프로필 등록이 완료됐어요" description="이제 내 일정에 맞는 공고를 찾아보세요." />
    <div><Card className="member-summary-card"><h2>{data.profile.name} 님의 근무 프로필</h2><p className="muted">{data.profile.experience === 'new' ? '신입' : `경력 ${data.profile.careers.length}건`}</p>{groupedTimes(data.profile.availability).map(value => <p className="muted" key={value}>{value}</p>)}</Card><Button variant="secondary" onClick={() => go('/profile')}>내 프로필 보기</Button></div>
  </Page>
}

function ProfilePage() {
  const { data, update, go, notify } = useApp()
  const owner = data.role === 'owner'
  return <Page action={owner ? undefined : <Button onClick={() => go('/profile/basic')}>프로필 수정</Button>}>
    <Heading title={`${owner ? data.store.ownerName : data.profile.name} 님`} description={owner ? 'owner@example.com' : data.profile.email} />
    {owner ? <Card className="member-summary-card"><h2>점주 계정</h2><p className="muted">매장과 근무자를 관리하고 온보딩 자료를 준비해 보세요.</p><Button variant="secondary" onClick={() => go('/home')}>내 매장으로 이동</Button></Card> : <ProfileCards />}
    <details className="member-demo-options"><summary>프로토타입 계정 전환</summary><Button variant="secondary" onClick={() => { update(old => ({ ...old, role: owner ? 'member' : 'owner' })); notify(`${owner ? '일반회원' : '점주'} 화면으로 전환했어요.`); go('/home') }}>{owner ? '일반회원으로' : '점주로'} 둘러보기</Button><Button variant="ghost" onClick={() => { update(old => ({ ...old, role: null })); go('/') }}>로그아웃</Button></details>
  </Page>
}

export function MemberPages({ route }: { route: string }) {
  const editing = route.startsWith('/profile')
  if (route.endsWith('/basic')) return <BasicPage key={route} editing={editing} />
  if (route.endsWith('/career')) return <CareerPage key={route} editing={editing} />
  if (route.endsWith('/work')) return <WorkPage key={route} editing={editing} />
  if (route.endsWith('/availability')) return <AvailabilityPage key={route} editing={editing} />
  if (route.endsWith('/time')) return <TimePage key={route} editing={editing} />
  if (route.endsWith('/review')) return <ReviewPage />
  if (route.endsWith('/complete')) return <CompletePage />
  return <ProfilePage />
}

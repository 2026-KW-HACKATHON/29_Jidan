import { useState, type FormEvent, type ReactNode } from 'react'
import { useApp } from '../state'
import { Badge, Button, Card, Empty, Field, Heading, Modal } from '../ui'
import './StorePages.css'

const assets = { plus: '/figma/store-plus.svg', user: '/figma/store-user.svg', chevron: '/figma/store-chevron.svg', check: '/figma/store-check.svg', select: '/figma/store-select-chevron.svg' }
const TASKS = ['홀 서빙', '주방 보조', '카운터', '상품 입고·진열', '매장 마감']
const today = () => new Date().toISOString().slice(0, 10)
const dateText = (value: string) => value ? new Date(value).toLocaleDateString('ko-KR', { year: 'numeric', month: '2-digit', day: '2-digit' }).replace(/\.$/, '') : '별도 종료 전까지'
const isExpired = (createdAt: string) => Date.now() > new Date(createdAt).getTime() + 7 * 24 * 60 * 60 * 1000
const shortDate = (value: string) => value ? new Date(value).toLocaleDateString('ko-KR', { month: 'long', day: 'numeric' }) : ''
const workerActive = (worker: { status: string; type: string; endDate: string }) => worker.status === 'active' && (worker.type === 'regular' || (!!worker.endDate && new Date(worker.endDate).getTime() > Date.now()))

function DetailRow({ label, children }: { label: string; children: ReactNode }) {
  return <div className="store-detail-row"><span>{label}</span><span>{children}</span></div>
}

function StoreHome() {
  const { data, go } = useApp()
  const active = data.workers.filter(workerActive)
  const pending = data.invites.filter(invite => invite.status === 'pending' && !isExpired(invite.createdAt))
  const isPending = data.store.status === 'pending'
  return <div className="page-content store-home">
    <Heading title={data.store.name} description="우리 매장 운영을 한눈에 확인하세요." />
    {isPending && <Card className="store-pending"><Badge tone="yellow">확인 대기</Badge><h3>매장 정보를 확인하고 있어요</h3><p className="muted">월계1동 소재와 관리 권한 확인이 완료되면 근무자 초대와 매장 운영을 시작할 수 있어요.</p><Button variant="secondary" onClick={() => go('/store/new')}>등록 정보 확인</Button></Card>}
    <Card className="store-statistics"><h3>운영 현황 요약</h3><div className="store-metrics">
      <button onClick={() => go('/jobs')}><span>모집 중 공고</span><strong>{isPending ? 0 : data.jobs.filter(job => job.status === 'open').length}건</strong></button>
      <button onClick={() => go('/invite')}><span>확인 필요</span><strong className="store-primary-text">{pending.length}건</strong></button>
      <button onClick={() => go('/manuals')}><span>승인 대기</span><strong>{data.manuals.filter(manual => manual.status === 'draft').length}건</strong></button>
    </div></Card>
    <section className="store-management"><h2 className="section-title">근무자 관리</h2>
      <button className="store-menu-card" disabled={isPending} onClick={() => go('/invite')}><img src={assets.plus} alt="" /><span className="store-menu-copy"><strong>근무자 초대</strong><span>대기 중 초대 {pending.length}건</span><b>초대하기</b></span><img src={assets.chevron} alt="" /></button>
      <button className="store-menu-card" disabled={isPending} onClick={() => go('/workers')}><img src={assets.user} alt="" /><span className="store-menu-copy"><strong>근무 상태 관리</strong><span>재직 중 {active.length}명 · 만료 예정 {active.filter(worker => worker.type === 'temporary').length}명</span><b>관리하기</b></span><img src={assets.chevron} alt="" /></button>
    </section>
  </div>
}

function InvitePage() {
  const { data, update, go, notify } = useApp()
  const [email, setEmail] = useState('')
  const [tasks, setTasks] = useState(['홀 서빙'])
  const [error, setError] = useState('')
  const [createdId, setCreatedId] = useState('')
  const [cancelId, setCancelId] = useState('')
  const submit = (event: FormEvent) => {
    event.preventDefault()
    const normalized = email.trim().toLowerCase()
    if (!/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(normalized)) return setError('올바른 이메일 주소를 입력해 주세요.')
    if (!tasks.length) return setError('담당 업무를 한 개 이상 선택해 주세요.')
    if (data.store.status !== 'active') return setError('매장 확인이 완료된 후 초대할 수 있어요.')
    if (data.workers.some(worker => worker.email.toLowerCase() === normalized && workerActive(worker) && tasks.every(task => worker.task.split(',').map(item => item.trim()).includes(task)))) return setError('이미 해당 업무를 맡고 있는 근무자예요.')
    if (data.invites.some(invite => invite.email.toLowerCase() === normalized && invite.task === tasks.join(', ') && invite.status === 'pending' && !isExpired(invite.createdAt))) return setError('같은 업무의 대기 중인 초대가 있어요. 아래 목록에서 확인해 주세요.')
    const id = `invite-${crypto.randomUUID()}`
    update(current => ({ ...current, invites: [...current.invites, { id, name: normalized.split('@')[0], email: normalized, task: tasks.join(', '), status: 'pending', createdAt: new Date().toISOString() }] }))
    setCreatedId(id)
    setError('')
    setEmail('')
    notify('초대 링크를 만들었어요.')
  }
  const copy = async (id: string) => {
    try { await navigator.clipboard.writeText(`${window.location.origin}${window.location.pathname}#/invitation/${id}`); notify('초대 링크를 복사했어요.') } catch { notify('링크를 복사하지 못했어요. 초대 화면에서 주소를 복사해 주세요.') }
  }
  return <form className="store-flow-page" onSubmit={submit} noValidate>
    <div className="page-content store-form-content">
      <Heading title="함께 일할 근무자를 초대하세요" description={data.store.name} />
      <div><Field label="초대 대상 이메일 *" type="email" autoComplete="email" value={email} placeholder="예: jidan@email.com" onChange={event => { setEmail(event.target.value); setError('') }} aria-invalid={!!error} aria-describedby={error ? 'invite-error' : undefined} />{error && <p className="store-error" id="invite-error" role="alert">{error}</p>}</div>
      <Card className="store-guidance"><h3>초대 전 확인해 주세요</h3><p>근무자는 초대 링크를 통해 매장 온보딩에 접근할 수 있어요.</p><p className="small">초대 링크는 생성 후 7일 동안 유효해요.</p></Card>
      <details className="store-task-details"><summary>담당 업무 <span>{tasks.join(', ') || '선택해 주세요'}</span></summary><div className="store-task-options">{TASKS.map(task => <label key={task}><input type="checkbox" checked={tasks.includes(task)} onChange={event => setTasks(event.target.checked ? [...tasks, task] : tasks.filter(item => item !== task))} />{task}</label>)}</div></details>
      {data.invites.length > 0 && <section className="store-invites"><h2 className="section-title">초대 내역</h2>{[...data.invites].reverse().map(invite => <Card key={invite.id} className="store-invite-item"><div className="row between"><strong>{invite.email}</strong><Badge tone={invite.status === 'accepted' ? 'green' : invite.status === 'pending' && !isExpired(invite.createdAt) ? 'blue' : 'gray'}>{invite.status === 'accepted' ? '수락 완료' : invite.status === 'cancelled' ? '초대 취소' : isExpired(invite.createdAt) ? '기간 만료' : '수락 대기'}</Badge></div><p className="small muted">{invite.task} · {dateText(invite.createdAt)} 생성</p>{invite.status === 'pending' && !isExpired(invite.createdAt) && <div className="store-inline-actions"><button type="button" onClick={() => go(`/invitation/${invite.id}`)}>초대 확인</button><button type="button" onClick={() => void copy(invite.id)}>링크 복사</button><button type="button" className="store-danger-text" onClick={() => setCancelId(invite.id)}>취소</button></div>}</Card>)}</section>}
    </div>
    <div className="store-bottom-action"><Button type="submit" disabled={data.store.status !== 'active'}>초대 생성</Button></div>
    {createdId && <Modal title="초대 링크를 만들었어요" onClose={() => setCreatedId('')}><p className="muted">프로토타입에서는 이메일을 보내지 않아요. 링크로 초대 수락 흐름을 확인할 수 있어요.</p><div className="store-dialog-actions"><Button type="button" variant="secondary" onClick={() => void copy(createdId)}>링크 복사</Button><Button type="button" onClick={() => { go(`/invitation/${createdId}`); setCreatedId('') }}>초대 화면 보기</Button></div></Modal>}
    {cancelId && <Modal title="초대를 취소할까요?" onClose={() => setCancelId('')}><p className="muted">취소하면 이 링크로 매장에 참여할 수 없어요.</p><div className="store-dialog-actions"><Button type="button" variant="secondary" onClick={() => setCancelId('')}>돌아가기</Button><Button type="button" variant="danger" onClick={() => { update(current => ({ ...current, invites: current.invites.map(invite => invite.id === cancelId && invite.status === 'pending' ? { ...invite, status: 'cancelled' } : invite) })); setCancelId(''); notify('초대를 취소했어요.') }}>초대 취소</Button></div></Modal>}
  </form>
}

function InvitationPage({ id }: { id: string }) {
  const { data, update, go, notify } = useApp()
  const [decline, setDecline] = useState(false)
  const invite = data.invites.find(item => item.id === id)
  const invalid = !invite || invite.status === 'cancelled' || (invite.status === 'pending' && isExpired(invite.createdAt))
  const correctAccount = invite?.email.toLowerCase() === data.profile.email.toLowerCase()
  const accept = () => {
    if (!invite || invalid || !correctAccount || data.store.status !== 'active') return
    update(current => {
      const latest = current.invites.find(item => item.id === id)
      if (!latest || latest.status !== 'pending' || isExpired(latest.createdAt)) return current
      if (latest.email.toLowerCase() !== current.profile.email.toLowerCase()) return current
      return { ...current, role: 'member', invites: current.invites.map(item => item.id === id ? { ...item, status: 'accepted' } : item), workers: current.workers.some(worker => worker.id === `worker-${id}`) ? current.workers : [...current.workers, { id: `worker-${id}`, name: current.profile.name, email: invite.email, task: invite.task, type: 'regular', status: 'active', startDate: today(), endDate: '' }] }
    })
    notify('초대를 수락했어요. 매장 매뉴얼을 확인해 보세요.')
    go('/manuals')
  }
  if (invalid) return <div className="page-content store-form-content"><Empty title="이 초대는 사용할 수 없어요" description="취소되었거나 유효기간이 지난 초대예요. 매장 점주에게 새로운 초대를 요청해 주세요." /><Button onClick={() => go('/')}>홈으로</Button></div>
  if (invite.status === 'accepted') return <div className="page-content store-form-content"><Heading title="이미 수락한 초대예요" description={`${data.store.name}의 업무 자료를 확인해 보세요.`} /><Button onClick={() => { if (correctAccount) update(current => ({ ...current, role: 'member' })); go('/manuals') }}>매장 매뉴얼 보기</Button></div>
  return <div className="store-flow-page"><div className="page-content store-form-content"><Heading title="매장에서 초대가 도착했어요" description="초대 정보를 확인하고 참여 여부를 선택해 주세요." /><Card className="store-invitation-card"><h2>{data.store.name}</h2><p className="muted">신규 근무자로 초대되었어요.</p><div className="store-detail-list"><DetailRow label="초대 대상">{invite.email}</DetailRow><DetailRow label="담당 업무">{invite.task}</DetailRow><DetailRow label="접근 기간">별도 종료 전까지</DetailRow><DetailRow label="제공 범위">업무 매뉴얼 · 체크리스트</DetailRow><DetailRow label="시작 시점">수락 후 즉시</DetailRow></div></Card><p className="small muted store-invitation-note">수락하면 매장 업무 자료를 확인하고 온보딩을 시작할 수 있어요.</p>{!correctAccount && <p className="store-error" role="alert">초대받은 이메일 계정으로 로그인해 주세요. 현재 계정: {data.profile.email}</p>}</div><div className="store-bottom-action store-two-actions"><Button variant="secondary" onClick={() => setDecline(true)} disabled={!correctAccount}>거절</Button><Button onClick={accept} disabled={!correctAccount || data.store.status !== 'active'}>초대 수락</Button></div>{decline && <Modal title="초대를 거절할까요?" onClose={() => setDecline(false)}><p className="muted">매장에 참여하지 않고 초대를 종료해요.</p><div className="store-dialog-actions"><Button variant="secondary" onClick={() => setDecline(false)}>돌아가기</Button><Button onClick={() => { update(current => ({ ...current, invites: current.invites.map(item => item.id === id ? { ...item, status: 'cancelled' } : item) })); notify('초대를 거절했어요.'); go('/') }}>거절하기</Button></div></Modal>}</div>
}

function WorkerList() {
  const { data, go } = useApp()
  const active = data.workers.filter(workerActive)
  return <div className="page-content store-form-content"><Heading title="근무자를 선택해 주세요" description={`${data.store.name} · 재직 중 ${active.length}명`} /><div className="store-worker-list">{data.workers.length === 0 ? <Empty title="아직 등록된 근무자가 없어요" description="함께 일할 근무자를 초대해 보세요." /> : data.workers.map(worker => <button className="store-worker-card" key={worker.id} onClick={() => go(`/workers/${worker.id}`)}><span><strong>{worker.name}</strong><span className="muted">{worker.task}</span><b className={!workerActive(worker) ? 'muted' : ''}>{!workerActive(worker) ? '접근 종료' : worker.type === 'regular' ? '정기 근무' : `대타 근무${worker.endDate ? ` · ${shortDate(worker.endDate)}까지` : ''}`}</b></span><img src={assets.chevron} alt="" /></button>)}</div>{data.workers.length === 0 && <Button onClick={() => go('/invite')}>근무자 초대</Button>}</div>
}

function WorkerDetail({ id }: { id: string }) {
  const { data, update, notify, go } = useApp()
  const [confirm, setConfirm] = useState(false)
  const worker = data.workers.find(item => item.id === id)
  if (!worker) return <div className="page-content store-form-content"><Empty title="근무자를 찾을 수 없어요" description="근무자 목록에서 다시 선택해 주세요." /><Button onClick={() => go('/workers')}>근무자 목록</Button></div>
  const active = workerActive(worker)
  return <div className="page-content store-worker-detail"><Card className="store-worker-info"><div className="store-worker-profile"><span className="store-worker-avatar"><img src={assets.user} alt="" /></span><div><h2>{worker.name}</h2><p className="small muted">{worker.task} · {worker.type === 'regular' ? '정기 근무' : '대타 근무'}</p></div></div><Badge tone={active ? 'green' : 'gray'}>{active ? '재직 중' : '접근 종료'}</Badge><div className="store-detail-list"><DetailRow label="접근 유형">{worker.type === 'regular' ? '정기 근무' : '대타 근무'}</DetailRow><DetailRow label="접근 시작일">{dateText(worker.startDate)}</DetailRow><DetailRow label="접근 만료일">{dateText(worker.endDate)}</DetailRow></div></Card><Card className="store-permissions"><h3>매장 접근 권한</h3>{['업무 매뉴얼', '체크리스트', 'AI 질의응답'].map(label => <div className="store-permission" key={label}><span>{label}</span><strong className={active ? 'store-success-text' : 'muted'}>{active && <img src={assets.check} alt="" />}{active ? '접근 허용' : '접근 종료'}</strong></div>)}</Card><p className="small muted">접근을 종료하면 매장 업무 자료를<br />더 이상 열람할 수 없어요.</p><Button variant="danger" disabled={!active} onClick={() => setConfirm(true)}>{active ? '접근 종료 처리' : '접근이 종료되었어요'}</Button>{confirm && <Modal title="매장 접근을 종료할까요?" onClose={() => setConfirm(false)}><p className="muted">{worker.name}님은 이 근무의 업무 매뉴얼과 AI 질의응답을 더 이상 이용할 수 없어요.</p><div className="store-dialog-actions"><Button variant="secondary" onClick={() => setConfirm(false)}>취소</Button><Button variant="danger" onClick={() => { update(current => ({ ...current, workers: current.workers.map(item => item.id === worker.id ? { ...item, status: 'ended', endDate: today() } : item) })); setConfirm(false); notify('매장 접근을 종료했어요.') }}>접근 종료</Button></div></Modal>}</div>
}

function StoreRegistration() {
  const { data, update, go, notify } = useApp()
  const existing = data.store.status === 'pending' ? data.store : undefined
  const [name, setName] = useState(existing?.name || '')
  const [ownerName, setOwnerName] = useState(existing?.ownerName || '')
  const [category, setCategory] = useState(existing?.category || '')
  const [address, setAddress] = useState(existing?.address || '')
  const [detail, setDetail] = useState('')
  const [phone, setPhone] = useState(existing?.phone || '')
  const [businessNumber, setBusinessNumber] = useState(existing?.businessNumber || '')
  const [error, setError] = useState('')
  const [addressOpen, setAddressOpen] = useState(false)
  const [registrationConfirm, setRegistrationConfirm] = useState(false)
  const [query, setQuery] = useState('')
  const addresses = ['서울특별시 노원구 광운로 20', '서울특별시 노원구 석계로 11', '서울특별시 노원구 광운로 29']
  const submit = (event: FormEvent) => {
    event.preventDefault()
    if (!name.trim() || !ownerName.trim() || !category || !address || !phone || !businessNumber) return setError('필수 항목을 모두 입력해 주세요.')
    if (!/^\d{10}$/.test(businessNumber.replaceAll('-', ''))) return setError('사업자 번호 10자리를 확인해 주세요.')
    if (!/^0\d{8,10}$/.test(phone.replaceAll('-', ''))) return setError('올바른 연락처를 입력해 주세요.')
    if (!existing && name.trim() === data.store.name) return setError('이미 등록된 매장이에요. 매장 관리에서 확인해 주세요.')
    setError('')
    if (!existing) setRegistrationConfirm(true)
    else saveRegistration()
  }
  const saveRegistration = () => {
    update(current => ({ ...current, ...(!existing ? { workers: [], invites: [], manuals: [], jobs: [], savedJobs: [] } : {}), role: 'owner', store: { ...current.store, id: existing?.id || `store-${crypto.randomUUID()}`, name: name.trim(), ownerName: ownerName.trim(), category, address: `${address}${detail.trim() ? ` ${detail.trim()}` : ''}`, phone, businessNumber, status: 'pending' } }))
    setRegistrationConfirm(false)
    notify('매장 등록을 접수했어요. 확인 후 이용할 수 있어요.')
    go('/store')
  }
  return <form className="store-flow-page" noValidate onSubmit={submit}><div className="page-content store-registration"><Heading title="우리 매장을 등록해 주세요" description="매장 운영에 필요한 기본 정보를 입력해 주세요." /><h2 className="section-title">매장 및 점주 정보</h2><Field label="매장명 *" value={name} onChange={event => setName(event.target.value)} placeholder="매장명을 입력해 주세요" /><Field label="점주 성명 *" value={ownerName} onChange={event => setOwnerName(event.target.value)} placeholder="이름을 입력해 주세요" /><label className="store-select-field"><span>업종 *</span><span className="store-select-wrap"><select value={category} onChange={event => setCategory(event.target.value)}><option value="">업종을 선택해 주세요</option><option>음식점</option><option>카페</option><option>편의점</option><option>기타</option></select><img src={assets.select} alt="" /></span></label><h2 className="section-title">위치 및 연락 정보</h2><div className="store-address-fields"><Field label="매장 주소 *" value={address ? '01897' : ''} placeholder="우편번호" readOnly /><Button type="button" variant="secondary" onClick={() => setAddressOpen(true)}>주소 검색</Button><Field label="기본 주소" value={address} placeholder="주소 검색으로 입력해 주세요" readOnly /><Field label="상세주소" value={detail} onChange={event => setDetail(event.target.value)} placeholder="층·호수 등을 입력해 주세요" /></div><Field label="사업자 번호 *" inputMode="numeric" value={businessNumber} onChange={event => setBusinessNumber(event.target.value)} placeholder="000-00-00000" /><Field label="연락처 *" type="tel" value={phone} onChange={event => setPhone(event.target.value)} placeholder="010-1234-5678" /><p className="small muted store-registration-notice">현재 월계1동에 위치한 매장을 대상으로 운영하고 있어요.{!existing && <><br />새 매장을 등록하면 현재 기기에 저장된 매장과 업무 데이터가 교체돼요.</>}</p>{error && <p className="store-error" role="alert">{error}</p>}</div><div className="store-bottom-action"><Button type="submit">매장 등록</Button></div>{addressOpen && <Modal title="매장 주소 검색" onClose={() => setAddressOpen(false)}><Field label="도로명 주소" value={query} onChange={event => setQuery(event.target.value)} placeholder="도로명 또는 건물명을 입력해 주세요" /><p className="small muted">프로토타입에서 선택할 수 있는 월계1동 예시 주소예요.</p><div className="store-address-results">{addresses.filter(item => !query || item.includes(query)).map(item => <button type="button" key={item} onClick={() => { setAddress(item); setAddressOpen(false) }}><span className="small muted">월계1동</span><strong>{item}</strong></button>)}{addresses.filter(item => !query || item.includes(query)).length === 0 && <p className="muted">일치하는 예시 주소가 없어요. 광운로 또는 석계로로 검색해 주세요.</p>}</div></Modal>}{registrationConfirm && <Modal title="새 매장으로 전환할까요?" onClose={() => setRegistrationConfirm(false)}><p className="muted">현재 기기에 저장된 근무자·초대·매뉴얼·공고를 삭제하고 새 매장을 등록해요. 새 매장은 확인 대기 상태로 표시돼요.</p><div className="store-dialog-actions"><Button type="button" variant="secondary" onClick={() => setRegistrationConfirm(false)}>취소</Button><Button type="button" onClick={saveRegistration}>새 매장 등록</Button></div></Modal>}</form>
}

export function StorePages({ route }: { route: string }) {
  const { data, go } = useApp()
  if (route.startsWith('/invitation/')) return <InvitationPage key={route} id={route.slice('/invitation/'.length)} />
  if (data.role !== 'owner') return <div className="page-content store-form-content"><Empty title="점주만 이용할 수 있는 화면이에요" description="초대받은 매장의 업무 자료는 매뉴얼에서 확인해 주세요." /><Button onClick={() => go('/manuals')}>매뉴얼 보기</Button></div>
  if (route === '/store/new') return <StoreRegistration />
  if (route === '/invite') return <InvitePage />
  if (route === '/workers') return <WorkerList />
  if (route.startsWith('/workers/')) return <WorkerDetail key={route} id={route.slice('/workers/'.length)} />
  return <StoreHome />
}

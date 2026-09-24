import { useState, type FormEvent } from 'react'
import { hasMemberAccess, useApp, STORAGE_KEY, type AppData, type Manual, type ManualAttachment } from '../state'
import { Badge, Button, Card, Empty, Field, Heading, Icon } from '../ui'
import './ManualPages.css'

const categories = ['홀 서빙', '주방 보조', '카운터', '상품 입고·진열', '매장 마감']
const voiceExample = '출근하면 먼저 매장 조명을 켜고 손을 깨끗이 씻어요. 작업대와 조리 도구의 청결 상태를 확인해요. 냉장고에 있는 재료의 상태와 소비기한을 확인해요. 부족한 재료는 점주에게 알려주세요.'

function publishedCopy(manual: Manual): Manual | null {
  if (manual.status === 'published') return manual
  if (manual.publishedSteps?.length) return {
    ...manual,
    title: manual.publishedTitle || manual.title,
    steps: manual.publishedSteps,
    attachments: manual.publishedAttachments || [],
    status: 'published',
  }
  return null
}

function shortDate(value: string) {
  const date = new Date(value)
  return Number.isNaN(date.getTime()) ? value : date.toLocaleDateString('ko-KR', { year: 'numeric', month: '2-digit', day: '2-digit' })
}

function saveMediaData(data: AppData, showError: (message: string) => void) {
  const size = data.manuals.reduce((total, manual) => total + [...manual.attachments || [], ...manual.publishedAttachments || []].reduce((count, attachment) => count + attachment.dataUrl.length, 0), 0)
  if (!size) return true
  if (size >= 3 * 1024 * 1024) { showError('게시본을 포함한 전체 사진이 저장 한도 3MB를 넘어요. 더 작은 사진으로 바꿔 주세요.'); return false }
  try { localStorage.setItem(STORAGE_KEY, JSON.stringify(data)); return true }
  catch { showError('브라우저 저장 공간이 부족하거나 저장이 차단되어 있어요. 사진을 줄인 뒤 다시 저장해 주세요.'); return false }
}

function AccessNotice({ editing = false }: { editing?: boolean }) {
  const { go } = useApp()
  return <div className="page-content manual-page stack">
    <Heading title={editing ? '점주만 수정할 수 있어요' : '매장 연결이 필요해요'} description={editing ? '매뉴얼 작성과 게시는 해당 매장 점주에게 제공됩니다.' : '초대를 수락하거나 대타 근무가 확정되면 담당 매장의 매뉴얼을 확인할 수 있어요.'} />
    <Empty title={editing ? '열람 권한과 작성 권한은 달라요' : '접근 가능한 매장이 없어요'} description={editing ? '담당 업무에 필요한 승인 매뉴얼을 확인해 보세요.' : '근무가 종료되거나 접근 기간이 만료된 자료는 제공되지 않아요.'} />
    <Button onClick={() => go(editing ? '/manuals' : '/home')}>{editing ? '매뉴얼 목록으로' : '홈으로 돌아가기'}</Button>
  </div>
}

export function ManualPages({ route }: { route: string }) {
  const { data } = useApp()
  const path = route.split('?')[0]
  const owner = data.role === 'owner'
  if (!owner && (data.role !== 'member' || !hasMemberAccess(data))) return <AccessNotice />
  if (path === '/chat') return <ManualChat />
  if (path === '/manuals/new') return owner ? <ManualCreate /> : <AccessNotice editing />
  const parts = path.split('/').filter(Boolean)
  if (parts.length > 1) {
    const manual = data.manuals.find(item => item.id === parts[1])
    if (!manual || (!owner && (!publishedCopy(manual) || !hasMemberAccess(data, manual.category)))) return <MissingManual />
    if (parts[2] === 'edit') return owner ? <ManualEdit key={manual.id} manual={manual} /> : <AccessNotice editing />
    if (parts[2] === 'review') return owner ? <ManualReview key={manual.id} manual={manual} /> : <AccessNotice editing />
    return <ManualDetail key={`${manual.id}-${manual.status}`} manual={manual} />
  }
  return <ManualList />
}

function MissingManual() {
  const { go } = useApp()
  return <div className="page-content manual-page stack"><Heading title="매뉴얼을 찾을 수 없어요" description="게시 여부 또는 접근 권한이 변경되었을 수 있어요." /><Button onClick={() => go('/manuals')}>매뉴얼 목록으로</Button></div>
}

function ManualList() {
  const { data, go } = useApp()
  const [query, setQuery] = useState('')
  const [category, setCategory] = useState('전체')
  const [status, setStatus] = useState('전체')
  const owner = data.role === 'owner'
  const available = owner ? data.manuals : data.manuals.filter(item => hasMemberAccess(data, item.category)).map(publishedCopy).filter((item): item is Manual => item !== null)
  const visible = available.filter(item =>
    (category === '전체' || item.category === category) &&
    (status === '전체' || item.status === (status === '게시됨' ? 'published' : 'draft')) &&
    `${item.title} ${item.category}`.toLocaleLowerCase().includes(query.trim().toLocaleLowerCase()),
  )
  return <div className="page-content manual-page stack">
    <Heading title={owner ? '우리 매장 매뉴얼' : '오늘의 업무 매뉴얼'} description={owner ? '매장의 노하우를 기록하고 공유하세요.' : '점주가 승인한 업무 절차를 확인하세요.'} />
    <Card className="manual-summary">
      <div className="manual-summary-store"><span className="manual-eyebrow">{data.store?.name || '담당 매장'}</span><strong>함께 일하는 기준, 매뉴얼</strong></div>
      <div className="manual-metrics"><div><span>게시된 매뉴얼</span><strong>{available.filter(item => publishedCopy(item)).length}<small>개</small></strong></div>{owner && <div><span>작성 중인 초안</span><strong className="manual-primary">{data.manuals.filter(item => item.status === 'draft').length}<small>개</small></strong></div>}</div>
      <Button onClick={() => go(owner ? '/manuals/new' : '/chat')}>{owner ? '새 매뉴얼 만들기' : '업무에 대해 질문하기'}</Button>
    </Card>
    <div className="manual-search"><Field label="매뉴얼 검색" type="search" placeholder="업무 또는 매뉴얼 제목을 검색하세요" value={query} onChange={event => setQuery(event.target.value)} /></div>
    {owner && <div className="manual-status-tabs" aria-label="게시 상태">{['전체', '게시됨', '초안'].map(value => <button type="button" aria-pressed={status === value} className={status === value ? 'is-active' : ''} key={value} onClick={() => setStatus(value)}>{value}</button>)}</div>}
    <div className="manual-filters" aria-label="업무 분류">{['전체', ...new Set(available.map(item => item.category))].map(value => <button type="button" key={value} aria-pressed={category === value} className={category === value ? 'is-active' : ''} onClick={() => setCategory(value)}>{value}</button>)}</div>
    <section className="manual-list-section" aria-label="매뉴얼 목록"><div className="row between"><h2 className="section-title">{category === '전체' ? '업무 목록' : `${category} 매뉴얼`}</h2><span className="small muted">{visible.length}개</span></div>
      {visible.length ? <div className="manual-card-list">{visible.map(manual => <button type="button" className="manual-list-card" key={manual.id} onClick={() => go(`/manuals/${manual.id}`)}>
        <div className="row between"><Badge tone={manual.status === 'published' ? 'blue' : 'yellow'}>{manual.status === 'published' ? '게시됨' : '작성 중'}</Badge><span className="manual-category">{manual.category}</span></div>
        <div className="manual-card-title"><h3>{manual.title}</h3><Icon name="chevron" /></div>
        <p className="small muted">{manual.steps.length}개 단계{owner && manual.status === 'draft' && manual.publishedSteps ? ' · 기존 게시본 유지 중' : ''}</p>
        {owner && <p className="manual-card-date">최근 수정 {shortDate(manual.updatedAt)}</p>}
      </button>)}</div> : <Empty title={query || category !== '전체' || status !== '전체' ? '검색 결과가 없어요' : '아직 게시된 매뉴얼이 없어요'} description={query ? '다른 업무 이름으로 검색해 보세요.' : owner ? '첫 매뉴얼을 작성해 매장의 노하우를 공유해 보세요.' : '점주가 매뉴얼을 게시하면 이곳에서 확인할 수 있어요.'} />}
    </section>
    {owner && <button type="button" className="manual-text-link" onClick={() => go('/chat')}>승인 매뉴얼로 질문해 보기 <span aria-hidden="true">→</span></button>}
  </div>
}

function ManualCreate() {
  const { update, go, notify } = useApp()
  const [title, setTitle] = useState('')
  const [category, setCategory] = useState(categories[0])
  const [source, setSource] = useState('')
  const [mode, setMode] = useState<'text' | 'voice'>('text')
  const [phase, setPhase] = useState<'input' | 'supplement'>('input')
  const [supplement, setSupplement] = useState('')
  const [error, setError] = useState('')
  function next(event: FormEvent) {
    event.preventDefault()
    if (title.trim().length < 2 || source.trim().length < 20) { setError('제목을 2자 이상, 업무 설명을 20자 이상 입력해 주세요.'); return }
    setError('')
    setPhase('supplement')
  }
  function create(includeSupplement: boolean) {
    const id = `manual-${crypto.randomUUID()}`
    const steps = source.split(/\n+|(?<=[.!?。])\s+/).map(value => value.trim()).filter(Boolean)
    if (includeSupplement && supplement.trim()) steps.push(supplement.trim())
    update(draft => ({ ...draft, manuals: [{ id, title: title.trim(), category, description: '매장의 업무 절차와 확인 사항을 정리한 매뉴얼입니다.', sourceText: source.trim(), steps, status: 'draft', updatedAt: new Date().toISOString() }, ...draft.manuals] }))
    notify('초안을 저장했어요. 내용을 확인하고 수정해 주세요.')
    go(`/manuals/${id}/edit`)
  }
  return <div className="page-content manual-page stack">
    <div className="manual-process" aria-label="매뉴얼 작성 단계"><span className={phase === 'input' ? 'current' : 'done'}>1. 업무 설명</span><i /><span className={phase === 'supplement' ? 'current' : ''}>2. 보완하기</span><i /><span>3. 검토·게시</span></div>
    <Heading title={phase === 'input' ? '업무 노하우를 들려주세요' : '한 가지만 더 확인할게요'} description={phase === 'input' ? '평소 하던 순서대로 편하게 적어 주세요.' : '새로운 근무자가 놓치기 쉬운 내용을 보완해요.'} />
    {phase === 'input' ? <form className="stack" onSubmit={next}>
      <Card className="manual-form-card"><Field label="매뉴얼 제목" placeholder="예: 오픈 전 준비하기" maxLength={60} value={title} onChange={event => setTitle(event.target.value)} required />
        <label className="manual-field"><span>담당 업무</span><select value={category} onChange={event => setCategory(event.target.value)}>{categories.map(value => <option key={value}>{value}</option>)}</select></label>
      </Card>
      <Card className="manual-form-card"><div className="manual-input-tabs"><button type="button" className={mode === 'text' ? 'is-active' : ''} aria-pressed={mode === 'text'} onClick={() => setMode('text')}>텍스트로 설명</button><button type="button" className={mode === 'voice' ? 'is-active' : ''} aria-pressed={mode === 'voice'} onClick={() => setMode('voice')}>음성 입력 예시</button></div>
        {mode === 'voice' && <div className="manual-demo-note"><strong>음성 입력 체험</strong><p>실제 녹음 없이 미리 준비된 음성 전사 예시를 불러와요.</p><Button type="button" variant="secondary" onClick={() => { setSource(voiceExample); if (!title) setTitle('오픈 전 준비하기'); setCategory('홀 서빙') }}>전사 예시 불러오기</Button></div>}
        <label className="manual-field"><span>업무 설명</span><textarea rows={8} value={source} maxLength={6000} onChange={event => setSource(event.target.value)} placeholder={'어떤 순서로 일하나요?\n사용하는 도구나 꼭 확인해야 할 사항도 함께 알려주세요.'} required /></label><p className="manual-input-help">{source.length.toLocaleString()} / 6,000자</p>
      </Card>
      <p className="manual-demo-caption">초안 정리는 AI 기능의 데모예요. 입력한 문장을 단계로 나누며, 실제 AI 서비스에는 전송하지 않아요.</p>
      {error && <p className="manual-error" role="alert">{error}</p>}
      <Button type="submit">초안 만들기</Button>
    </form> : <>
      <Card className="manual-form-card"><Badge tone="blue">보완 질문 1 / 1</Badge><h2 className="section-title">업무를 마친 뒤 꼭 확인할 사항이 있나요?</h2><p className="muted">정리 방법, 설비 확인 또는 문제가 생겼을 때 누구에게 알려야 하는지 적어 주세요.</p><label className="manual-field"><span>추가 설명</span><textarea rows={5} value={supplement} onChange={event => setSupplement(event.target.value)} maxLength={1000} placeholder="예: 이상이 있으면 작업을 멈추고 점주에게 알려주세요." /></label></Card>
      <Button disabled={!supplement.trim()} onClick={() => create(true)}>보완 내용 반영하고 초안 보기</Button><Button variant="secondary" onClick={() => create(false)}>현재 내용으로 초안 보기</Button><button type="button" className="manual-text-link" onClick={() => setPhase('input')}>업무 설명 다시 수정하기</button>
    </>}
  </div>
}

function ManualEdit({ manual }: { manual: Manual }) {
  const { data, update, go, notify } = useApp()
  const [title, setTitle] = useState(manual.title)
  const [steps, setSteps] = useState(manual.steps)
  const [attachments, setAttachments] = useState<ManualAttachment[]>(manual.attachments || [])
  const [readingPhoto, setReadingPhoto] = useState(false)
  const [error, setError] = useState('')
  function addPhoto(file: File | undefined, step: number) {
    if (!file) return
    if (!['image/jpeg', 'image/png', 'image/webp', 'image/gif'].includes(file.type)) { setError('JPG, PNG, WebP 또는 GIF 사진을 선택해 주세요. 영상 첨부는 지원하지 않아요.'); return }
    if (file.size > 2 * 1024 * 1024) { setError('사진 한 장은 2MB 이하로 선택해 주세요.'); return }
    setError('')
    setReadingPhoto(true)
    const reader = new FileReader()
    reader.onload = () => {
      if (typeof reader.result === 'string') {
        const photo = { step, name: file.name, dataUrl: reader.result }
        const next = [...attachments.filter(item => item.step !== step), photo]
        const allPhotos = data.manuals.flatMap(item => item.id === manual.id ? [...next, ...(item.status === 'published' ? item.attachments || [] : item.publishedAttachments || [])] : [...item.attachments || [], ...item.publishedAttachments || []])
        if (allPhotos.reduce((total, item) => total + item.dataUrl.length, 0) >= 3 * 1024 * 1024) setError('이 기기의 전체 매뉴얼 사진 저장 한도는 3MB예요. 더 작은 사진을 선택하거나 초안의 사진을 줄여 주세요.')
        else setAttachments(next)
      }
      setReadingPhoto(false)
    }
    reader.onerror = () => { setError('사진을 읽지 못했어요. 파일을 다시 선택해 주세요.'); setReadingPhoto(false) }
    reader.readAsDataURL(file)
  }
  function removeStep(index: number) {
    setSteps(values => values.filter((_, number) => number !== index))
    setAttachments(values => values.filter(item => item.step !== index).map(item => item.step > index ? { ...item, step: item.step - 1 } : item))
  }
  function save(review: boolean) {
    if (title.trim().length < 2 || !steps.length || steps.some(step => !step.trim())) { setError('제목과 모든 단계의 내용을 입력해 주세요.'); return }
    const now = new Date().toISOString()
    const next: AppData = { ...data, manuals: data.manuals.map(item => item.id === manual.id ? { ...item, title: title.trim(), steps: steps.map(step => step.trim()), attachments, status: 'draft', updatedAt: now, publishedSteps: item.status === 'published' ? [...item.steps] : item.publishedSteps, publishedTitle: item.status === 'published' ? item.title : item.publishedTitle, publishedAttachments: item.status === 'published' ? item.attachments || [] : item.publishedAttachments } : item) }
    if (!saveMediaData(next, setError)) return
    update(() => next)
    notify(review ? '저장한 내용을 검토해 주세요.' : '초안을 저장했어요.')
    go(`/manuals/${manual.id}${review ? '/review' : ''}`)
  }
  return <div className="page-content manual-page stack">
    <Heading title="매뉴얼 초안 편집" description="새로운 근무자가 이해하기 쉽게 다듬어 주세요." />
    <div className="manual-notice"><Badge tone="yellow">아직 게시되지 않은 수정 내용</Badge><p>{publishedCopy(manual) ? '수정한 내용을 승인하기 전까지 기존 게시본이 근무자에게 제공돼요.' : '점주의 검토와 승인이 끝나면 근무자에게 제공돼요.'}</p></div>
    <Card className="manual-form-card"><Field label="매뉴얼 제목" value={title} maxLength={60} onChange={event => setTitle(event.target.value)} required /><div className="row between"><span className="muted">담당 업무</span><Badge tone="gray">{manual.category}</Badge></div></Card>
    <section className="manual-step-editor"><div className="row between"><h2 className="section-title">업무 절차</h2><span className="small muted">{steps.length}개 단계</span></div>{steps.map((step, index) => {
      const photo = attachments.find(item => item.step === index)
      return <Card key={index} className="manual-form-card"><div className="row between"><strong className="manual-step-label">{index + 1}단계</strong><button type="button" disabled={steps.length === 1 || readingPhoto} className="manual-delete-step" aria-label={`${index + 1}단계 삭제`} onClick={() => removeStep(index)}>삭제</button></div><label className="manual-field"><span className="manual-sr-only">{index + 1}단계 내용</span><textarea rows={3} value={step} maxLength={2000} onChange={event => setSteps(values => values.map((value, number) => number === index ? event.target.value : value))} /></label>{photo && <figure className="manual-photo"><img src={photo.dataUrl} alt={`${index + 1}단계 참고 사진: ${photo.name}`} /><figcaption>{photo.name}<button type="button" className="manual-delete-step" disabled={readingPhoto} aria-label={`${index + 1}단계 사진 삭제`} onClick={() => setAttachments(values => values.filter(item => item.step !== index))}>사진 삭제</button></figcaption></figure>}<label className="manual-photo-input"><span>{photo ? '사진 바꾸기' : '참고 사진 추가'} <small>선택</small></span><input aria-label={`${index + 1}단계 참고 사진`} type="file" accept="image/jpeg,image/png,image/webp,image/gif" disabled={readingPhoto} onChange={event => { addPhoto(event.target.files?.[0], index); event.target.value = '' }} /></label></Card>
    })}<Button variant="secondary" disabled={readingPhoto} onClick={() => setSteps(values => [...values, ''])}>단계 추가하기</Button><p className="manual-demo-caption">단계마다 사진 1장, 파일당 2MB까지 첨부할 수 있어요. 사진은 이 브라우저에만 저장되며 서버에 업로드되지 않아요. 전체 사진 저장 한도는 게시본을 포함해 3MB예요.</p></section>
    {manual.sourceText && <details className="manual-original"><summary>처음 입력한 업무 설명 보기</summary><p>{manual.sourceText}</p></details>}
    {error && <p className="manual-error" role="alert">{error}</p>}
    <div className="manual-actions"><Button variant="secondary" disabled={readingPhoto} onClick={() => save(false)}>초안 저장</Button><Button disabled={readingPhoto} onClick={() => save(true)}>{readingPhoto ? '사진 읽는 중…' : '검토하기'}</Button></div>
    <button type="button" className="manual-text-link" onClick={() => go(`/manuals/${manual.id}`)}>변경하지 않고 돌아가기</button>
  </div>
}

function ManualReview({ manual }: { manual: Manual }) {
  const { data, update, go, notify } = useApp()
  const [confirmed, setConfirmed] = useState(false)
  const [error, setError] = useState('')
  function publish() {
    if (!confirmed || data.role !== 'owner' || !manual.steps.length || manual.steps.some(step => !step.trim())) return
    const next: AppData = { ...data, manuals: data.manuals.map(item => item.id === manual.id ? { ...item, status: 'published', publishedSteps: [...item.steps], publishedTitle: item.title, publishedAttachments: item.attachments || [], updatedAt: new Date().toISOString() } : item) }
    if (!saveMediaData(next, setError)) return
    update(() => next)
    notify('매뉴얼을 게시했어요. 근무자가 확인할 수 있어요.')
    go(`/manuals/${manual.id}`)
  }
  return <div className="page-content manual-page stack"><Heading title="게시 전 마지막 확인" description="아래 내용 그대로 근무자에게 제공돼요." /><Card className="manual-detail-heading"><Badge tone="gray">{manual.category}</Badge><h2>{manual.title}</h2><p className="small muted">업무 절차 {manual.steps.length}단계</p></Card><Steps steps={manual.steps} attachments={manual.attachments} /><div className="manual-notice"><strong>게시하면 무엇이 달라지나요?</strong><p>매장 접근 권한이 있는 근무자가 이 내용을 열람하고, 업무 질문의 근거로 사용할 수 있어요.</p></div><label className="manual-confirm"><input type="checkbox" checked={confirmed} onChange={event => setConfirmed(event.target.checked)} /><span>각 단계가 우리 매장의 실제 업무와 일치하는지 확인했어요.</span></label>{error && <p className="manual-error" role="alert">{error}</p>}<Button disabled={!confirmed || !manual.steps.length || manual.steps.some(step => !step.trim())} onClick={publish}>승인하고 게시하기</Button><Button variant="secondary" onClick={() => go(`/manuals/${manual.id}/edit`)}>돌아가서 수정하기</Button></div>
}

function Steps({ steps, attachments = [], checked, onCheck }: { steps: string[]; attachments?: ManualAttachment[]; checked?: number[]; onCheck?: (index: number) => void }) {
  return <ol className="manual-steps">{steps.map((step, index) => {
    const photo = attachments.find(item => item.step === index)
    return <li key={index}><Card className={`manual-step-card ${checked?.includes(index) ? 'is-checked' : ''}`}><div className="row between"><span className="manual-step-label">{index + 1}단계</span>{onCheck && <label className="manual-step-check"><input type="checkbox" aria-label={`${index + 1}단계 확인`} checked={checked?.includes(index) || false} onChange={() => onCheck(index)} /><span>확인</span></label>}</div><p>{step}</p>{photo && <figure className="manual-photo"><img src={photo.dataUrl} alt={`${index + 1}단계 참고 사진: ${photo.name}`} /><figcaption>{photo.name}</figcaption></figure>}</Card></li>
  })}</ol>
}

function ManualDetail({ manual }: { manual: Manual }) {
  const { data, go } = useApp()
  const [checked, setChecked] = useState<number[]>([])
  const [showPublished, setShowPublished] = useState(false)
  const owner = data.role === 'owner'
  const published = publishedCopy(manual)
  const current = (!owner || showPublished) && published ? published : manual
  const draft = current.status === 'draft'
  return <div className="page-content manual-page stack">
    <div className="row between"><Badge tone={draft ? 'yellow' : 'blue'}>{draft ? '작성 중인 초안' : '승인된 매뉴얼'}</Badge><span className="small muted">{current.category}</span></div>
    <Heading title={current.title} description={data.store?.name || '우리 매장'} />
    {owner && manual.status === 'draft' && <div className="manual-notice"><strong>{showPublished ? '현재 근무자가 보는 게시본이에요.' : '초안은 근무자에게 보이지 않아요.'}</strong><p>{published ? '수정 내용을 승인하기 전까지 기존 게시본이 유지돼요.' : '내용을 검토하고 승인하면 매뉴얼이 게시돼요.'}</p>{published && <button type="button" className="manual-text-link" onClick={() => { setShowPublished(value => !value); setChecked([]) }}>{showPublished ? '수정 중인 초안 보기' : '기존 게시본 보기'} <span aria-hidden="true">→</span></button>}</div>}
    <div className="row between"><h2 className="section-title">업무 절차</h2><span className="small muted">{current.steps.length}개 단계</span></div>
    <Steps steps={current.steps} attachments={current.attachments} checked={checked} onCheck={draft ? undefined : index => setChecked(values => values.includes(index) ? values.filter(value => value !== index) : [...values, index])} />
    {!draft && <p className="manual-demo-caption">체크는 이 화면에서만 유지돼요. 학습 이력이나 숙련도로 기록되지 않아요.</p>}
    {owner ? <div className="manual-actions"><Button variant="secondary" onClick={() => go(`/manuals/${manual.id}/edit`)}>{manual.status === 'published' ? '수정 초안 만들기' : '초안 편집하기'}</Button>{manual.status === 'draft' && <Button onClick={() => go(`/manuals/${manual.id}/review`)}>검토하고 게시</Button>}</div> : <Button onClick={() => go('/chat')}>이 업무에 대해 질문하기</Button>}
    <button type="button" className="manual-text-link" onClick={() => go('/manuals')}>전체 매뉴얼 보기</button>
  </div>
}

type ChatEntry = { id: string; question: string; kind: 'found' | 'unknown' | 'clarify'; manualId?: string; version?: string }
const snapshot = (manual: Manual) => JSON.stringify([manual.title, manual.steps])
const stopWords = new Set(['어떻게', '하나요', '하나요요', '알려주세요', '알려줘', '궁금해요', '무엇인가요', '무슨', '어떤', '매뉴얼', '매장', '업무', '절차', '순서', '방법', '주세요', '대해', '있나요', '하나', '하는', '알려', '해야'])
function keywords(text: string) {
  return text.replace(/[^가-힣a-zA-Z0-9 ]/g, ' ').split(/\s+/).map(word => word.replace(/(에서는|으로|에서|하는|하기|하기는|인가요|하나요|나요|은|는|을|를|이|가|도|의|에|요)$/g, '')).filter(word => word.length > 1 && !stopWords.has(word))
}

function ManualChat() {
  const { data, go } = useApp()
  const [question, setQuestion] = useState('')
  const [entries, setEntries] = useState<ChatEntry[]>([])
  const manuals = data.manuals.filter(manual => data.role === 'owner' || hasMemberAccess(data, manual.category)).map(publishedCopy).filter((manual): manual is Manual => manual !== null)
  function ask(value: string) {
    const clean = value.trim()
    if (!clean || (data.role !== 'owner' && !hasMemberAccess(data))) return
    const tokens = keywords(clean)
    const matches = manuals.filter(manual => data.role === 'owner' || hasMemberAccess(data, manual.category)).map(manual => ({ manual, score: tokens.reduce((score, token) => score + (manual.title.includes(token) ? 3 : manual.steps.some(step => step.includes(token)) ? 1 : 0), 0) })).filter(item => item.score > 0).sort((a, b) => b.score - a.score)
    const match = matches[0]
    const ambiguous = matches.length > 1 && match.score === matches[1].score
    setEntries(values => [...values, { id: crypto.randomUUID(), question: clean, kind: match && !ambiguous ? 'found' : !tokens.length || ambiguous ? 'clarify' : 'unknown', manualId: match && !ambiguous ? match.manual.id : undefined, version: match && !ambiguous ? snapshot(match.manual) : undefined }])
    setQuestion('')
  }
  return <div className="page-content manual-page manual-chat-page stack"><Heading title="업무 질문하기" description="우리 매장의 승인된 매뉴얼에서 찾아볼게요." />
    <div className="manual-chat-context"><span className="manual-presence-dot" /><strong>{data.store?.name || '담당 매장'}</strong><span className="small muted">게시 매뉴얼 {manuals.length}개</span></div>
    <Card className="manual-chat-greeting"><Badge tone="blue">지단 도우미</Badge><p>안녕하세요! 어떤 업무가 궁금하세요?<br />게시된 매뉴얼의 절차와 확인 사항을 찾아드려요.</p><div className="manual-suggestions">{manuals.slice(0, 3).map(manual => <button type="button" key={manual.id} onClick={() => ask(`${manual.title} 절차를 알려주세요`)}>{manual.title} <span aria-hidden="true">↗</span></button>)}</div></Card>
    {!manuals.length && <Empty title="아직 참고할 매뉴얼이 없어요" description="점주가 매뉴얼을 게시하면 업무 내용을 찾아볼 수 있어요." />}
    <div className="manual-chat-log" aria-live="polite" aria-relevant="additions text">{entries.map(entry => {
      const manual = manuals.find(item => item.id === entry.manualId)
      const changed = entry.kind === 'found' && (!manual || snapshot(manual) !== entry.version)
      return <div className="manual-exchange" key={entry.id}><div className="manual-question"><span className="manual-sr-only">나: </span>{entry.question}</div><Card className="manual-answer"><Badge tone="blue">지단 도우미</Badge>{changed ? <p>참고했던 매뉴얼이 변경되었어요. 최신 게시본으로 확인하려면 다시 질문해 주세요.</p> : entry.kind === 'found' && manual ? <><p>관련된 승인 매뉴얼에서 다음 내용을 찾았어요.</p><ol>{manual.steps.map((step, index) => <li key={index}>{step}</li>)}</ol><button type="button" className="manual-source" aria-label={`근거 매뉴얼: ${manual.title}`} onClick={() => go(`/manuals/${manual.id}`)}><span>근거 매뉴얼</span><strong>{manual.title} <span aria-hidden="true">↗</span></strong></button><p className="small muted">찾으시는 내용이 없다면 점주에게 확인해 주세요.</p></> : entry.kind === 'clarify' ? <><p>어떤 업무인지 조금 더 알려주세요. 아래에서 매뉴얼을 선택하거나 업무 이름을 함께 적어 주세요.</p><div className="manual-suggestions">{manuals.map(item => <button type="button" key={item.id} onClick={() => ask(`${item.title} 절차를 알려주세요`)}>{item.title}</button>)}</div></> : <><strong>확인할 수 있는 근거가 없어요.</strong><p>현재 게시된 매뉴얼에서 질문과 관련된 내용을 찾지 못했어요. 작업 방법을 추측하지 말고 점주 또는 담당자에게 직접 확인해 주세요.</p><button type="button" className="manual-text-link" onClick={() => go('/manuals')}>게시 매뉴얼 확인하기 <span aria-hidden="true">→</span></button></>}</Card></div>
    })}</div>
    <form className="manual-chat-composer" onSubmit={event => { event.preventDefault(); ask(question) }}><label className="manual-field"><span>궁금한 업무를 질문해 보세요</span><textarea rows={3} placeholder="예: 오픈 준비는 어떤 순서로 하나요?" value={question} maxLength={500} onChange={event => setQuestion(event.target.value)} /></label><Button type="submit" disabled={!question.trim() || !manuals.length}>질문 보내기</Button></form>
    <p className="manual-demo-caption">게시 매뉴얼 검색 데모예요. 실제 AI 응답은 생성하지 않으며, 대화는 이 화면에서만 유지돼요.</p>
  </div>
}

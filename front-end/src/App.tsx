import { useEffect, useRef, useState } from 'react'
import { AppProvider, useApp } from './state'
import { Badge, Button, Card, Empty, Heading, Icon, Modal } from './ui'
import { JobCard, JobPages } from './pages/JobPages'
import { jobStart } from './job-logic'
import { StorePages } from './pages/StorePages'
import { MemberPages } from './pages/MemberPages'
import { ManualPages } from './pages/ManualPages'
import './App.css'

function useRoute() {
  const read = () => window.location.hash.slice(1).split('?')[0] || '/'
  const [route, setRoute] = useState(read)
  useEffect(() => { const listener = () => setRoute(read()); window.addEventListener('hashchange', listener); return () => window.removeEventListener('hashchange', listener) }, [])
  return route
}
function Login() {
  const { go } = useApp()
  return <div className="login-page" data-figma-node="192:5290"><div className="brand-lockup"><span className="brand-symbol"><Icon name="brand" /></span><strong>지단</strong></div><div className="login-intro"><h1>매장의 시작을,<br />더 단단하게.</h1><p>첫 출근부터 매장 운영까지<br />함께하는 AI 온보딩</p></div><div className="login-preview"><span className="onboarding-label">AI 온보딩</span><h2>오늘도 준비된 우리 매장</h2><div className="preview-row"><Icon name="check" /><span>업무 매뉴얼, 한곳에서 확인</span></div><div className="preview-row"><Icon name="check" /><span>궁금한 업무는 AI에게 질문</span></div></div><div className="login-actions"><Button onClick={() => go('/roles')}>SSO 계정으로 시작하기</Button><p className="small muted">기존 계정으로 로그인하거나 새로 가입할 수 있어요.</p></div><p className="login-footer">매장 AI 온보딩 서비스 · 지단</p><p className="demo-mobile-note">프로토타입 · 샘플 계정으로 체험해요</p></div>
}
function Roles() {
  const { go, update, notify } = useApp()
  const choose = (role: 'owner' | 'member') => { update(previous => ({ ...previous, role })); go(role === 'owner' ? '/home' : '/signup/basic'); notify('샘플 계정으로 시작했어요. 실제 SSO 로그인은 연결 전이에요.') }
  return <div className="page-content role-page" data-figma-node="192:5299"><Heading title={'어떤 역할로\n지단과 함께하시나요?'} description="나에게 맞는 유형으로 시작해 보세요." /><div className="role-cards"><Card className="role-card"><div className="row"><span className="role-icon"><Icon name="store" /></span><h2>점주로 가입</h2></div><p className="muted">매장을 등록하고 근무자를 관리하며<br />대타 공고를 게시할 수 있어요.</p><Button onClick={() => choose('owner')}>점주로 가입</Button></Card><Card className="role-card"><div className="row"><span className="role-icon member"><Icon name="user" /></span><h2>일반회원으로 가입</h2></div><p className="muted">대타 공고를 탐색하고 신청하며<br />업무 매뉴얼을 확인할 수 있어요.</p><Button variant="secondary" onClick={() => choose('member')}>일반회원으로 가입</Button></Card></div><p className="small muted role-note">이용할 서비스에 맞는 가입 유형을 선택해 주세요.</p></div>
}
function Calendar() {
  const { data, go } = useApp(); const today = new Date(); const [month, setMonth] = useState(new Date(today.getFullYear(), today.getMonth(), 1)); const [selected, setSelected] = useState<number | null>(today.getDate())
  const year = month.getFullYear(); const monthIndex = month.getMonth(); const dayCount = new Date(year, monthIndex + 1, 0).getDate(); const first = month.getDay()
  const events = data.jobs.filter(job => Number(job.date.slice(0, 4)) === year && Number(job.date.slice(5, 7)) === monthIndex + 1 && (data.role === 'owner' || job.applicants.some(app => app.email === data.profile.email && app.status === 'confirmed')))
  const visibleEvents = selected && events.some(event => Number(event.date.slice(8)) === selected) ? events.filter(event => Number(event.date.slice(8)) === selected) : events
  const change = (amount: number) => { setMonth(new Date(year, monthIndex + amount, 1)); setSelected(null) }
  return <div className="calendar-section"><h2>{data.role === 'owner' ? '매장 캘린더' : '근무 캘린더'}</h2><Card className="calendar"><div className="row between"><h3>{year}년 {monthIndex + 1}월</h3><div className="calendar-arrows"><button className="icon-button" aria-label="이전 달" onClick={() => change(-1)}><Icon name="calendar-back" /></button><button className="icon-button" aria-label="다음 달" onClick={() => change(1)}><Icon name="chevron" /></button></div></div><div className="calendar-grid">{['일', '월', '화', '수', '목', '금', '토'].map(day => <span className="weekday" key={day}>{day}</span>)}{Array.from({ length: first }, (_, i) => <span key={`empty-${i}`} />)}{Array.from({ length: dayCount }, (_, i) => i + 1).map(day => <button key={day} aria-label={`${monthIndex + 1}월 ${day}일`} aria-pressed={selected === day} className={`calendar-day ${selected === day ? 'selected' : events.some(event => Number(event.date.slice(8)) === day) ? 'has-event' : ''}`} onClick={() => setSelected(day)}><span>{day}</span></button>)}</div></Card><Card className="upcoming-events">{visibleEvents.length ? visibleEvents.map(job => <button className="event-row" key={job.id} onClick={() => go(`/jobs/${job.id}`)}><strong>{monthIndex + 1}.{Number(job.date.slice(8))}</strong><span>{job.title}<small>{job.start}–{job.end}</small></span><Badge tone="yellow">대타 근무</Badge></button>) : <p className="small muted">이번 달 예정된 근무가 없어요.</p>}</Card></div>
}
function Home() {
  const { data, go, now } = useApp(); const owner = data.role === 'owner'; const openJobs = data.jobs.filter(job => job.status === 'open' && jobStart(job) > now); const activeJobs = data.jobs.filter(job => job.applicants.some(app => app.email === data.profile.email && app.status === 'pending')).length
  return <div className="page-content home-page" data-figma-node={owner ? '192:5168' : '223:5461'}><Heading title={`안녕하세요, ${owner ? `${data.store.ownerName} 점주님` : `${data.profile.name}님`}`} description={owner ? '오늘의 매장 운영 현황을 확인하세요.' : '원하는 공고를 찾아보세요.'} />{owner ? <div className="home-store-section"><Card className="managed-store"><p className="small">관리 매장</p><div className="row between"><h2>{data.store.name}</h2><Badge>{data.store.status === 'active' ? '운영 중' : '확인 대기'}</Badge></div><div className="row"><Button variant="secondary" onClick={() => go('/store')}>매장 관리</Button><Button variant="secondary" disabled={data.store.status !== 'active'} onClick={() => go('/jobs/new')}>공고 등록</Button></div></Card><button className="link-button add-store" onClick={() => go('/store/new')}>+ 매장 추가</button></div> : <Card className="member-activity"><strong>나의 활동</strong><div className="activity-columns"><button onClick={() => go('/jobs')}><span>신청 중 공고</span><strong className="primary-text">{activeJobs}건</strong></button><button onClick={() => go('/jobs')}><span>관심 공고</span><strong>{data.savedJobs.length}건</strong></button><button onClick={() => go('/manuals')}><span>정기 근무</span><strong>{data.workers.some(worker => worker.email === data.profile.email && worker.type === 'regular' && worker.status === 'active') ? 1 : 0}곳</strong></button></div></Card>}<div className="home-jobs"><div className="row between"><h2>{owner ? '모집 중 공고' : '추천 공고'}</h2>{owner && <button className="link-button" onClick={() => go('/jobs')}>{openJobs.length}건</button>}</div><div className="job-list">{openJobs.slice(0, 3).map(job => <JobCard job={job} key={job.id} />)}{!openJobs.length && <Empty title="모집 중인 공고가 없어요" description={owner ? '함께할 근무자를 찾는 공고를 등록해 보세요.' : '새로운 공고가 등록되면 이곳에서 확인할 수 있어요.'} />}</div>{!owner && <button className="more-jobs" onClick={() => go('/jobs')}>+ 공고 더 찾아보기</button>}</div><Calendar /></div>
}
function Notifications() {
  const { data, go } = useApp(); const invites = data.invites.filter(invite => invite.status === 'pending' && (data.role === 'owner' || invite.email === data.profile.email))
  return <div className="page-content stack"><Heading title="새로운 소식을 확인하세요" description="초대와 공고의 현재 상태를 확인할 수 있어요." />{invites.map(invite => <button className="list-button" key={invite.id} onClick={() => go(data.role === 'owner' ? '/invite' : `/invitation/${invite.id}`)}><Card className="stack"><Badge>매장 초대</Badge><h3>{data.store.name}</h3><p>{invite.name}님에게 도착한 {invite.task} 초대가 있어요.</p><p className="small muted">초대일로부터 7일간 유효해요.</p></Card></button>)}{data.jobs.filter(job => job.applicants.some(app => data.role === 'owner' ? app.status === 'pending' : app.email === data.profile.email && app.status === 'confirmed')).map(job => <button className="list-button" key={job.id} onClick={() => go(`/jobs/${job.id}`)}><Card className="stack"><Badge>{data.role === 'owner' ? '새 지원자' : '근무 확정'}</Badge><h3>{job.title}</h3><p className="small muted">{data.role === 'owner' ? '지원자를 확인하고 근무를 확정해 주세요.' : '첫 근무 전 담당 업무를 확인해 주세요.'}</p></Card></button>)}{!invites.length && !data.jobs.some(job => job.applicants.some(app => data.role === 'owner' ? app.status === 'pending' : app.email === data.profile.email && app.status === 'confirmed')) && <Empty title="새로운 소식이 없어요" />}</div>
}
function PrototypeTools() {
  const { data, update, go, reset } = useApp(); const [confirmReset, setConfirmReset] = useState(false)
  const switchRole = (role: 'owner' | 'member') => { update(previous => ({ ...previous, role })); go('/home') }
  return <><aside className="prototype-tools"><a className="prototype-brand" href="#/">지단<span>INTERACTIVE PROTOTYPE</span></a><h2>매장의 시작을,<br />더 단단하게.</h2><p>점주와 근무자의 경험을<br />직접 이어서 확인해 보세요.</p><div className="prototype-role-switch"><button className={data.role === 'owner' ? 'active' : ''} onClick={() => switchRole('owner')}>점주 체험</button><button className={data.role === 'member' ? 'active' : ''} onClick={() => switchRole('member')}>근무자 체험</button></div><div className="prototype-shortcuts"><button onClick={() => go('/roles')}>가입 흐름 보기 <span>↗</span></button><button onClick={() => { switchRole('owner'); go('/manuals') }}>매뉴얼 작성 · 게시 <span>↗</span></button><button onClick={() => { switchRole('owner'); go('/invite') }}>근무자 초대 <span>↗</span></button><button onClick={() => { switchRole('member'); go('/jobs') }}>대타 신청 · 온보딩 <span>↗</span></button></div><p className="prototype-info">샘플 데이터는 이 브라우저에 저장됩니다.<br />SSO·AI·이메일 발송은 데모 동작입니다.</p><button className="prototype-reset" onClick={() => setConfirmReset(true)}>샘플 데이터 초기화</button><a className="figma-link" href="https://www.figma.com/design/ZaFHresnBXJ1h98Xl1AUDj?node-id=0-1" target="_blank" rel="noreferrer">Figma Design ↗</a></aside>{confirmReset && <Modal title="샘플 데이터를 초기화할까요?" onClose={() => setConfirmReset(false)}><p>이 브라우저에서 변경한 프로토타입 데이터가 처음 상태로 돌아가요.</p><Button onClick={() => { reset(); setConfirmReset(false) }}>초기화하기</Button><Button variant="secondary" onClick={() => setConfirmReset(false)}>돌아가기</Button></Modal>}</>
}
const titleFor = (route: string, owner: boolean) => {
  if (route === '/') return '매장 AI 온보딩'
  if (route === '/roles') return '가입 유형 선택'
  if (route === '/store/new') return '매장 등록'
  if (route === '/store') return '매장 관리'
  if (route.startsWith('/workers')) return '근무 상태 관리'
  if (route === '/invite') return '근무자 초대'
  if (route.startsWith('/invitation')) return '초대 수락'
  if (route.endsWith('/career')) return '경력 추가'
  if (route.endsWith('/time')) return '가능 시간 추가'
  if (route === '/signup/review') return '입력 내용 확인'
  if (route === '/signup/complete') return '등록 완료'
  if (route.startsWith('/signup')) return '프로필 등록'
  if (route.startsWith('/profile')) return ({ '/profile': '내 프로필', '/profile/basic': '기본 정보 수정', '/profile/work': '근무 정보 수정', '/profile/availability': '가능 시간 수정' } as Record<string,string>)[route] || '프로필 수정'
  if (route === '/jobs/new') return '공고 등록'
  if (route === '/jobs') return owner ? '공고 관리' : '공고 찾기'
  if (route.startsWith('/jobs')) return '공고 상세'
  if (route === '/chat') return 'AI 업무 질문'
  if (route === '/manuals') return '업무 매뉴얼'
  if (route === '/manuals/new') return '매뉴얼 만들기'
  if (route.endsWith('/edit')) return '매뉴얼 편집'
  if (route.endsWith('/review')) return '검토 및 게시'
  if (route.startsWith('/manuals')) return '업무 매뉴얼'
  if (route === '/notifications') return '알림'
  return '지단'
}
function parentRoute(route: string) {
  if (route === '/roles') return '/'
  if (route.startsWith('/signup/')) return ({ '/signup/basic': '/roles', '/signup/work': '/signup/basic', '/signup/availability': '/signup/work', '/signup/review': '/signup/availability', '/signup/career': '/signup/work', '/signup/time': '/signup/availability' } as Record<string, string>)[route] || '/home'
  if (route.startsWith('/profile/')) return route.endsWith('/career') ? '/profile/work' : route.endsWith('/time') ? '/profile/availability' : '/profile'
  if (route.startsWith('/workers/')) return '/workers'
  if (['/invite', '/workers'].includes(route)) return '/store'
  if (route.startsWith('/jobs/')) return '/jobs'
  if (route.startsWith('/manuals/')) return route.endsWith('/edit') || route.endsWith('/review') ? route.split('/').slice(0, 3).join('/') : '/manuals'
  if (route === '/chat') return '/manuals'
  return '/home'
}
function Prototype() {
  const route = useRoute(); const { data, update, go } = useApp(); const pageRef = useRef<HTMLElement>(null); const owner = data.role === 'owner'
  const guest = !data.role && route !== '/roles' && !route.startsWith('/invitation/'); const current = guest ? '/' : route
  const login = current === '/'; const home = current === '/home'; const nav = ['/home', '/store', '/manuals', '/jobs', '/profile'].includes(current)
  const ownerOnly = ['/store', '/store/new', '/workers', '/invite', '/jobs/new', '/manuals/new'].includes(current) || current.startsWith('/workers/') || current.endsWith('/edit') || current.endsWith('/review') && !current.startsWith('/signup')
  useEffect(() => { window.scrollTo({ top: 0, behavior: 'instant' }); document.title = `${titleFor(current, owner)} · 지단`; if (pageRef.current) pageRef.current.focus({ preventScroll: true }) }, [current, owner])
  const notificationCount = data.invites.filter(invite => invite.status === 'pending' && (owner || invite.email === data.profile.email)).length + data.jobs.filter(job => job.applicants.some(app => owner ? app.status === 'pending' : app.email === data.profile.email && app.status === 'confirmed')).length
  let page
  if (login) page = <Login />
  else if (current === '/roles') page = <Roles />
  else if (ownerOnly && !owner) page = <div className="page-content stack"><Empty title="점주 전용 화면이에요" description="근무자는 담당 업무의 매뉴얼과 공고를 확인할 수 있어요." /><Button onClick={() => go('/home')}>홈으로</Button></div>
  else if (home) page = <Home />
  else if (/^\/(store|workers|invite|invitation)(\/|$)/.test(current)) page = <StorePages route={current} />
  else if (/^\/(signup|profile)(\/|$)/.test(current)) page = <MemberPages route={current} />
  else if (current.startsWith('/manuals') || current === '/chat') page = <ManualPages route={current} />
  else if (current.startsWith('/jobs')) page = <JobPages route={current} />
  else if (current === '/notifications') page = <Notifications />
  else page = <div className="page-content stack"><Empty title="페이지를 찾을 수 없어요" /><Button onClick={() => go('/home')}>홈으로 돌아가기</Button></div>
  return <><PrototypeTools /><div className={`app-shell ${login ? 'login-shell' : ''} ${nav ? 'with-navigation' : ''}`}><a className="skip-link" href="#main-content" onClick={event => { event.preventDefault(); pageRef.current?.focus() }}>본문으로 건너뛰기</a>{!login && <header className={`app-header ${home ? 'brand-header' : ''}`}>{home ? <button className="header-brand" onClick={() => go('/home')}>지단</button> : <div className="row"><button className="back-button" aria-label="뒤로가기" onClick={() => go(parentRoute(current))}><Icon name="arrow" /></button><strong>{titleFor(current, owner)}</strong></div>}{home && <button className="notification-button" aria-label={`알림 ${data.readNotifications ? 0 : notificationCount}개`} onClick={() => { update(previous => ({ ...previous, readNotifications: true })); go('/notifications') }}>{!data.readNotifications && notificationCount > 0 && <span>미확인 알림 {notificationCount}개</span>}<Icon name="bell" /></button>}</header>}<main ref={pageRef} id="main-content" tabIndex={-1} className="app-content" key={`${current}-${data.role}`}>{page}</main>{nav && <nav className="bottom-navigation" aria-label="주요 메뉴"><div className="nav-items">{[{ path: '/home', icon: 'home', label: '홈' }, { path: '/manuals', icon: 'book', label: '매뉴얼' }, { path: '/jobs', icon: 'brief', label: owner ? '공고 관리' : '공고 찾기' }, { path: '/profile', icon: 'user-muted', label: '프로필' }].map(item => <a key={item.path} href={`#${item.path}`} className={current === item.path ? 'active' : ''} aria-current={current === item.path ? 'page' : undefined}><Icon name={item.icon} /><span>{item.label}</span></a>)}</div><span className="home-indicator" /></nav>}</div></>
}
export default function App() { return <AppProvider><Prototype /></AppProvider> }

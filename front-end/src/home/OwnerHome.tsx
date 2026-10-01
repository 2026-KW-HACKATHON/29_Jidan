import { useState } from 'react'
import { Button } from '../ui/Button'
import { Modal } from '../ui/Modal'
import { HomeCalendar, type CalendarMark } from './HomeCalendar'
import { HomeShell } from './HomeShell'
import { eventsInView, isoMonth, shortDate } from './calendarGrid'
import './OwnerHome.css'

export type OwnerJob = { id: string; title: string; schedule: string; applicants: number }
export type OwnerEvent = { id: string; date: string; descriptions: string[]; substitute?: boolean }
export type OwnerHomeData = {
  store?: { name: string; status: 'operating' | 'pending' }
  jobs?: OwnerJob[]
  events?: OwnerEvent[]
  marks?: CalendarMark[]
  notificationCount?: number
}

export function OwnerHome({ displayName, data = {}, initialDate, onManage, onCreateJob, onJobs }: {
  displayName: string
  data?: OwnerHomeData
  initialDate?: Date
  onManage?: () => void
  onCreateJob?: () => void
  onJobs?: () => void
}) {
  const [unavailable, setUnavailable] = useState<string | null>(null)
  const [today] = useState(() => initialDate ?? new Date())
  const [visibleMonth, setVisibleMonth] = useState(() => isoMonth(today.getFullYear(), today.getMonth()))
  const [selectedDate, setSelectedDate] = useState<string | null>(null)
  const jobs = data.jobs || []
  const events = eventsInView(data.events || [], visibleMonth, selectedDate, selectedDate ? undefined : 3)
  return <>
    <HomeShell role="owner" notificationCount={data.notificationCount}>
      <div className="home-greeting"><h2>안녕하세요, {displayName} 점주님</h2><p>오늘의 매장 운영 현황을 확인하세요.</p></div>
      <section className="home-section owner-home-store-section" aria-label="관리 매장">
        <div className="owner-home-store home-card"><p>관리 매장</p>
          {data.store ? <><div className="owner-home-store-title"><h3>{data.store.name}</h3><span className={data.store.status === 'operating' ? 'owner-home-operating' : 'owner-home-pending'}>{data.store.status === 'operating' ? '운영 중' : '승인 대기 중'}</span></div>
            <div className="owner-home-store-actions"><Button intent="secondary" onClick={onManage || (() => setUnavailable('매장 관리'))}>매장 관리</Button><Button intent="secondary" onClick={onCreateJob || (() => setUnavailable('공고 등록'))}>공고 등록</Button></div></> : <div className="owner-home-no-store">매장 정보를 불러오지 못했어요.</div>}
        </div>
        <button type="button" className="owner-home-add-store" onClick={() => setUnavailable('매장 추가')}>+ 매장 추가</button>
      </section>
      <section className="home-section" aria-labelledby="owner-home-jobs-title">
        <div className="home-section-heading"><h3 id="owner-home-jobs-title">모집 중 공고</h3><span>{jobs.length}건</span></div>
        {jobs.length ? jobs.slice(0, 3).map(job => <article className="owner-home-job home-card" key={job.id}><div><h4>{job.title}</h4><span className={job.applicants ? 'owner-home-applicants' : 'owner-home-no-applicants'}>{job.applicants ? `지원자 ${job.applicants}명` : '지원자 없음'}</span></div><p>{job.schedule}</p></article>) : <p className="home-empty home-card">모집 중인 공고가 없어요.</p>}
        {jobs.length > 3 && <button type="button" className="owner-home-more" onClick={onJobs || (() => setUnavailable('공고 모두 보기'))}>+ 공고 모두 보기</button>}
      </section>
      <section className="home-section" aria-labelledby="owner-home-calendar-title">
        <div className="home-section-heading"><h3 id="owner-home-calendar-title">매장 캘린더</h3><button type="button" className="owner-home-add-event" onClick={() => setUnavailable('일정 등록')}>+ 일정 등록</button></div>
        <HomeCalendar label="매장 캘린더" marks={data.marks} initialDate={today} onMonthChange={(year, month) => { setVisibleMonth(isoMonth(year, month)); setSelectedDate(null) }} onDateSelect={setSelectedDate} />
        {events.length ? <div className="owner-home-events home-card">{events.map(event => <div className="owner-home-event" key={event.id}><strong>{shortDate(event.date)}</strong><div>{event.descriptions.map((line, index) => <p key={`${event.id}-${index}`}>{line}</p>)}</div>{event.substitute && <span>대타 근무</span>}</div>)}</div> : <p className="home-empty home-card">{selectedDate ? '선택한 날짜에 일정이 없어요.' : '이 달에 일정이 없어요.'}</p>}
      </section>
    </HomeShell>
    <Modal open={unavailable !== null} state="information" title={`${unavailable} 화면을 준비하고 있어요`} description="현재 화면에서 계속 이용해 주세요." cancelLabel="닫기" onClose={() => setUnavailable(null)} />
  </>
}

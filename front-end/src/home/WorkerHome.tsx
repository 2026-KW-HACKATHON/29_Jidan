import { useState } from 'react'
import { Modal } from '../ui/Modal'
import { HomeCalendar, type CalendarMark } from './HomeCalendar'
import { HomeShell } from './HomeShell'
import { eventsInView, isoMonth, shortDate } from './calendarGrid'
import './WorkerHome.css'

export type WorkerJob = { id: string; title: string; schedule: string; status: string }
export type WorkerShift = { id: string; date: string; storeName: string; kind: 'regular' | 'substitute' }
export type WorkerHomeData = {
  activity?: { applications: number; favoriteStores: number; regularStores: number }
  recommendations?: WorkerJob[]
  shifts?: WorkerShift[]
  marks?: CalendarMark[]
  notificationCount?: number
}

export function WorkerHome({ displayName, data = {}, initialDate, onMonthChange, onManual, onNotifications, onProfile, onJobs, onSelectJob }: {
  displayName: string
  data?: WorkerHomeData
  initialDate?: Date
  onMonthChange?: (year:number,month:number)=>void
  onManual?:()=>void
  onNotifications?:()=>void
  onSelectJob?:(id:string)=>void
  onProfile?: () => void
  onJobs?: () => void
}) {
  const [unavailable, setUnavailable] = useState<string | null>(null)
  const [today] = useState(() => initialDate ?? new Date())
  const [visibleMonth, setVisibleMonth] = useState(() => isoMonth(today.getFullYear(), today.getMonth()))
  const [selectedDate, setSelectedDate] = useState<string | null>(null)
  const activity = data.activity || { applications: 0, favoriteStores: 0, regularStores: 0 }
  const jobs = data.recommendations || []
  const shifts = eventsInView(data.shifts || [], visibleMonth, selectedDate, selectedDate ? undefined : 3)
  return <>
    <HomeShell onManual={onManual} onNotifications={onNotifications} role="worker" notificationCount={data.notificationCount} onProfile={onProfile} onJobs={onJobs}>
      <div className="home-greeting"><h2>안녕하세요, {displayName}님</h2><p>원하는 공고를 찾아보세요.</p></div>
      <section className="worker-home-activity home-card" aria-label="나의 활동"><h3>나의 활동</h3><div>
        <div><span>신청 중 공고</span><strong>{activity.applications}건</strong></div>
        <div><span>관심 매장</span><strong>{activity.favoriteStores}건</strong></div>
        <div><span>정기 근무</span><strong>{activity.regularStores}곳</strong></div>
      </div></section>
      <section className="home-section" aria-labelledby="worker-home-jobs-title"><div className="home-section-heading"><h3 id="worker-home-jobs-title">추천 공고</h3></div>
        {jobs.length ? jobs.map(job => <article className="worker-home-job home-card" key={job.id}><div><h4>{onSelectJob?<button className="worker-home-job-link" onClick={()=>onSelectJob(job.id)}>{job.title}</button>:job.title}</h4><span className={job.status === '지원자 없음' ? 'worker-home-no-applicants' : undefined}>{job.status}</span></div><p>{job.schedule}</p></article>) : <p className="home-empty home-card">추천 공고가 없어요.</p>}
        <button type="button" className="worker-home-more" onClick={onJobs || (() => setUnavailable('공고 찾기'))}>+ 공고 더 찾아보기</button>
      </section>
      <section className="home-section" aria-labelledby="worker-home-calendar-title"><div className="home-section-heading"><h3 id="worker-home-calendar-title">근무 캘린더</h3></div>
        <HomeCalendar role="worker" label="근무 캘린더" marks={data.marks} initialDate={today} onMonthChange={(year, month) => { setVisibleMonth(isoMonth(year, month)); setSelectedDate(null); onMonthChange?.(year,month) }} onDateSelect={setSelectedDate} />
        {shifts.length ? <div className="worker-home-shifts home-card">{shifts.map(shift => <div className="worker-home-shift" key={shift.id}><strong>{shortDate(shift.date)}</strong><span>{shift.storeName}</span><em className={`worker-home-${shift.kind}`}>{shift.kind === 'regular' ? '정기 근무' : '대타 근무'}</em></div>)}</div> : <p className="home-empty home-card">{selectedDate ? '선택한 날짜에 근무가 없어요.' : '예정된 근무가 없어요.'}</p>}
      </section>
    </HomeShell>
    <Modal open={unavailable !== null} state="information" title={`${unavailable} 화면을 준비하고 있어요`} description="현재 화면에서 계속 이용해 주세요." cancelLabel="닫기" onClose={() => setUnavailable(null)} />
  </>
}

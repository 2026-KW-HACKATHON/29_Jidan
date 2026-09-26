import { useState } from 'react'
import arrow from './assets/arrow.svg'
import chevron from './assets/chevron.svg'
import memberArrow from './assets/member-arrow.svg'
import memberChevron from './assets/member-chevron.svg'
import { monthCells, isoDate, isoMonth } from './calendarGrid'
import type { HomeRole } from './HomeShell'

export type CalendarMark = { date: string; kind?: 'regular' | 'substitute' }
const weekdays = ['일', '월', '화', '수', '목', '금', '토']
export function HomeCalendar({ marks = [], initialDate = new Date(), label, role = 'owner', onMonthChange, onDateSelect }: {
  marks?: CalendarMark[]
  initialDate?: Date
  label: string
  role?: HomeRole
  onMonthChange?: (year: number, month: number) => void
  onDateSelect?: (date: string | null) => void
}) {
  const [today] = useState(initialDate)
  const [visible, setVisible] = useState(() => new Date(today.getFullYear(), today.getMonth(), 1))
  const [selected, setSelected] = useState<string | null>(null)
  const year = visible.getFullYear(), month = visible.getMonth()
  const todayKey = isoDate(today.getFullYear(), today.getMonth(), today.getDate())
  const activeDate = selected || (isoMonth(year, month) === isoMonth(today.getFullYear(), today.getMonth()) ? todayKey : null)
  const marked = new Map(marks.map(mark => [mark.date, mark.kind || 'regular']))
  const cells = monthCells(year, month)

  function move(offset: number) {
    const next = new Date(year, month + offset, 1)
    setVisible(next)
    setSelected(null)
    onMonthChange?.(next.getFullYear(), next.getMonth())
  }
  function select(day: number) {
    const date = isoDate(year, month, day)
    const next = date === todayKey ? null : date
    setSelected(next)
    onDateSelect?.(next)
  }

  return <div className="home-calendar home-card" aria-label={label}>
    <div className="home-calendar-heading"><strong>{year}년 {month + 1}월</strong><button type="button" aria-label="이전 달" onClick={() => move(-1)}><img src={role === 'owner' ? arrow : memberArrow} alt="" /></button><button type="button" aria-label="다음 달" onClick={() => move(1)}><img src={role === 'owner' ? chevron : memberChevron} alt="" /></button></div>
    <div className="home-calendar-weekdays" aria-hidden="true">{weekdays.map(day => <span key={day}>{day}</span>)}</div>
    <div className="home-calendar-days">{cells.map((day, index) => day === null ? <span key={`empty-${index}`} /> : <button type="button" key={day} aria-label={`${year}년 ${month + 1}월 ${day}일`} aria-current={todayKey === isoDate(year, month, day) ? 'date' : undefined} aria-pressed={activeDate === isoDate(year, month, day)} data-user-selected={selected === isoDate(year, month, day) ? 'true' : undefined} className={marked.has(isoDate(year, month, day)) ? `home-calendar-${marked.get(isoDate(year, month, day))}` : ''} onClick={() => select(day)}><span>{day}</span></button>)}</div>
  </div>
}

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { AppProvider, STORAGE_KEY } from '../state'
import { MemberPages } from './MemberPages'
import { normalizeAvailability } from './availability'

const period = (day: number, start: string, end: string) => ({ id: `${day}-${start}-${end}`, day, start, end })
const ranges = (values: ReturnType<typeof normalizeAvailability>) => values.map(({ day, start, end }) => ({ day, start, end }))

describe('recurring availability', () => {
  it('merges overlaps and touching ranges without double counting', () => {
    expect(ranges(normalizeAvailability([
      period(1, '09:00', '12:00'), period(1, '11:30', '14:00'), period(1, '14:00', '15:00'), period(1, '09:00', '12:00'),
    ]))).toEqual([{ day: 1, start: '09:00', end: '15:00' }])
  })

  it('preserves gaps and distinct days', () => {
    expect(ranges(normalizeAvailability([
      period(1, '09:00', '10:00'), period(1, '10:30', '12:00'), period(3, '09:00', '10:00'),
    ]))).toEqual([
      { day: 1, start: '09:00', end: '10:00' }, { day: 1, start: '10:30', end: '12:00' }, { day: 3, start: '09:00', end: '10:00' },
    ])
  })

  it('splits an overnight range and merges the next morning overlap', () => {
    expect(ranges(normalizeAvailability([
      period(1, '22:00', '02:00'), period(2, '01:00', '03:00'),
    ]))).toEqual([{ day: 1, start: '22:00', end: '24:00' }, { day: 2, start: '00:00', end: '03:00' }])
  })

  it('wraps Saturday overnight into Sunday using JavaScript weekday values', () => {
    expect(ranges(normalizeAvailability([period(6, '23:30', '01:00')]))).toEqual([
      { day: 6, start: '23:30', end: '24:00' }, { day: 0, start: '00:00', end: '01:00' },
    ])
  })

  it('preserves an explicit full day and remains stable when normalized again', () => {
    const result = normalizeAvailability([period(3, '00:00', '24:00')])
    expect(ranges(result)).toEqual([{ day: 3, start: '00:00', end: '24:00' }])
    expect(normalizeAvailability(result)).toEqual(result)
    expect(normalizeAvailability([])).toEqual([])
  })
})

describe('member profile forms', () => {
  beforeEach(() => {
    const storage = () => {
      const values = new Map<string, string>()
      return { getItem: (key: string) => values.get(key) ?? null, setItem: (key: string, value: string) => values.set(key, value), removeItem: (key: string) => values.delete(key), clear: () => values.clear() }
    }
    vi.stubGlobal('localStorage', storage()); vi.stubGlobal('sessionStorage', storage()); window.location.hash = ''
  })
  afterEach(() => { cleanup(); vi.unstubAllGlobals() })

  it('keeps a malformed phone number on the basic step with an actionable error', () => {
    render(<AppProvider><MemberPages route="/signup/basic" /></AppProvider>)
    expect(screen.getByLabelText('이메일 | Google 연동')).toHaveAttribute('readonly')
    fireEvent.change(screen.getByLabelText('전화번호 *'), { target: { value: '123' } })
    fireEvent.click(screen.getByRole('button', { name: '다음' }))
    expect(screen.getByRole('alert')).toHaveTextContent('전화번호를 확인')
    expect(window.location.hash).toBe('')
  })

  it('disables end month for current employment and persists the saved career', () => {
    render(<AppProvider><MemberPages route="/signup/career" /></AppProvider>)
    fireEvent.change(screen.getByLabelText('담당 업무 *'), { target: { value: '음료 제조' } })
    fireEvent.change(screen.getByLabelText('시작 연월 *'), { target: { value: '2024-03' } })
    fireEvent.click(screen.getByRole('checkbox', { name: '현재 근무 중' }))
    expect(screen.getByLabelText('종료 연월 *')).toBeDisabled()
    fireEvent.click(screen.getByRole('button', { name: '경력 저장' }))
    expect(window.location.hash).toBe('#/signup/work')
    const stored = JSON.parse(localStorage.getItem(STORAGE_KEY)!)
    expect(stored.profile.careers.at(-1)).toMatchObject({ task: '음료 제조', current: true, end: '' })
  })

  it('requires overnight confirmation and merges the saved early-morning interval', () => {
    render(<AppProvider><MemberPages route="/signup/time" /></AppProvider>)
    fireEvent.change(screen.getByLabelText('시작 시간 *'), { target: { value: '22:00' } })
    fireEvent.change(screen.getByLabelText('종료 시간 *'), { target: { value: '02:00' } })
    fireEvent.click(screen.getByRole('button', { name: '시간 추가' }))
    expect(screen.getByRole('alert')).toHaveTextContent('다음 날 종료')
    fireEvent.click(screen.getByRole('checkbox', { name: '다음 날 종료' }))
    fireEvent.click(screen.getByRole('button', { name: '시간 추가' }))
    expect(window.location.hash).toBe('#/signup/availability')
    const stored = JSON.parse(localStorage.getItem(STORAGE_KEY)!)
    expect(stored.profile.availability).toEqual(expect.arrayContaining([
      expect.objectContaining({ day: 1, start: '22:00', end: '24:00' }),
      expect.objectContaining({ day: 2, start: '00:00', end: '02:00' }),
    ]))
  })
})

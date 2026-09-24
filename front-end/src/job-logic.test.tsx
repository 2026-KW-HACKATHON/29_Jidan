import { beforeEach, describe, expect, it, vi } from 'vitest'
import { createDemoData, hasMemberAccess } from './state'
import { confirmApplicant, hasConflict, jobEnd, jobStart, matchesAvailability } from './job-logic'
import { AppProvider, STORAGE_KEY } from './state'
import { JobPages } from './pages/JobPages'
import { fireEvent, render, screen } from '@testing-library/react'

beforeEach(() => { const storage = new Map<string, string>(); vi.stubGlobal('localStorage', { getItem: (key: string) => storage.get(key) ?? null, setItem: (key: string, value: string) => storage.set(key, value) }); window.location.hash = '' })
describe('connected recruitment', () => {
  it('confirms once, fills capacity, and grants only the assigned task until shift end', () => {
    const data = createDemoData(); data.role = 'owner'; data.workers = []; data.jobs[0].applicants[0].email = data.profile.email
    const result = confirmApplicant(data, 'job-1', 'app-1')
    expect(result.error).toBeUndefined(); expect(result.data.jobs[0].status).toBe('closed')
    expect(result.data.workers).toHaveLength(1); expect(result.data.workers[0].endDate).toBe(new Date(jobEnd(data.jobs[0])).toISOString())
    expect(hasMemberAccess(result.data, '홀 서빙')).toBe(true); expect(hasMemberAccess(result.data, '매장 마감')).toBe(false)
    const duplicate = confirmApplicant(result.data, 'job-1', 'app-1'); expect(duplicate.error).toBeTruthy(); expect(duplicate.data.workers).toHaveLength(1)
    expect(hasMemberAccess({ ...result.data, workers: result.data.workers.map(worker => ({ ...worker, endDate: '2020-01-01T10:00:00Z' })) })).toBe(false)
  })
  it('blocks overlapping confirmed shifts, but permits adjacent shifts', () => {
    const data = createDemoData(); data.role = 'owner'; const first = data.jobs[0]; first.applicants[0].status = 'confirmed'
    const second = data.jobs[1]; second.date = first.date; second.start = '13:00'; second.end = '18:00'; second.applicants[0].email = first.applicants[0].email
    expect(hasConflict(data.jobs, second, second.applicants[0].email)).toBe(true)
    expect(confirmApplicant(data, second.id, second.applicants[0].id).error).toContain('겹쳐요')
    second.start = '14:00'; expect(hasConflict(data.jobs, second, second.applicants[0].email)).toBe(false)
  })
  it('rejects withdrawn applicants, members, and past-start confirmation', () => {
    const data = createDemoData(); data.role = 'owner'; data.jobs[0].applicants[0].status = 'withdrawn'
    expect(confirmApplicant(data, 'job-1', 'app-1').error).toBeTruthy()
    data.jobs[0].applicants[0].status = 'pending'; data.role = 'member'; expect(confirmApplicant(data, 'job-1', 'app-1').error).toBeTruthy()
    data.role = 'owner'; data.jobs[0].date = '2020-01-01'; expect(confirmApplicant(data, 'job-1', 'app-1').error).toBeTruthy()
  })
  it('applies once and withdrawal removes the pending application', () => {
    const data = createDemoData(); data.role = 'member'; localStorage.setItem(STORAGE_KEY, JSON.stringify(data))
    render(<AppProvider><JobPages route="/jobs/job-1" /></AppProvider>)
    fireEvent.click(screen.getByRole('button', { name: '이 공고에 신청하기' }))
    expect(screen.getByText('신청 완료 · 확정 대기')).toBeInTheDocument()
    let saved = JSON.parse(localStorage.getItem(STORAGE_KEY)!); expect(saved.jobs[0].applicants.filter((item: {email:string}) => item.email === data.profile.email)).toHaveLength(1)
    fireEvent.click(screen.getByRole('button', { name: '신청 철회하기' })); fireEvent.click(screen.getByRole('button', { name: '철회하기' }))
    saved = JSON.parse(localStorage.getItem(STORAGE_KEY)!); expect(saved.jobs[0].applicants.at(-1).status).toBe('withdrawn')
    expect(screen.getByRole('button', { name: '이 공고에 신청하기' })).toBeInTheDocument()
  })
  it('does not use retained hidden careers for an inexperienced profile', () => {
    const data = createDemoData(); data.role = 'member'; data.profile.experience = 'new'; data.jobs[0].experience = '관련 업무 경험자'
    localStorage.setItem(STORAGE_KEY, JSON.stringify(data)); render(<AppProvider><JobPages route="/jobs/job-1" /></AppProvider>)
    fireEvent.click(screen.getByRole('button', { name: '이 공고에 신청하기' })); expect(screen.getByRole('alert')).toHaveTextContent('관련 업무 경험')
    expect(JSON.parse(localStorage.getItem(STORAGE_KEY)!).jobs[0].applicants).toHaveLength(3)
  })
  it('rechecks the actual clock when a previously opened job is submitted', () => {
    const data = createDemoData(); data.role = 'member'; localStorage.setItem(STORAGE_KEY, JSON.stringify(data))
    render(<AppProvider><JobPages route="/jobs/job-1" /></AppProvider>)
    const clock = vi.spyOn(Date, 'now').mockReturnValue(jobStart(data.jobs[0]) + 1)
    fireEvent.click(screen.getByRole('button', { name: '이 공고에 신청하기' }))
    expect(screen.getByRole('alert')).toHaveTextContent('신청할 수 없는 공고'); clock.mockRestore()
  })
})
describe('availability matches', () => {
  it('requires coverage of the complete shift, including next-day time', () => {
    const job = createDemoData().jobs[0]; const day = new Date(`${job.date}T12:00`).getDay()
    const range = { id: 'range', day, start: '09:00', end: '14:00' }
    expect(matchesAvailability(job, [range])).toBe(true)
    expect(matchesAvailability(job, [{ ...range, end: '13:30' }])).toBe(false)
    job.start = '22:00'; job.end = '02:00'
    expect(jobEnd(job) - jobStart(job)).toBe(4 * 3600000)
    expect(matchesAvailability(job, [{ ...range, start: '22:00', end: '24:00' }, { ...range, day: (day + 1) % 7, start: '00:00', end: '02:00' }])).toBe(true)
    expect(matchesAvailability(job, [{ ...range, start: '22:00', end: '24:00' }])).toBe(false)
  })
})

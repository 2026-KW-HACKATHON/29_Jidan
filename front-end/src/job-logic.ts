import type { AppData, Job } from './state'

export const jobStart = (job: Job) => new Date(`${job.date}T${job.start}`).getTime()
export function jobEnd(job: Job) { const end = new Date(`${job.date}T${job.end}`).getTime(); return end <= jobStart(job) ? end + 86400000 : end }
export function hasConflict(jobs: Job[], current: Job, email: string) { return jobs.some(job => job.id !== current.id && job.applicants.some(app => app.email === email && app.status === 'confirmed') && jobStart(current) < jobEnd(job) && jobEnd(current) > jobStart(job)) }
export const jobDateLabel = (job: Job) => `${Number(job.date.slice(5, 7))}월 ${Number(job.date.slice(8))}일 ㅣ ${job.start}–${job.end}${job.end <= job.start ? ' (다음 날)' : ''}`
export function confirmApplicant(data: AppData, jobId: string, applicantId: string): { data: AppData; error?: string } {
  const job = data.jobs.find(item => item.id === jobId); const applicant = job?.applicants.find(item => item.id === applicantId)
  if (!job || !applicant || data.role !== 'owner' || job.storeId !== data.store.id || data.store.status !== 'active' || job.status !== 'open' || applicant.status !== 'pending' || jobStart(job) <= Date.now()) return { data, error: '현재 확정할 수 없는 신청이에요. 공고와 지원 상태를 확인해 주세요.' }
  if (job.applicants.filter(item => item.status === 'confirmed').length >= job.capacity) return { data, error: '이미 모집 인원이 모두 확정되었어요.' }
  if (hasConflict(data.jobs, job, applicant.email)) return { data, error: '이미 확정된 근무와 시간이 겹쳐요. 다른 지원자를 선택해 주세요.' }
  const applicants = job.applicants.map(item => item.id === applicant.id ? { ...item, status: 'confirmed' as const } : item)
  return { data: { ...data, readNotifications: false, jobs: data.jobs.map(item => item.id === job.id ? { ...item, applicants, status: applicants.filter(item => item.status === 'confirmed').length >= job.capacity ? 'closed' : 'open' } : item), workers: [...data.workers, { id: `shift-${job.id}-${applicant.id}`, name: applicant.name, email: applicant.email, task: job.task, type: 'temporary', status: 'active', startDate: new Date().toISOString(), endDate: new Date(jobEnd(job)).toISOString() }] } }
}

export function matchesAvailability(job: Job, availability: AppData['profile']['availability']) {
  const begin = jobStart(job), end = jobEnd(job)
  for (let cursor = begin; cursor < end; cursor += 30 * 60000) {
    const moment = new Date(cursor)
    const minutes = moment.getHours() * 60 + moment.getMinutes()
    if (!availability.some(range => {
      const [sh, sm] = range.start.split(':').map(Number)
      const [eh, em] = range.end.split(':').map(Number)
      return range.day === moment.getDay() && sh * 60 + sm <= minutes && eh * 60 + em >= minutes + Math.min(30, (end - cursor) / 60000)
    })) return false
  }
  return true
}

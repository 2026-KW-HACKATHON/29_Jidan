import { createHangulSearch } from './search'

export type Industry = '카페' | '식당' | '편의점' | '기타'
export type Job = {
  id: string; industry: Industry; title: string; storeName: string; address: string; district?: string
  date: string; start: string; end: string; nextDay: boolean
  hourlyPay: number; headcount: number; applicants: number; publishedAt: string
  experience: string; tasks: string[]
}
export type JobFilters = { industry: '전체' | Industry; date: '전체' | '오늘' | '내일' | '일주일 이내' | '한달 이내'; time: '전체' | '오전' | '오후' | '야간' }
export const defaultFilters: JobFilters = { industry: '전체', date: '전체', time: '전체' }
function dateKey(date: Date) { return `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, '0')}-${String(date.getDate()).padStart(2, '0')}` }
function addDays(date: Date, days: number) { return new Date(date.getFullYear(), date.getMonth(), date.getDate() + days) }
export function filterJobs(jobs: readonly Job[], query: string, filters: JobFilters, today: Date): Job[] {
  const first = dateKey(today)
  const monthEnd = new Date(today.getFullYear(), today.getMonth() + 2, 0).getDate()
  const nextMonth = dateKey(new Date(today.getFullYear(), today.getMonth() + 1, Math.min(today.getDate(), monthEnd)))
  const matchesSearch = createHangulSearch(query)
  return jobs.filter(job => {
    if (filters.industry !== '전체' && job.industry !== filters.industry) return false
    if (!matchesSearch([job.title, job.storeName, job.industry, job.industry === '식당' ? '음식점' : job.industry, ...job.tasks])) return false
    if (filters.date === '오늘' && job.date !== first) return false
    if (filters.date === '내일' && job.date !== dateKey(addDays(today, 1))) return false
    if (filters.date === '일주일 이내' && (job.date < first || job.date >= dateKey(addDays(today, 7)))) return false
    if (filters.date === '한달 이내' && (job.date < first || job.date >= nextMonth)) return false
    const hour = Number(job.start.slice(0, 2))
    if (filters.time === '오전' && !(hour >= 6 && hour < 12)) return false
    if (filters.time === '오후' && !(hour >= 12 && hour < 18)) return false
    if (filters.time === '야간' && !(hour >= 18 || hour < 6)) return false
    return true
  }).sort((a, b) => b.publishedAt.localeCompare(a.publishedAt) || a.id.localeCompare(b.id))
}
export function jobHours(job: Pick<Job, 'start' | 'end' | 'nextDay'>) {
  const minutes = (time: string) => Number(time.slice(0, 2)) * 60 + Number(time.slice(3))
  return (minutes(job.end) + (job.nextDay ? 1440 : 0) - minutes(job.start)) / 60
}
export function jobTime(job: Pick<Job, 'start' | 'end' | 'nextDay'>) { return `${job.start}–${job.nextDay ? '다음 날 ' : ''}${job.end}` }
export function jobDate(date: string) { return date.replaceAll('-', '.') }
export function won(amount: number) { return `${amount.toLocaleString('ko-KR')}원` }

export function shortJobDate(date: string) { return `${Number(date.slice(5,7))}월 ${Number(date.slice(8))}일` }

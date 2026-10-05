import { industries, type Industry } from '../owner/model'
export const weekdays = ['월', '화', '수', '목', '금', '토', '일'] as const
export type Career = { id: string; industry: Industry; duties: string; store: string; start: string; end: string; current: boolean }
export type Availability = { id: string; days: number[]; start: number; end: number; overnight: boolean }
export type WorkerDraft = { name: string; phone: string; birth: string; gender: '' | '남성' | '여성'; experience: '' | '신입' | '경력 있음'; careers: Career[]; availability: Availability[] }
export type Errors = Record<string, string>
export const emptyWorker: WorkerDraft = { name: '', phone: '', birth: '', gender: '', experience: '', careers: [], availability: [] }
export const emptyCareer: Career = { id: '', industry: '', duties: '', store: '', start: '', end: '', current: false }
export const emptyAvailability: Availability = { id: '', days: [], start: -1, end: -1, overnight: false }
export function today() { const parts = new Intl.DateTimeFormat('en-CA',{timeZone:'Asia/Seoul',year:'numeric',month:'2-digit',day:'2-digit'}).formatToParts(new Date()); return ['year','month','day'].map(type=>parts.find(part=>part.type===type)!.value).join('-') }
export function validDate(value: string, current = today()) {
  if (!/^\d{4}-\d{2}-\d{2}$/.test(value) || value < '0001-01-01' || value > current) return false
  const date = new Date(`${value}T00:00:00Z`)
  return Number.isFinite(date.getTime()) && date.toISOString().slice(0,10) === value
}
export function validateCareer(c: Career, _birth: string, current = today()): Errors {
  const e: Errors = {}, month = /^\d{4}-(0[1-9]|1[0-2])$/
  if (!industries.includes(c.industry as typeof industries[number])) e.industry = '업종을 선택해 주세요.'
  if (!c.duties.trim() || c.duties.trim().length > 300) e.duties = '담당 업무를 1~300자로 입력해 주세요.'
  if (c.store.trim().length > 100) e.store = '매장명은 100자 이내로 입력해 주세요.'
  if (!month.test(c.start) || c.start.startsWith('0000') || c.start > current.slice(0,7)) e.start = '올바른 시작 연월을 선택해 주세요.'
  if (!c.current && (!month.test(c.end) || c.end < c.start || c.end > current.slice(0,7))) e.end = '시작 연월 이후의 올바른 종료 연월을 선택해 주세요.'
  return e
}
export const duration = (a: Availability) => a.end + (a.overnight ? 1440 : 0) - a.start
export function slots(a: Availability): number[] {
  const result: number[] = []
  for (const day of a.days) for (let minute = a.start; minute < a.start + duration(a); minute += 30) result.push((day * 48 + minute / 30) % 336)
  return result
}
export function validateAvailability(a: Availability, others: Availability[] = []): Errors {
  const e: Errors = {}
  if (!a.days.length || new Set(a.days).size !== a.days.length || a.days.some(d => !Number.isInteger(d) || d < 0 || d > 6)) e.days = '가능한 요일을 선택해 주세요.'
  if (![a.start,a.end].every(t => Number.isInteger(t) && t >= 0 && t < 1440 && t % 30 === 0)) e.time = '시간을 30분 단위로 선택해 주세요.'
  else if (duration(a) > 1440) e.time = '근무 가능 시간은 하루 24시간을 초과할 수 없어요.'
  else if (duration(a) <= 0) e.time = '종료 시간과 다음 날 종료 여부를 확인해 주세요.'
  if (!Object.keys(e).length) {
    const occupied = new Set(others.flatMap(slots))
    if (slots(a).some(s => occupied.has(s))) e.time = '이미 등록한 시간과 겹쳐요. 요일과 시간을 확인해 주세요.'
  }
  return e
}
export function validateWorker(d: WorkerDraft, step: number, current = today()): Errors {
  const e: Errors = {}
  if (step === 1 || step === 4) {
    if (!d.name.trim() || d.name.trim().length > 50) e.name = '이름을 1~50자로 입력해 주세요.'
    if (!/^010[0-9]{8}$/.test(d.phone.replace(/[\s-]/g,''))) e.phone = '올바른 휴대전화 번호를 입력해 주세요.'
    if (!validDate(d.birth,current)) e.birth = '오늘 이전의 올바른 생년월일을 입력해 주세요.'
    if (!['남성','여성'].includes(d.gender)) e.gender = '성별을 선택해 주세요.'
  }
  if (step === 2 || step === 4) {
    if (!['신입','경력 있음'].includes(d.experience)) e.experience = '근무 경력을 선택해 주세요.'
    if (d.careers.length > 20) e.careers = '경력은 20건까지 등록할 수 있어요.'
    if (d.experience === '경력 있음' && (!d.careers.length || d.careers.some(c => Object.keys(validateCareer(c,d.birth,current)).length))) e.careers = '올바른 경력을 한 건 이상 등록해 주세요.'
  }
  if (step === 3 || step === 4) {
    if (d.availability.length > 100) e.availability = '근무 가능 시간은 100건까지 등록할 수 있어요.'
    if (!d.availability.length || d.availability.some((a,i) => Object.keys(validateAvailability(a,d.availability.slice(0,i))).length)) e.availability = '겹치지 않는 근무 가능 시간을 등록해 주세요.'
  }
  return e
}
export const clockText = (minutes: number) => `${String(Math.floor(minutes/60)).padStart(2,'0')}:${String(minutes%60).padStart(2,'0')}`
export const daysText = (days: number[]) => [...days].sort((a,b)=>a-b).map(d=>weekdays[d]).join(' · ')
export const rangeText = (a: Availability) => `${clockText(a.start)}–${a.overnight ? '다음 날 ' : ''}${clockText(a.end)}`
export const weekHours = (values: Availability[]) => new Set(values.flatMap(slots)).size / 2
export function careerPeriod(c: Career) {
  const end = c.current ? today().slice(0,7) : c.end
  const months = (Number(end.slice(0,4))-Number(c.start.slice(0,4)))*12 + Number(end.slice(5))-Number(c.start.slice(5))+1
  return `${c.start.replace('-', '. ')} – ${c.current ? '현재' : end.replace('-', '. ')} · ${Math.floor(months/12) ? `${Math.floor(months/12)}년` : ''}${months%12 ? ` ${months%12}개월` : ''}`
}
export function normalizedWorker(d: WorkerDraft): WorkerDraft {
  return { ...d, name: d.name.trim(), phone: d.phone.replace(/[\s-]/g,''), careers: d.experience === '신입' ? [] : d.careers.map(c => ({ ...c, duties: c.duties.trim(), store: c.store.trim(), end: c.current ? '' : c.end })) }
}
/** Rebuild daily contiguous runs after grid painting; preserve all off-screen overnight slots. */
export function availabilityFromSlots(selected: Set<number>): Availability[] {
  const groups = new Map<string,Availability>()
  for(let day=0;day<7;day++) for(let half=0;half<48;half++) {
    if(!selected.has(day*48+half)) continue
    const start=half*30
    while(half<48 && selected.has(day*48+half)) half++
    const end=half===48?0:half*30, overnight=half===48, key=`${start}:${end}:${overnight}`
    const existing=groups.get(key)
    if(existing) existing.days.push(day)
    else groups.set(key,{id:`grid-${day}-${start}`,days:[day],start,end,overnight})
  }
  return [...groups.values()]
}

import type { Availability } from '../state'

export const days = ['월', '화', '수', '목', '금', '토', '일']
export const dayNumbers = [1, 2, 3, 4, 5, 6, 0]
export const dayName = (day: number) => days[(day + 6) % 7]
export const minutes = (value: string) => Number(value.split(':')[0]) * 60 + Number(value.split(':')[1])
export const time = (value: number) => `${String(Math.floor(value / 60)).padStart(2, '0')}:${String(value % 60).padStart(2, '0')}`

/** Split overnight periods at midnight, then merge overlapping or touching periods. */
export function normalizeAvailability(values: Availability[]): Availability[] {
  const slots = Array.from({ length: 7 }, () => Array<boolean>(48).fill(false))
  values.forEach(value => {
    const start = minutes(value.start), rawEnd = minutes(value.end)
    const end = rawEnd <= start ? rawEnd + 1440 : rawEnd
    for (let minute = start; minute < end; minute += 30) {
      const day = (value.day + Math.floor(minute / 1440)) % 7
      slots[day][Math.floor((minute % 1440) / 30)] = true
    }
  })
  return dayNumbers.flatMap(day => {
    const selected = slots[day]
    const result: Availability[] = []
    let start = -1
    for (let slot = 0; slot <= 48; slot++) {
      if (selected[slot] && start < 0) start = slot
      if (!selected[slot] && start >= 0) {
        result.push({ id: `time-${day}-${start}-${slot}`, day, start: time(start * 30), end: time(slot * 30) })
        start = -1
      }
    }
    return result
  })
}

export function weeklyHours(values: Availability[]) {
  return normalizeAvailability(values).reduce((sum, value) => sum + (minutes(value.end) - minutes(value.start)) / 60, 0)
}

export function groupedTimes(values: Availability[]) {
  const groups = new Map<string, number[]>()
  normalizeAvailability(values).forEach(value => {
    const key = `${value.start}–${value.end}`
    groups.set(key, [...(groups.get(key) || []), value.day])
  })
  return [...groups].map(([range, dayValues]) => `${dayValues.map(dayName).join(' · ')}  ${range}`)
}

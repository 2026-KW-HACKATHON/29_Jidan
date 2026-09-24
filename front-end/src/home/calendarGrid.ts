export const isoDate = (year: number, month: number, day: number) => `${year}-${String(month + 1).padStart(2, '0')}-${String(day).padStart(2, '0')}`
export const isoMonth = (year: number, month: number) => `${year}-${String(month + 1).padStart(2, '0')}`

export function eventsInView<T extends { date: string }>(events: readonly T[], month: string, selectedDate: string | null): T[] {
  return events.filter(event => event.date.startsWith(`${month}-`) && (!selectedDate || event.date === selectedDate))
    .sort((a, b) => a.date.localeCompare(b.date))
}

export function shortDate(date: string): string {
  const match = /^\d{4}-(\d{2})-(\d{2})$/.exec(date)
  return match ? `${Number(match[1])}.${Number(match[2])}` : date
}

export function monthCells(year: number, month: number): (number | null)[] {
  const firstWeekday = new Date(year, month, 1).getDay()
  const lastDay = new Date(year, month + 1, 0).getDate()
  const rows = Math.ceil((firstWeekday + lastDay) / 7)
  return Array.from({ length: rows * 7 }, (_, index) => {
    const day = index - firstWeekday + 1
    return day < 1 || day > lastDay ? null : day
  })
}

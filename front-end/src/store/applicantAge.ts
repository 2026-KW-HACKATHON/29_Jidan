import { today, validDate } from '../registration/worker/model'

/** Calendar dates avoid timezone shifts; age advances on the birthday. */
export function applicantAge(birth: string, current = today()): number | undefined {
  if (!validDate(current, current) || !validDate(birth, current)) return undefined
  return Number(current.slice(0, 4)) - Number(birth.slice(0, 4)) - (current.slice(5) < birth.slice(5) ? 1 : 0)
}

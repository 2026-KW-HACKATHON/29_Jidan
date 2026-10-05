import { expect, it } from 'vitest'
import { applicantAge } from './applicantAge'

it('생일 전날·당일·다음 날과 출생 당일의 만 나이를 계산한다', () => {
  expect(applicantAge('2007-10-01', '2026-09-30')).toBe(18)
  expect(applicantAge('2007-10-01', '2026-10-01')).toBe(19)
  expect(applicantAge('2007-10-01', '2026-10-02')).toBe(19)
  expect(applicantAge('2026-10-01', '2026-10-01')).toBe(0)
})
it('윤년 생일은 평년 3월 1일부터 다음 나이로 표시한다', () => {
  expect(applicantAge('2000-02-29', '2026-02-28')).toBe(25)
  expect(applicantAge('2000-02-29', '2026-03-01')).toBe(26)
  expect(applicantAge('2000-02-29', '2024-02-29')).toBe(24)
})
it('빈 값·존재하지 않는 날짜·미래 날짜·잘못된 기준 날짜는 나이를 표시하지 않는다', () => {
  for (const birth of ['', '2007-02-29', '2007-13-01', '0000-01-01', '2026-10-02', '2007-1-1']) expect(applicantAge(birth, '2026-10-01')).toBeUndefined()
  expect(applicantAge('2007-10-01', '2026-02-30')).toBeUndefined()
})

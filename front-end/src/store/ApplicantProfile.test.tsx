import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, expect, it, vi } from 'vitest'
import { ApplicantProfile, type ApplicantProfileData } from './ApplicantProfile'
afterEach(cleanup)
const data: ApplicantProfileData = { name: '박서연', birth: '2007-10-01', experienceSummary: '카페 경력 1년 2개월', application: { title: '주말 오픈 대타', schedule: '9월 26일 · 09:00 – 14:00' }, introduction: '첫 문장.\n둘째 문장.', experiences: [{ id: 'cafe', title: '카페 · 1년 2개월', period: '2024. 03 – 2025. 04', duties: '음료 제조 · 주문 접수 · 매장 정리' }] }
it('신원·만 나이·지원 공고·소개서·경력과 뒤로 가기를 표시한다', () => {
  const onBack = vi.fn()
  render(<ApplicantProfile data={data} currentDate="2026-10-01" onBack={onBack} />)
  expect(screen.getByText('(만 19세)')).toBeVisible()
  expect(screen.getByRole('heading', { name: '박서연' })).toBeVisible()
  expect(screen.getByText(data.application.title)).toBeVisible()
  expect(screen.getByText(data.application.schedule)).toBeVisible()
  expect(screen.getByRole('region', { name: '지원자 소개서' })).toHaveTextContent('첫 문장.')
  expect(screen.getByText(data.experiences[0].duties)).toBeVisible()
  fireEvent.click(screen.getByRole('button', { name: '뒤로 가기' }))
  expect(onBack).toHaveBeenCalledOnce()
})
it('잘못된 생년월일이면 나이를 꾸며 표시하지 않고 빈 경력도 허용한다', () => {
  render(<ApplicantProfile data={{ ...data, birth: '', experiences: [] }} currentDate="2026-10-01" onBack={() => {}} />)
  expect(screen.queryByText(/만 .*세/)).not.toBeInTheDocument()
  expect(screen.queryByText(data.experiences[0].duties)).not.toBeInTheDocument()
  expect(screen.getByText(/경력은 근무자가 직접 등록한 정보/)).toBeVisible()
})

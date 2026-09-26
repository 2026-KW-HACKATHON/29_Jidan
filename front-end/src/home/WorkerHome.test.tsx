import { fireEvent, render, screen } from '@testing-library/react'
import { expect, it } from 'vitest'
import { WorkerHome } from './WorkerHome'

it('shows the authenticated member and zero/empty states without preview data', () => {
  render(<WorkerHome displayName="이하늘" initialDate={new Date(2026, 8, 25)} />)
  expect(screen.getByRole('heading', { name: '안녕하세요, 이하늘님' })).toBeInTheDocument()
  expect(screen.getByText('추천 공고가 없어요.')).toBeInTheDocument()
  expect(screen.getByText('예정된 근무가 없어요.')).toBeInTheDocument()
  expect(screen.queryByText('컴포즈커피 광운대80주년기념관점')).not.toBeInTheDocument()
  expect(screen.getByRole('navigation', { name: '주 메뉴' })).toHaveTextContent('공고 찾기')
  expect(screen.getByRole('navigation', { name: '주 메뉴' })).toHaveTextContent('프로필')
})

it('renders activity, recommendations, and shift types from supplied data', () => {
  render(<WorkerHome displayName="김지수" initialDate={new Date(2025, 6, 19)} data={{ activity: { applications: 3, favoriteStores: 5, regularStores: 2 }, recommendations: [{ id: '1', title: '광운 카페', status: '신청 가능', schedule: '7월 19일' }], shifts: [{ id: '1', date: '2025-07-22', storeName: '광운 매장', kind: 'substitute' }], marks: [{ date: '2025-07-22', kind: 'substitute' }] }} />)
  expect(screen.getByText('광운 카페')).toBeInTheDocument()
  expect(screen.getByText('3건')).toBeInTheDocument()
  expect(screen.getByText('대타 근무')).toBeInTheDocument()
  expect(screen.getByRole('button', { name: '2025년 7월 22일' })).toHaveClass('home-calendar-substitute')
})
it('지원자가 없는 추천 공고는 중립 배지로 표시한다', () => {
  render(<WorkerHome displayName="김지수" data={{ recommendations: [
    { id: 'none', title: '국수천왕 광운대본점', status: '지원자 없음', schedule: '7월 22일' },
    { id: 'three', title: '컴포즈커피', status: '지원자 3명', schedule: '7월 19일' },
  ] }} />)
  expect(screen.getByText('지원자 없음')).toHaveClass('worker-home-no-applicants')
  expect(screen.getByText('지원자 3명')).not.toHaveClass('worker-home-no-applicants')
})

it('shows shifts for the visible month and narrows them to a touched date', () => {
  render(<WorkerHome displayName="김지수" initialDate={new Date(2025, 6, 19)} data={{ shifts: [
    { id: 'july-19', date: '2025-07-19', storeName: '오늘의 매장', kind: 'regular' },
    { id: 'july-22', date: '2025-07-22', storeName: '대타 매장', kind: 'substitute' },
    { id: 'july-26', date: '2025-07-26', storeName: '다음 매장', kind: 'regular' },
    { id: 'july-30', date: '2025-07-30', storeName: '네 번째 매장', kind: 'regular' },
    { id: 'august', date: '2025-08-02', storeName: '다음 달 매장', kind: 'regular' },
  ], marks: [{ date: '2025-07-22', kind: 'substitute' }] }} />)
  expect(screen.getByRole('button', { name: '2025년 7월 19일' })).toHaveAttribute('aria-pressed', 'true')
  expect(screen.getByText('오늘의 매장')).toBeInTheDocument()
  expect(screen.getByText('대타 매장')).toBeInTheDocument()
  expect(screen.getByText('다음 매장')).toBeInTheDocument()
  expect(screen.queryByText('네 번째 매장')).not.toBeInTheDocument()
  expect(screen.queryByText('다음 달 매장')).not.toBeInTheDocument()

  fireEvent.click(screen.getByRole('button', { name: '2025년 7월 22일' }))
  expect(screen.queryByText('오늘의 매장')).not.toBeInTheDocument()
  expect(screen.getByText('대타 매장')).toBeInTheDocument()
  expect(screen.getByRole('button', { name: '2025년 7월 22일' })).toHaveAttribute('data-user-selected', 'true')

  fireEvent.click(screen.getByRole('button', { name: '2025년 7월 23일' }))
  expect(screen.getByText('선택한 날짜에 근무가 없어요.')).toBeInTheDocument()

  fireEvent.click(screen.getByRole('button', { name: '2025년 7월 30일' }))
  expect(screen.getByText('네 번째 매장')).toBeInTheDocument()
  fireEvent.click(screen.getByRole('button', { name: '2025년 7월 19일' }))
  expect(screen.getByText('오늘의 매장')).toBeInTheDocument()
  expect(screen.getByText('대타 매장')).toBeInTheDocument()
  expect(screen.getByText('다음 매장')).toBeInTheDocument()
  expect(screen.queryByText('네 번째 매장')).not.toBeInTheDocument()

  fireEvent.click(screen.getByRole('button', { name: '다음 달' }))
  expect(screen.getByText('다음 달 매장')).toBeInTheDocument()
  expect(screen.queryByText('대타 매장')).not.toBeInTheDocument()
  fireEvent.click(screen.getByRole('button', { name: '이전 달' }))
  expect(screen.getByText('오늘의 매장')).toBeInTheDocument()
  expect(screen.queryByText('네 번째 매장')).not.toBeInTheDocument()
})

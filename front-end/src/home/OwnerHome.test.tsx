import { fireEvent, render, screen } from '@testing-library/react'
import { expect, it, vi } from 'vitest'
import { OwnerHome } from './OwnerHome'

it('uses session identity and shows honest empty states without API data', () => {
  render(<OwnerHome displayName="이하늘" initialDate={new Date(2026, 8, 25)} />)
  expect(screen.getByRole('heading', { name: '안녕하세요, 이하늘 점주님' })).toBeInTheDocument()
  expect(screen.getByText('매장 정보를 불러오지 못했어요.')).toBeInTheDocument()
  expect(screen.getByText('모집 중인 공고가 없어요.')).toBeInTheDocument()
  expect(screen.getByText('이 달에 일정이 없어요.')).toBeInTheDocument()
  expect(screen.queryByText('명랑핫도그 광운대점')).not.toBeInTheDocument()
  expect(screen.getByRole('navigation', { name: '주 메뉴' })).toBeInTheDocument()
})

it('updates the list for the visible month and narrows it to a touched date', () => {
  render(<OwnerHome displayName="김민수" initialDate={new Date(2025, 6, 19)} data={{ events: [
    { id: 'july-19', date: '2025-07-19', descriptions: ['오늘 일정'] },
    { id: 'july-22', date: '2025-07-22', descriptions: ['대타 일정'], substitute: true },
    { id: 'july-26', date: '2025-07-26', descriptions: ['다음 일정'] },
    { id: 'july-30', date: '2025-07-30', descriptions: ['네 번째 일정'] },
    { id: 'august-1', date: '2025-08-01', descriptions: ['8월 일정'] },
  ] }} />)
  expect(screen.getByText('오늘 일정')).toBeInTheDocument()
  expect(screen.getByText('대타 일정')).toBeInTheDocument()
  expect(screen.getByText('다음 일정')).toBeInTheDocument()
  expect(screen.queryByText('네 번째 일정')).not.toBeInTheDocument()
  expect(screen.queryByText('8월 일정')).not.toBeInTheDocument()
  fireEvent.click(screen.getByRole('button', { name: '2025년 7월 22일' }))
  expect(screen.queryByText('오늘 일정')).not.toBeInTheDocument()
  expect(screen.getByText('대타 일정')).toBeInTheDocument()
  fireEvent.click(screen.getByRole('button', { name: '2025년 7월 23일' }))
  expect(screen.getByText('선택한 날짜에 일정이 없어요.')).toBeInTheDocument()
  fireEvent.click(screen.getByRole('button', { name: '2025년 7월 30일' }))
  expect(screen.getByText('네 번째 일정')).toBeInTheDocument()
  fireEvent.click(screen.getByRole('button', { name: '2025년 7월 19일' }))
  expect(screen.getByText('오늘 일정')).toBeInTheDocument()
  expect(screen.getByText('대타 일정')).toBeInTheDocument()
  expect(screen.getByText('다음 일정')).toBeInTheDocument()
  expect(screen.queryByText('네 번째 일정')).not.toBeInTheDocument()
  fireEvent.click(screen.getByRole('button', { name: '다음 달' }))
  expect(screen.getByText('8월 일정')).toBeInTheDocument()
  expect(screen.queryByText('대타 일정')).not.toBeInTheDocument()
  fireEvent.click(screen.getByRole('button', { name: '이전 달' }))
  expect(screen.getByText('오늘 일정')).toBeInTheDocument()
  expect(screen.getByText('대타 일정')).toBeInTheDocument()
  expect(screen.queryByText('네 번째 일정')).not.toBeInTheDocument()
})

it('renders an operating store, job applicants, and calendar marks from supplied data', () => {
  render(<OwnerHome displayName="김민수" initialDate={new Date(2025, 6, 19)} data={{ store: { name: '광운 매장', status: 'operating' }, jobs: [{ id: 'job', title: '주말 대타', schedule: '7월 19일', applicants: 0 }], marks: [{ date: '2025-07-22' }] }} />)
  expect(screen.getByText('광운 매장')).toBeInTheDocument()
  expect(screen.getByText('운영 중')).toBeInTheDocument()
  expect(screen.getByText('지원자 없음')).toBeInTheDocument()
  expect(screen.getByRole('button', { name: '2025년 7월 22일' })).toHaveClass('home-calendar-regular')
})
it('모집 중 공고 전체 건수를 표시하고 처음 세 건만 미리 보여준다', () => {
  const jobs = Array.from({ length: 4 }, (_, index) => ({ id: String(index), title: `${index + 1}번째 공고`, schedule: '7월 19일', applicants: index }))
  render(<OwnerHome displayName="김민수" data={{ jobs }} initialDate={new Date(2025, 6, 19)} />)
  expect(screen.getByText('4건')).toBeInTheDocument()
  expect(screen.getByText('3번째 공고')).toBeInTheDocument()
  expect(screen.queryByText('4번째 공고')).not.toBeInTheDocument()
  expect(screen.getByRole('button', { name: '+ 공고 모두 보기' })).toBeInTheDocument()
})

it('공고 관리 메뉴는 연결된 이동 콜백을 실행한다',()=>{const jobs=vi.fn();render(<OwnerHome displayName="점주" onJobs={jobs}/>);fireEvent.click(screen.getByRole('button',{name:'공고 관리'}));expect(jobs).toHaveBeenCalledTimes(1)})

it('키보드로 접근 가능한 공고 카드에서 선택한 공고 객체를 전달한다',()=>{const select=vi.fn(),job={id:'night',title:'평일 마감 대타',schedule:'7월 22일',applicants:1};render(<OwnerHome displayName="점주" data={{jobs:[job]}} onSelectJob={select}/>);fireEvent.click(screen.getByRole('button',{name:/평일 마감 대타/}));expect(select).toHaveBeenCalledExactlyOnceWith(job)})
it('홈에 일부 공고만 도착해도 서버 전체 집계와 모두 보기 버튼을 표시한다',()=>{render(<OwnerHome displayName="점주" data={{recruitingCount:8,jobs:[{id:'job',title:'오픈',schedule:'내일',applicants:0}]}}/>);expect(screen.getByText('8건')).toBeInTheDocument();expect(screen.getByRole('button',{name:'+ 공고 모두 보기'})).toBeInTheDocument()})

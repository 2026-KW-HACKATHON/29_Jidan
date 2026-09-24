import { fireEvent, render, screen } from '@testing-library/react'
import { expect, it } from 'vitest'
import { HomeCalendar } from './HomeCalendar'
import { monthCells } from './calendarGrid'

it('lays out months across year and leap-day boundaries', () => {
  expect(monthCells(2024, 1).filter(Boolean)).toHaveLength(29)
  expect(monthCells(2025, 6).slice(0, 3)).toEqual([null, null, 1])
  expect(monthCells(2025, 11).filter(Boolean)).toHaveLength(31)
})

it('moves across December and January and keeps event marks scoped to their date', () => {
  render(<HomeCalendar label="매장 캘린더" initialDate={new Date(2025, 11, 31)} marks={[{ date: '2026-01-01', kind: 'substitute' }]} />)
  expect(screen.getByText('2025년 12월')).toBeInTheDocument()
  fireEvent.click(screen.getByRole('button', { name: '다음 달' }))
  expect(screen.getByText('2026년 1월')).toBeInTheDocument()
  expect(screen.getByRole('button', { name: '2026년 1월 1일' })).toHaveClass('home-calendar-substitute')
  fireEvent.click(screen.getByRole('button', { name: '이전 달' }))
  expect(screen.getByRole('button', { name: '2025년 12월 31일' })).toHaveAttribute('aria-pressed', 'true')
})

it('shows today in blue by default, then highlights the touched date and restores today on return', () => {
  render(<HomeCalendar label="매장 캘린더" initialDate={new Date(2025, 6, 19)} marks={[{ date: '2025-07-22', kind: 'substitute' }]} />)
  const today = screen.getByRole('button', { name: '2025년 7월 19일' })
  const substitute = screen.getByRole('button', { name: '2025년 7월 22일' })
  expect(today).toHaveAttribute('aria-current', 'date')
  expect(today).toHaveAttribute('aria-pressed', 'true')
  expect(substitute).toHaveClass('home-calendar-substitute')
  expect(substitute).not.toHaveAttribute('data-user-selected')
  fireEvent.click(substitute)
  expect(today).toHaveAttribute('aria-pressed', 'false')
  expect(substitute).toHaveAttribute('aria-pressed', 'true')
  expect(substitute).toHaveAttribute('data-user-selected', 'true')
  fireEvent.click(screen.getByRole('button', { name: '다음 달' }))
  expect(screen.getByRole('button', { name: '2025년 8월 1일' })).toHaveAttribute('aria-pressed', 'false')
  fireEvent.click(screen.getByRole('button', { name: '이전 달' }))
  expect(screen.getByRole('button', { name: '2025년 7월 19일' })).toHaveAttribute('aria-pressed', 'true')
})

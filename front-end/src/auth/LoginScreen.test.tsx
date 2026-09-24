import { fireEvent, render, screen } from '@testing-library/react'
import { expect, it, vi } from 'vitest'
import { LoginScreen } from './LoginScreen'

it('exposes one SSO entry and blocks activation while authentication is pending', () => {
  const start = vi.fn()
  const { rerender } = render(<LoginScreen onStart={start} />)
  const button = screen.getByRole('button', { name: 'SSO 계정으로 시작하기' })
  expect(screen.getAllByRole('button')).toHaveLength(1)
  expect(button).toHaveAccessibleDescription('기존 계정으로 로그인하거나 새로 가입할 수 있어요.')
  fireEvent.click(button)
  expect(start).toHaveBeenCalledOnce()
  rerender(<LoginScreen onStart={start} busy />)
  fireEvent.click(button)
  expect(start).toHaveBeenCalledOnce()
  expect(button).toBeDisabled()
})

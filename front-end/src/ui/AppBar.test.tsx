import { fireEvent, render, screen } from '@testing-library/react'
import { expect, it, vi } from 'vitest'
import { AppBar } from './AppBar'

it('announces the current screen and invokes the supplied back navigation', () => {
  const back = vi.fn()
  render(<AppBar title="매장 관리" onBack={back} />)
  expect(screen.getByRole('heading', { level: 1 })).toHaveTextContent('매장 관리')
  fireEvent.click(screen.getByRole('button', { name: '뒤로 가기' }))
  expect(back).toHaveBeenCalledOnce()
})

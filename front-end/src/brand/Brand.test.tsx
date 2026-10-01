import { cleanup, render, screen } from '@testing-library/react'
import { afterEach, expect, it } from 'vitest'
import { Brand } from './Brand'
afterEach(cleanup)
it.each(['login','appbar'] as const)('%s는 브랜드 이름을 한 번만 제공한다', variant => {
  const { container } = render(<Brand variant={variant} />)
  expect(screen.getAllByRole('img',{name:'지단'})).toHaveLength(1)
  expect(container.querySelectorAll('img')).toHaveLength(variant==='login'?2:1)
})

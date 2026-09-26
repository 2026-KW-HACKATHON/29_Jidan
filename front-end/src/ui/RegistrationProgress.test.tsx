import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { RegistrationProgress } from './RegistrationProgress'

describe.each(['owner', 'member'] as const)('RegistrationProgress %s', role => {
  it.each([1, 2, 3] as const)('marks only step %d as current', step => {
    render(<RegistrationProgress role={role} step={step} />)
    const steps = screen.getAllByRole('listitem')
    expect(steps.filter(item => item.getAttribute('aria-current') === 'step')).toEqual([steps[step - 1]])
    expect(steps[1]).toHaveTextContent(role === 'owner' ? '매장 정보' : '근무 정보')
  })
})

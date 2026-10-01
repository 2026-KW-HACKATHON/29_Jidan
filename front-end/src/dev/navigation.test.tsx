import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, expect, it, vi } from 'vitest'
import { handlePreviewLink } from './navigation'

afterEach(() => { cleanup(); vi.restoreAllMocks() })
it.each([
  { modifiers: { ctrlKey: true } },
  { modifiers: { metaKey: true } },
  { modifiers: { shiftKey: true } },
  { modifiers: { altKey: true } },
  { modifiers: { button: 1 } },
  { target: '_blank' },
  { download: true },
  { href: 'https://example.com/__preview' },
  { href: '/login' },
  { href: '/__preview#section' },
  { prevented: true },
])('기본 브라우저 링크 동작을 보존한다: %j', options => {
  const push = vi.spyOn(history, 'pushState')
  let intercepted = false
  render(<div onClick={event => {
    if ('prevented' in options) event.preventDefault()
    const before = event.defaultPrevented
    handlePreviewLink(event)
    intercepted = !before && event.defaultPrevented
    event.preventDefault() // Do not execute jsdom's unimplemented document navigation.
  }}><a href={'href' in options ? options.href : '/__preview'} target={'target' in options ? options.target : undefined} download={'download' in options ? options.download : undefined}>화면 링크</a></div>)
  fireEvent.click(screen.getByRole('link'), 'modifiers' in options ? options.modifiers : {})
  expect(intercepted).toBe(false)
  expect(push).not.toHaveBeenCalled()
})

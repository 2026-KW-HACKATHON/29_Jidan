import { expect, it } from 'vitest'
import { canonicalAuthPath, isPreviewPath } from './routes'
it.each([['/__auth/signup', '/signup'], ['/__auth/session', '/home']])('callback %s는 검수 화면 대신 제품 %s로 연결한다', (path, expected) => {
  expect(canonicalAuthPath(path)).toBe(expected); expect(isPreviewPath(path)).toBe(false)
})
it('검수 화면과 알 수 없는 경로를 보존한다', () => {
  expect(isPreviewPath('/__preview/signup')).toBe(true); expect(isPreviewPath('/__auth/worker')).toBe(true)
  for (const path of ['/home','/unknown','/toString']) { expect(canonicalAuthPath(path)).toBe(path); expect(isPreviewPath(path)).toBe(false) }
})

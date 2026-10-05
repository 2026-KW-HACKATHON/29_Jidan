import { beforeEach, expect, it } from 'vitest'
import { clearDraft, restoreDraft, saveDraft } from './draft'
import { emptyDraft } from './model'
const state = { draft: { ...emptyDraft, name: '김민수' }, step: 2 as const, requestKey: 'key' }
beforeEach(() => sessionStorage.clear())
it('동일 가입 세션의 새로고침에서 입력과 중복 방지 키를 복원한다', () => {
  saveDraft('scope', state, sessionStorage, 100)
  expect(restoreDraft('scope', sessionStorage, 200)).toEqual(state)
  clearDraft(); expect(restoreDraft('scope')).toBeNull()
})
it('다른 계정 또는 만료된 입력을 폐기한다', () => {
  saveDraft('first', state); expect(restoreDraft('second')).toBeNull()
  saveDraft('scope', state, sessionStorage, 100)
  expect(restoreDraft('scope', sessionStorage, 1800100)).toBeNull()
})
it('손상·미래 시간·알 수 없는 업종을 거부한다', () => {
  sessionStorage.setItem('jidan.owner-draft.v1', '{'); expect(restoreDraft('scope')).toBeNull()
  saveDraft('scope', state, sessionStorage, 100); expect(restoreDraft('scope', sessionStorage, 50)).toBeNull()
  sessionStorage.setItem('jidan.owner-draft.v1', JSON.stringify({ ...state, scope: 'scope', savedAt: Date.now(), draft: { ...emptyDraft, industry: 'invalid' } }))
  expect(restoreDraft('scope')).toBeNull()
})
it('스토리지 사용 불가에서도 안전하게 실패한다', () => {
  const storage = { getItem() { throw Error() }, setItem() { throw Error() }, removeItem() { throw Error() } } as unknown as Storage
  expect(restoreDraft('scope', storage)).toBeNull()
  expect(saveDraft('scope', state, storage)).toBe(false)
  expect(() => clearDraft(storage)).not.toThrow()
})

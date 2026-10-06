import { beforeEach, expect, it, vi } from 'vitest'
import { clearWorkerDraft, restoreWorkerDraft, saveWorkerDraft } from './draft'
import { emptyWorker } from './model'
beforeEach(()=>sessionStorage.clear())
it('같은 인증 이메일에서만 초안과 재시도 key를 복원하고 완료 후 삭제한다',()=>{
 saveWorkerDraft('a@b.com',emptyWorker,'key');expect(restoreWorkerDraft('a@b.com')).toEqual({draft:emptyWorker,requestKey:'key'});expect(restoreWorkerDraft('other@b.com')).toBeNull();clearWorkerDraft();expect(restoreWorkerDraft('a@b.com')).toBeNull()
})
it('손상·만료·미래 초안과 잘못된 nested 필드를 거부한다',()=>{
 for(const value of ['bad',JSON.stringify({scope:'a',draft:{...emptyWorker,careers:[{}]},requestKey:'key',savedAt:Date.now()}),JSON.stringify({scope:'a',draft:{...emptyWorker,availability:[{}]},requestKey:'key',savedAt:Date.now()})]){sessionStorage.setItem('jidan.worker-draft.v1',value);expect(restoreWorkerDraft('a')).toBeNull()}
 vi.useFakeTimers();saveWorkerDraft('a',emptyWorker,'key');vi.advanceTimersByTime(1800000);expect(restoreWorkerDraft('a')).toBeNull();vi.useRealTimers()
})

import {expect,it} from 'vitest'
import {workerFromApi} from './api'
import type {StoreWorkerAccess,StoreAccessGrant} from '../api/types.generated'
const regular:StoreAccessGrant={id:'r',type:'REGULAR',dutyLabel:null,startedAt:'2026-10-01T00:00:00Z',validUntil:null,revokedAt:null,status:'ACTIVE',permissions:['READ_MANUALS','READ_CHECKLISTS','USE_AI_QA']}
const base:StoreWorkerAccess={workerId:'w',storeId:'s',name:'회원',accessStatus:'ACTIVE',accessGrants:[regular],permissions:['READ_MANUALS','READ_CHECKLISTS','USE_AI_QA'],asOf:'2026-10-08T00:00:00Z'}
it('무기한 정기 권한과 임시 권한이 공존해도 전체 접근 만료로 표시하지 않는다',()=>{const temporary:StoreAccessGrant={...regular,id:'t',type:'TEMPORARY',status:'EXPIRING',validUntil:'2026-10-09T00:00:00Z'};const view=workerFromApi({...base,accessGrants:[temporary,regular]});expect(view.expiry).toBe('별도 종료 전까지');expect(view.type).toContain('정기 근무');expect(view.type).toContain('대타 근무')})
it('권한이 없는 종료 상태는 이전 권한을 복구해서 표시하지 않는다',()=>{const view=workerFromApi({...base,accessStatus:'ENDED',permissions:[],accessGrants:[]});expect(view.status).toBe('ended');expect(view.permissions).toEqual([]);expect(view.expiry).toBe('접근 종료')})

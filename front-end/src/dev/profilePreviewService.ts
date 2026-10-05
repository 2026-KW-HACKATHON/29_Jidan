import { emptyWorker } from '../registration/worker/model'
import type { ProfileService, WorkerProfileData } from '../profile/service'
export const sampleProfile:WorkerProfileData={email:'member@example.com',draft:{...emptyWorker,name:'김지수',phone:'010-1234-5678',birth:'2001-03-14',gender:'여성',experience:'신입',availability:[{id:'sample',days:[0,2,4],start:540,end:840,overnight:false}]}}
/** In-memory preview only. Reload resets fixtures; no account/session storage. */
export function createProfilePreviewService(initial=sampleProfile,failFirst=false):ProfileService {
 let value=structuredClone(initial),failed=false
 return {
  read:async signal=>{signal.throwIfAborted();return structuredClone(value)},
  save:async (next,signal)=>{signal.throwIfAborted();if(failFirst&&!failed){failed=true;throw Error('MOCK_FAILURE')}value=structuredClone(next)},
 }
}

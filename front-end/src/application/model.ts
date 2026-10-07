import type { Job } from '../jobs/model'
export type Application = { id:string; job:Job; introduction:string; statusLabel?:string; statusMessage?:string; canWithdraw?:boolean }
/** Injectable UI effects, not an HTTP contract. */
export type ApplicationService = {
  submit: (job:Job,introduction:string,signal:AbortSignal)=>Promise<Application>
  withdraw: (id:string,signal:AbortSignal)=>Promise<void>
}
export const applicationService: ApplicationService = {
  submit:async()=>{throw new Error('APPLICATION_NOT_CONFIGURED')},
  withdraw:async()=>{throw new Error('APPLICATION_NOT_CONFIGURED')},
}
const segmenter=new Intl.Segmenter('ko',{granularity:'grapheme'})
export function introductionCharacters(value:string) { return [...segmenter.segment(value)].map(part=>part.segment) }
export function limitIntroduction(value:string) { return introductionCharacters(value).slice(0,500).join('') }
export function validIntroduction(value:string) { return !!value.replace(/[\s\p{Cf}]/gu,'') && introductionCharacters(value).length<=500 }

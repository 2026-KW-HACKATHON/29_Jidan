import { saveWorkerDraft, restoreWorkerDraft, clearWorkerDraft } from './draft'
import { useEffect, useRef, useState } from 'react'
import { Button } from '../../ui/Button'
import { Modal } from '../../ui/Modal'
import { WorkerFrame } from './WorkerFrame'
import { WorkerBasic } from './WorkerBasic'
import { CareerEditor, WorkerExperience } from './WorkerExperience'
import { AvailabilityEditor, WorkerAvailability } from './WorkerAvailability'
import { WorkerReview, WorkerCompleteContent } from './WorkerReview'
import { emptyAvailability, emptyCareer, emptyWorker, normalizedWorker, validateWorker, type Errors, type WorkerDraft } from './model'
import { isWorkerReceipt, WorkerFailure, workerService, type WorkerService } from './service'
import { WorkerProfile } from '../../profile/WorkerProfile'
import type { ProfileService } from '../../profile/service'
type Page = 1|2|3|'review'|'complete'|'profile'
type Editor = {kind:'career'|'time';index:number}|null
export function WorkerRegistration({ service=workerService, onBack, onExpired, onHome, onRegistered, initialDraft=emptyWorker, initialPage=1, profileService }: { profileService?:ProfileService;service?:WorkerService;onBack:()=>void;onExpired:()=>void;onHome:()=>void;onRegistered?:()=>void;initialDraft?:WorkerDraft;initialPage?:Page }) {
  const [draft,setDraft]=useState(initialDraft),[page,setPage]=useState<Page>(initialPage),[editor,setEditor]=useState<Editor>(null),[editing,setEditing]=useState(false)
  const [email,setEmail]=useState(''),[ready,setReady]=useState(false),[busy,setBusy]=useState(false),[errors,setErrors]=useState<Errors>({}),[message,setMessage]=useState(''),[expired,setExpired]=useState(false),[loadVersion,setLoadVersion]=useState(0),[scope,setScope]=useState('')
  const editSnapshot=useRef<{draft:WorkerDraft;key:string;dirty:boolean}|null>(null)
  const lock=useRef(false), request=useRef<AbortController|null>(null), key=useRef<string>(crypto.randomUUID()), alive=useRef(true),dirty=useRef(false)
  useEffect(()=>{
    alive.current=true;const controller=new AbortController();request.current=controller
    const timer=setTimeout(()=>{controller.abort();if(alive.current)setMessage('가입 정보를 불러오지 못했어요. 다시 시도해 주세요.')},10000)
    void service.identity(controller.signal).then(identity=>{if(controller.signal.aborted||!alive.current)return;if(!identity.email)throw new WorkerFailure('unavailable');setEmail(identity.email);if(identity.draftScope){setScope(identity.draftScope);const saved=restoreWorkerDraft(identity.draftScope);if(saved){setDraft(saved.draft);key.current=saved.requestKey}}setReady(true)}).catch(e=>{if(controller.signal.aborted||!alive.current)return;setExpired(e instanceof WorkerFailure&&e.code==='expired');setMessage(e instanceof WorkerFailure&&e.message!==e.code?e.message:'가입 정보를 불러오지 못했어요. 다시 로그인하거나 잠시 후 시도해 주세요.')}).finally(()=>clearTimeout(timer))
    return()=>{alive.current=false;clearTimeout(timer);request.current?.abort()}
  },[service,loadVersion])
  useEffect(()=>{if(scope&&ready&&page!=='complete'&&page!=='profile')saveWorkerDraft(scope,draft,key.current)},[scope,ready,draft,page])
  useEffect(()=>{const warn=(e:BeforeUnloadEvent)=>{if(dirty.current&&page!=='complete'&&page!=='profile'){e.preventDefault();e.returnValue=''}};window.addEventListener('beforeunload',warn);return()=>window.removeEventListener('beforeunload',warn)},[page])
  useEffect(()=>{document.querySelector('.worker-signup .ds-mobile-body')?.scrollTo?.(0,0)},[page,editor])
  function change(patch:Partial<WorkerDraft>) {if(lock.current)return;setDraft(d=>({...d,...patch}));key.current=crypto.randomUUID();dirty.current=true;setErrors({})}
  function back() {if(lock.current)return;if(editor){setEditor(null);return}if(page==='complete'){onHome();return}if(page==='profile'){setPage('complete');return}if(editing){if(editSnapshot.current){setDraft(editSnapshot.current.draft);key.current=editSnapshot.current.key;dirty.current=editSnapshot.current.dirty}editSnapshot.current=null;setErrors({});setEditing(false);setPage('review');return}if(page===1)onBack();else setPage(page==='review'?3:page===3?2:1)}
  async function next() {
    if(lock.current||!ready)return
    if(page==='complete'){onHome();return}if(page==='profile'){setPage('complete');return}
    const step=page==='review'?4:page,e=validateWorker(draft,step);setErrors(e)
    if(Object.keys(e).length){if(page==='review'){const first=([1,2,3] as const).find(s=>Object.keys(validateWorker(draft,s)).length);if(first){setEditing(true);setPage(first)}}return}
    if(page!=='review'){if(editing){setEditing(false);setPage('review')}else setPage(page===1?2:page===2?3:'review');return}
    lock.current=true;setBusy(true);const controller=new AbortController();request.current=controller
    const timer=setTimeout(()=>{controller.abort();if(alive.current){lock.current=false;setBusy(false);setMessage('등록 응답이 지연되고 있어요. 같은 내용으로 다시 시도해 주세요.')}},15000)
    try {const receipt=await service.submit(normalizedWorker(draft),key.current,controller.signal);if(!alive.current||controller.signal.aborted)return;if(!isWorkerReceipt(receipt))throw new WorkerFailure('network');dirty.current=false;clearWorkerDraft();setPage('complete');onRegistered?.()}
    catch(e){if(!alive.current||controller.signal.aborted)return;if(e instanceof WorkerFailure&&e.code==='expired'){setExpired(true);setReady(false);setEmail('');setEditor(null);setErrors({});dirty.current=false}if(e instanceof WorkerFailure&&e.code==='validation'){setErrors(e.fields);setEditing(true);setPage(Object.keys(e.fields).some(k=>['name','phone','birth','gender'].includes(k))?1:Object.keys(e.fields).some(k=>['experience','careers'].includes(k))?2:3)}setMessage(e instanceof WorkerFailure&&e.message!==e.code?e.message:e instanceof WorkerFailure&&e.code==='expired'?'가입 세션이 만료됐어요. 다시 로그인해 주세요.':'프로필을 등록하지 못했어요. 입력 내용을 확인하고 다시 시도해 주세요.')}
    finally {clearTimeout(timer);if(alive.current&&!controller.signal.aborted){lock.current=false;setBusy(false)}}
  }
  const modal=<Modal open={!!message} state="error" title="가입을 계속할 수 없어요" description={message} confirmLabel={expired?'다시 로그인':'확인'} cancelLabel="닫기" onClose={()=>setMessage('')} onConfirm={expired?onExpired:undefined}/>
  if(!ready)return <><WorkerFrame title="프로필 등록" heading={expired?'다시 로그인해 주세요':'가입 정보 확인'} description={expired?'가입 세션이 만료됐어요. 다시 로그인한 뒤 진행해 주세요.':'인증된 계정 정보를 확인하고 있어요.'} action={expired?'다시 로그인':'다시 시도'} onBack={onBack} onNext={()=>{if(expired){onExpired();return}setMessage('');setLoadVersion(v=>v+1)}}>{!expired&&<Button intent="secondary" onClick={onExpired}>다시 로그인</Button>}</WorkerFrame>{modal}</>
  if(page==='profile') return <WorkerProfile initialProfile={{draft,email}} service={profileService} onSaved={profile=>setDraft(profile.draft)} onBack={()=>setPage('complete')}/>
  if(editor?.kind==='career') return <CareerEditor key={`career-${editor.index}`} value={draft.careers[editor.index]||emptyCareer} birth={draft.birth} onBack={()=>setEditor(null)} onSave={career=>{change({careers:editor.index<0?[...draft.careers,career]:draft.careers.map((c,i)=>i===editor.index?career:c)});setEditor(null)}}/>
  if(editor?.kind==='time')return <AvailabilityEditor key={`time-${editor.index}`} value={draft.availability[editor.index]||emptyAvailability} others={draft.availability.filter((_,i)=>i!==editor.index)} onBack={()=>setEditor(null)} onSave={a=>{change({availability:editor.index<0?[...draft.availability,a]:draft.availability.map((v,i)=>i===editor.index?a:v)});setEditor(null)}}/>
  const config=page===1?['반가워요! 기본 정보를 알려주세요','매장과 연락할 때 필요한 정보예요.']:page===2?['어떤 일을 해보셨나요?','근무 경력을 알려주세요.']:page===3?['마지막 단계예요!','근무 가능 시간을 선택해 주세요.']:page==='complete'?['프로필 등록이 완료됐어요','이제 내 일정에 맞는 공고를 찾아보세요.']:['프로필을 확인해 주세요','등록 후에도 내 프로필에서 수정할 수 있어요.']
  return <><WorkerFrame title={typeof page==='number'?'프로필 등록':page==='review'?'입력 내용 확인':page==='complete'?'등록 완료':'내 프로필'} heading={config[0]} description={config[1]} step={typeof page==='number'?page:undefined} complete={page==='complete'} action={page==='complete'?'지단 시작하기':page==='review'?'프로필 등록 완료':editing?'수정 완료':page===3?'입력 내용 확인':'다음'} busy={busy} onBack={back} onNext={()=>void next()}>
    <fieldset disabled={busy} className="worker-content worker-fields-boundary">
    {page===1&&<WorkerBasic draft={draft} email={email} errors={errors} change={change} blur={field=>setErrors(e=>({...e,[field]:validateWorker(draft,1)[field]||''}))}/>}
    {page===2&&<WorkerExperience draft={draft} errors={errors} change={change} edit={index=>setEditor({kind:'career',index})}/>}
    {page===3&&<WorkerAvailability values={draft.availability} error={errors.availability} change={availability=>change({availability})} edit={index=>setEditor({kind:'time',index})}/>}
    {page==='review'&&<WorkerReview draft={draft} email={email} edit={page==='review'?step=>{editSnapshot.current={draft:structuredClone(draft),key:key.current,dirty:dirty.current};setEditing(true);setErrors({});setPage(step)}:undefined}/>}
    {page==='complete'&&<WorkerCompleteContent draft={draft} onProfile={()=>setPage('profile')}/>}
    </fieldset>
  </WorkerFrame>{modal}</>
}

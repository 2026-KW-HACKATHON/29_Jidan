import {useCallback,useEffect,useRef,useState} from 'react'
import {Button} from '../ui/Button'
import {Checkbox} from '../ui/Checkbox'
import {ManualFrame,ManualCard} from './ManualFrame'
import {ManualContentView} from './ManualContentView'
import {ManualWorkerPreview} from './ManualWorkerPreview'
import {ManualVoiceComposer} from './ManualVoiceComposer'
import {createDraftVoiceCorrection,publicationInput} from './draftCommands'
import {errorMessage,ManualError,type ManualService} from './service'
import {pause,waitVisible} from './poll'
import {useManualTask} from './useManualTask'
import type {ManualDraft,ManualDraftCorrection,ManualDraftCorrectionTarget,ManualWorkerPreview as Preview,PublishedManual} from './types'
import type {Recording} from './voice'

export function ManualDraftReview({service,versionId,onBack,onReload}:{service:ManualService;versionId:string;onBack:()=>void;onReload:()=>void}) {
 const [draft,setDraft]=useState<ManualDraft|null>(null),[preview,setPreview]=useState<Preview|null>(null),[target,setTarget]=useState<ManualDraftCorrectionTarget|null>(null),[published,setPublished]=useState<PublishedManual|null>(null),[readError,setReadError]=useState(''),[refresh,setRefresh]=useState(0),[checked,setChecked]=useState<string[]>([]),[confirmed,setConfirmed]=useState('')
 const task=useManualTask(),epoch=useRef(0),voice=useRef<{recording:Recording;run:ReturnType<typeof createDraftVoiceCorrection>}|null>(null)
 const apply=useCallback((updated:ManualDraft)=>{if(updated.versionId!==versionId)throw new ManualError('MANUAL_VERSION_CONFLICT');setDraft(old=>!old||updated.revision>old.revision||updated.revision===old.revision&&(!old.latestCorrection||!!updated.latestCorrection&&(old.latestCorrection.id!==updated.latestCorrection.id||updated.latestCorrection.attempt>=old.latestCorrection.attempt))?updated:old)},[versionId])
 useEffect(()=>{
  if(published)return
  const controller=new AbortController(),signal=controller.signal
  void (async()=>{while(!signal.aborted){await waitVisible(signal);const readEpoch=epoch.current;try{
   const result=await service.call('getManualDraft',{},undefined,{signal});signal.throwIfAborted();if(readEpoch!==epoch.current)continue;apply(result.data)
   if(result.data.latestCorrection?.status==='RUNNING'){
    const job=await service.call('getManualDraftCorrection',{correctionId:result.data.latestCorrection.id},undefined,{signal});signal.throwIfAborted()
    if(job.data.versionId!==versionId||job.data.id!==result.data.latestCorrection.id)throw new ManualError('INVALID_RESPONSE')
    // Read the committed draft after completion; a job response alone is not new content.
    if(job.data.status!=='RUNNING'){const updated=await service.call('getManualDraft',{},undefined,{signal});signal.throwIfAborted();if(readEpoch===epoch.current)apply(updated.data)}
   }
   setReadError('');await pause(result.retryAfterMs,signal)
  }catch(e){if(signal.aborted)return;setReadError(errorMessage(e));if(e instanceof ManualError&&['MANUAL_VERSION_CONFLICT','MANUAL_RESOURCE_NOT_FOUND'].includes(e.code))return;await pause(2000,signal)}}})().catch(e=>{if(!signal.aborted)setReadError(errorMessage(e))})
  return()=>controller.abort()
 },[service,versionId,refresh,published,apply])
 function acceptJob(job:ManualDraftCorrection){epoch.current++;if(job.versionId!==versionId)throw new ManualError('MANUAL_VERSION_CONFLICT');setDraft(old=>old&&old.versionId===job.versionId&&old.revision===job.baseRevision?{...old,latestCorrection:job} as ManualDraft:old);setPreview(null);setTarget(null);setConfirmed('');setRefresh(v=>v+1)}
 async function correct(recording:Recording,signal:AbortSignal){if(!draft||!target)throw new ManualError('MANUAL_STATE_CONFLICT');if(voice.current?.recording!==recording)voice.current={recording,run:createDraftVoiceCorrection(service,draft,target,recording)};const job=await voice.current.run(signal);signal.throwIfAborted();acceptJob(job);voice.current=null}
 function retryCorrection(){if(!draft||draft.latestCorrection?.status!=='ERROR'||!draft.latestCorrection.error.retryable)return;const captured=draft,job=draft.latestCorrection;void task.run(async(signal,key)=>{const result=await service.call('retryManualDraftCorrection',{correctionId:job.id},{expectedVersionId:captured.versionId,expectedRevision:captured.revision},{signal,key});signal.throwIfAborted();if(result.data.id!==job.id)throw new ManualError('INVALID_RESPONSE');acceptJob(result.data)})}
 function retryGeneration(){if(!draft?.interviewSessionId)return;const sessionId=draft.interviewSessionId;let revision:number|undefined;void task.run(async(signal,key)=>{if(revision===undefined){const session=await service.call('getManualInterview',{sessionId},undefined,{signal});if(session.data.draftVersionId!==versionId||session.data.processing?.kind!=='DRAFT_GENERATION')throw new ManualError('MANUAL_STATE_CONFLICT');revision=session.data.revision;}await service.call('retryManualInterviewProcessing',{sessionId},{expectedRevision:revision},{signal,key});signal.throwIfAborted();setRefresh(v=>v+1)})}
 function showPreview(){if(!draft)return;const captured=draft;void task.run(async(signal)=>{const result=await service.call('previewManualForWorker',{},undefined,{signal});signal.throwIfAborted();if(result.data.versionId!==captured.versionId||result.data.revision!==captured.revision)throw new ManualError('REVISION_CONFLICT');setPreview(result.data)})}
 function acknowledge(){if(!draft)return;const captured=draft,issueIds=captured.issues.filter(i=>i.status==='OPEN').map(i=>i.id);void task.run(async(signal,key)=>{const result=await service.call('acknowledgeManualIssues',{}, {expectedVersionId:captured.versionId,expectedRevision:captured.revision,issueIds,confirmed:true},{signal,key});signal.throwIfAborted();epoch.current++;apply(result.data);setChecked([]);setPreview(null)})}
 function publish(){if(!draft)return;const captured=draft,body=publicationInput(captured);void task.run(async(signal,key)=>{const result=await service.call('publishManualDraft',{},body,{signal,key});signal.throwIfAborted();if(result.data.versionId!==captured.versionId)throw new ManualError('INVALID_RESPONSE');setPublished(result.data)})}
 if(published)return <ManualFrame title="매뉴얼 게시 완료" stage={4} onBack={onBack} footer={<Button onClick={onBack}>완료</Button>}><div className="manual-stack"><h2 className="manual-heading">매뉴얼을 게시했어요</h2><p>근무자가 확인할 수 있어요.</p><ManualContentView content={published.content} service={service}/></div></ManualFrame>
 const job=draft?.latestCorrection,running=job?.status==='RUNNING',failure=job?.status==='ERROR',stamp=draft?`${draft.versionId}:${draft.revision}:${job?.id??''}:${job?.attempt??0}`:'',reconfirmed=confirmed===stamp
 const ready=draft?.generationStatus==='READY'&&!readError,blocked=!ready||running||task.busy,open=draft?.issues.filter(i=>i.status==='OPEN')??[]
 if(preview&&draft&&preview.versionId===draft.versionId&&preview.revision===draft.revision&&!running)return <ManualWorkerPreview preview={preview} service={service} onClose={()=>setPreview(null)}/>
 const footer=target?<ManualVoiceComposer key={`${target.kind}:${target.targetId}`} disabled={blocked} onRecording={correct} label="말해서 수정하기"/>:<div className="manual-button-row"><Button intent="secondary" disabled={blocked} onClick={()=>setTarget({kind:'MANUAL',targetId:null})}>수정할게요</Button><Button busy={task.busy} disabled={blocked||open.length>0||(failure&&!reconfirmed)} onClick={publish}>확인하고 게시</Button></div>
 return <ManualFrame title={target?'매뉴얼 수정':'매뉴얼 전체 확인'} stage={4} onBack={target?()=>{setTarget(null);voice.current=null}:onBack} footer={footer}><div className="manual-stack">
  <h2 className="manual-heading">{target?'어떤 부분을\n고치면 될까요?':'우리 매장 운영을\n한눈에 확인해 주세요'}</h2><p className="manual-muted">{target?'다르게 정리된 내용을 말해 주세요.':'각 항목을 확인하고 다르게 정리된 내용은 말해서 고칠 수 있어요.'}</p>
  {readError&&<><p role="alert">{readError}</p><Button intent="secondary" onClick={onReload}>최신 작성 상태 다시 불러오기</Button></>}
  {!draft&&<p role="status">초안을 불러오고 있어요.</p>}
  {draft&&draft.generationStatus!=='READY'&&<><p role={draft.generationStatus==='ERROR'?'alert':'status'}>{draft.generationStatus==='ERROR'?'매뉴얼을 만들지 못했어요.':'매뉴얼을 만들고 있어요.'}</p>{draft.generationStatus==='ERROR'&&<Button busy={task.busy} onClick={retryGeneration}>매뉴얼 생성 다시 시도</Button>}</>}
  {running&&<p role="status">수정한 내용을 정리하고 있어요. 저장된 내용은 아래에서 확인할 수 있어요.</p>}
  {failure&&<ManualCard title="수정을 반영하지 못했어요"><p role="alert">{errorMessage(new ManualError(job.error.code))}</p>{job.error.retryable&&<Button disabled={blocked} onClick={retryCorrection}>수정 처리 다시 시도</Button>}<Button intent="secondary" disabled={blocked} onClick={()=>setTarget(job.target)}>다시 말해서 수정하기</Button><Button intent="secondary" disabled={blocked} onClick={()=>setConfirmed(stamp)}>이전 내용을 확인했어요</Button>{reconfirmed&&<p>이전 내용을 게시할 수 있어요.</p>}</ManualCard>}
  {draft?.content&&<><Button intent="secondary" disabled={blocked||!!target} onClick={showPreview}>근무자 화면 미리보기</Button><ManualContentView content={draft.content} service={service} onCorrect={!blocked&&!target?t=>setTarget(t):undefined}/>{target&&<p className="manual-muted">수정 대상: {target.kind==='MANUAL'?'전체 매뉴얼':target.kind==='SHIFT'?draft.content.shifts.find(s=>s.id===target.targetId)?.name??'변경된 근무조':draft.content.sections.find(s=>s.id===target.targetId)?.title??'변경된 업무'}</p>}</>}
  {!!draft?.issues.length&&<ManualCard title="추가 확인이 필요한 내용">{draft.issues.map(issue=><label className="manual-issue" key={issue.id}><Checkbox disabled={blocked||issue.status==='ACKNOWLEDGED'} checked={issue.status==='ACKNOWLEDGED'||checked.includes(`${draft.revision}:${issue.id}`)} onChange={e=>setChecked(old=>e.target.checked?[...old,`${draft.revision}:${issue.id}`]:old.filter(id=>id!==`${draft.revision}:${issue.id}`))}/><span>{issue.description}{issue.status==='ACKNOWLEDGED'?' · 확인 완료':''}</span></label>)}{!!open.length&&<Button disabled={blocked||open.some(i=>!checked.includes(`${draft.revision}:${i.id}`))} onClick={acknowledge}>부족한 내용을 확인했어요</Button>}</ManualCard>}
  {task.error&&<><p role="alert">{task.error}</p>{task.canRetry&&<Button intent="secondary" onClick={task.retry}>요청 다시 시도</Button>}<Button intent="secondary" onClick={onReload}>최신 작성 상태 다시 불러오기</Button></>}
  {!target&&ready&&<p className="manual-muted">게시하면 근무자가 이 내용을 볼 수 있어요.</p>}
 </div></ManualFrame>
}

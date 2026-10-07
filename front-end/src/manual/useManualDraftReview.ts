import {useCallback,useEffect,useRef,useState} from 'react'
import {createDraftVoiceCorrection,publicationInput} from './draftCommands'
import {errorMessage,ManualError,type ManualService} from './service'
import {pause,waitVisible} from './poll'
import {useManualTask} from './useManualTask'
import type {ManualDraft,ManualDraftCorrection,ManualDraftCorrectionTarget,ManualWorkerPreview as Preview,PublishedManual} from './types'
import type {Recording} from './voice'

export function useManualDraftReview({service,versionId}:{service:ManualService;versionId:string}) {
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
 return {draft,preview,setPreview,target,setTarget,published,readError,checked,setChecked,confirmed,setConfirmed,task,voice,correct,retryCorrection,retryGeneration,showPreview,acknowledge,publish}
}

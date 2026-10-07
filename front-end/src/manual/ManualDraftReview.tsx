import {useState} from 'react'
import {Button} from '../ui/Button'
import {Modal} from '../ui/Modal'
import {ManualDraftDetail} from './ManualDraftDetail'
import {ManualDraftPhotos} from './ManualDraftPhotos'
import {ManualDraftSummary,type SummaryGroup} from './ManualDraftSummary'
import {ManualIssuesScreen} from './ManualIssuesScreen'
import {ManualErrorDialog} from './ManualErrorDialog'
import {useManualDraftReview} from './useManualDraftReview'
import {ManualFrame} from './ManualFrame'
import {ManualContentView} from './ManualContentView'
import {ManualWorkerPreview} from './ManualWorkerPreview'
import {ManualVoiceComposer} from './ManualVoiceComposer'
import {errorMessage,ManualError,type ManualService} from './service'
import type {PhotoTarget} from './photos'

export function ManualDraftReview({service,versionId,onBack,onReload}:{service:ManualService;versionId:string;onBack:()=>void;onReload:()=>void}) {
 const [group,setGroup]=useState<SummaryGroup|'photos'|null>(null)
 const [photoTarget,setPhotoTarget]=useState<PhotoTarget|null>(null)
 const [showIssues,setShowIssues]=useState(false)
 const state=useManualDraftReview({service,versionId})
 const {cancelCorrection,updatePhotos,draft,preview,setPreview,target,setTarget,published,readError,checked,setChecked,confirmed,setConfirmed,task,correct,retryCorrection,retryGeneration,showPreview,acknowledge,publish}=state
 if(published)return <ManualFrame title="매뉴얼 게시 완료" stage={4} onBack={onBack} footer={<Button onClick={onBack}>완료</Button>}><div className="manual-stack"><h2 className="manual-heading">매뉴얼을 게시했어요</h2><p>근무자가 확인할 수 있어요.</p><ManualContentView content={published.content} service={service}/></div></ManualFrame>
 const job=draft?.latestCorrection,running=job?.status==='RUNNING',failure=job?.status==='ERROR'
 const stamp=draft?`${draft.versionId}:${draft.revision}:${job?.id??''}:${job?.attempt??0}`:'',reconfirmed=confirmed===stamp
 const ready=draft?.generationStatus==='READY'&&!readError,blocked=!ready||running||task.busy,open=draft?.issues.filter(i=>i.status==='OPEN')??[]
 const overlays=<>
  <ManualErrorDialog error={readError||task.error} onClose={readError?onReload:task.clearError} onRetry={task.canRetry?task.retry:undefined} onReload={onReload}/>
  <ManualErrorDialog error={draft?.generationStatus==='ERROR'?'매뉴얼을 만들지 못했어요.':''} onClose={onBack} onRetry={retryGeneration} retryLabel="매뉴얼 생성 다시 시도" busy={task.busy}/>
  <Modal open={!!failure&&!reconfirmed&&!target} state="error" title="수정을 반영하지 못했어요" description={failure?errorMessage(new ManualError(job.error.code)):''} onClose={onBack} closeOnConfirm={false} confirmLabel="이전 내용을 확인했어요" onConfirm={()=>setConfirmed(stamp)} summary={<div className="manual-stack">{failure&&job.error.retryable&&<Button disabled={blocked} onClick={retryCorrection}>수정 처리 다시 시도</Button>}<Button intent="secondary" disabled={blocked} onClick={()=>{if(job)setTarget(job.target)}}>다시 말해서 수정하기</Button></div>}/>
 </>
 let view
 if(preview&&draft&&preview.versionId===draft.versionId&&preview.revision===draft.revision&&!running)view=<ManualWorkerPreview preview={preview} service={service} onClose={()=>setPreview(null)}/>
 else if(photoTarget&&draft)view=<ManualDraftPhotos service={service} draft={draft} target={photoTarget} onUpdate={updatePhotos} onClose={()=>setPhotoTarget(null)}/>
 else if(showIssues&&draft&&open.length)view=<ManualIssuesScreen draft={draft} checked={checked} onChecked={setChecked} disabled={blocked} onConfirm={()=>{acknowledge();setShowIssues(false)}} onBack={()=>setShowIssues(false)}/>
 else if(group&&draft?.content&&!target)view=<ManualDraftDetail content={draft.content} group={group} service={service} disabled={blocked} onCorrect={setTarget} onPhotos={setPhotoTarget} onBack={()=>setGroup(null)}/>
 else {
  const footer=target?<ManualVoiceComposer key={`${target.kind}:${target.targetId}`} disabled={blocked} onRecording={correct} label="말해서 수정하기"/>:<div className="manual-button-row"><Button intent="secondary" disabled={blocked} onClick={()=>setTarget({kind:'MANUAL',targetId:null})}>수정하기</Button><Button busy={task.busy} disabled={blocked||(failure&&!reconfirmed)} onClick={()=>{if(open.length)setShowIssues(true);else publish()}}>확인하고 게시</Button></div>
  const content=draft?.content
  const correctionContent=content&&target?{...content,shifts:target.kind==='MANUAL'?content.shifts:target.kind==='SHIFT'?content.shifts.filter(s=>s.id===target.targetId):[],sections:target.kind==='MANUAL'?content.sections:target.kind==='SECTION'?content.sections.filter(s=>s.id===target.targetId):[],missingInformation:[]}:null
  view=<ManualFrame compactFooter={!target} title={target?'매뉴얼 수정':'매뉴얼 전체 확인'} stage={4} onBack={target?cancelCorrection:onBack} footer={footer}><div className="manual-stack">
   <h2 className="manual-heading">{target?'어떤 부분을\n고치면 될까요?':'우리 매장 운영을\n한눈에 확인해 주세요'}</h2><p className="manual-muted">{target?'다르게 정리된 내용을 말해 주세요.':'각 항목을 눌러 자세히 보거나 수정할 수 있어요.'}</p>
   {content&&(target&&correctionContent?<ManualContentView content={correctionContent} service={service}/>:<><Button intent="secondary" disabled={blocked} onClick={showPreview}>근무자 화면 미리보기</Button><ManualDraftSummary content={content} service={service} disabled={blocked} onSelect={setGroup} onPhotos={()=>setGroup('photos')}/><p className="manual-muted manual-caption">게시하면 근무자가 이 내용을 볼 수 있어요.</p></>)}
  </div></ManualFrame>
 }
 return <>{view}{overlays}<Modal open={!readError&&(!draft||draft.generationStatus==='RUNNING'||draft.generationStatus==='NOT_STARTED'||running)} showIcon={false} title={running?'수정한 내용을 정리하고 있어요':'매뉴얼을 준비하고 있어요'} description="잠시만 기다려 주세요." onClose={onBack} confirmLabel="작성 화면 나가기"/></>
}

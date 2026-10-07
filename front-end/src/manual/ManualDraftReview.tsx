import {useManualDraftReview} from './useManualDraftReview'
import {Button} from '../ui/Button'
import {Checkbox} from '../ui/Checkbox'
import {ManualFrame,ManualCard} from './ManualFrame'
import {ManualContentView} from './ManualContentView'
import {ManualWorkerPreview} from './ManualWorkerPreview'
import {ManualVoiceComposer} from './ManualVoiceComposer'
import {errorMessage,ManualError,type ManualService} from './service'

export function ManualDraftReview({service,versionId,onBack,onReload}:{service:ManualService;versionId:string;onBack:()=>void;onReload:()=>void}) {
 const {draft,preview,setPreview,target,setTarget,published,readError,checked,setChecked,confirmed,setConfirmed,task,voice,correct,retryCorrection,retryGeneration,showPreview,acknowledge,publish}=useManualDraftReview({service,versionId})
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

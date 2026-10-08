import {useEffect,useMemo,useState} from 'react'
import {call} from '../api/operations'
import {ApiError} from '../api/client'
import type {Operations} from '../api/types.generated'
import {liveManualService} from '../manual/live'
import {ManualPhoto} from '../manual/ManualPhoto'
import {Modal} from '../ui/Modal'
import {LoadingState} from '../ui/LoadingState'
import {accessLost,qaError} from './errors'
import type {QACitation} from './types.generated'
export function QaCitation({citation,storeId,onClose,onLatest,onAccessLost}:{citation:QACitation;storeId:string;onClose:()=>void;onLatest:()=>void;onAccessLost?:(e:unknown)=>void}){
 const [detail,setDetail]=useState<Operations['getPublishedManualSection']['output']|null>(null),[error,setError]=useState<unknown>(null),[attempt,setAttempt]=useState(0)
 const service=useMemo(()=>liveManualService(storeId),[storeId])
 useEffect(()=>{const controller=new AbortController();void call('getPublishedManualSection',{signal:controller.signal,params:{storeId,sectionId:citation.sectionId},query:{expectedVersionId:citation.versionId}}).then(data=>{if(!controller.signal.aborted)setDetail(data)}).catch(e=>{if(!controller.signal.aborted){setError(e);if(accessLost(e))onAccessLost?.(e)}});return()=>controller.abort()},[storeId,citation.sectionId,citation.versionId,attempt,onAccessLost])
 const changed=error instanceof ApiError&&error.code==='MANUAL_VERSION_CHANGED'
 return <Modal open title={citation.sectionTitle} description={error?qaError(error):'답변의 근거가 된 매뉴얼이에요.'} onClose={onClose} confirmLabel={changed?'최신 매뉴얼 보기':error?'다시 불러오기':'닫기'} closeOnConfirm={false} onConfirm={changed?onLatest:error?()=>{setError(null);setAttempt(v=>v+1)}:onClose} summary={detail?<div className="qa-citation-detail"><ol>{detail.section.steps.map(step=><li key={step.id}>{step.instruction}</li>)}</ol>{detail.section.photos.map(photo=><ManualPhoto key={photo.mediaId} photo={photo} service={service}/>)}</div>:!error?<LoadingState/>:undefined}/>
}

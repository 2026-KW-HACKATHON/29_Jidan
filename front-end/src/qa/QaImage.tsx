import {useEffect,useState} from 'react'
import {accessLost} from './errors'
import type {QaService} from './service'
export function QaImage({service,mediaId,index,onAccessLost}:{service:QaService;mediaId:string;index:number;onAccessLost:(e:unknown)=>void}){
 const [url,setUrl]=useState(''),[failed,setFailed]=useState(false),[attempt,setAttempt]=useState(0)
 useEffect(()=>{const controller=new AbortController();let objectUrl=''
  void service.call('getQAQuestionMediaContent',undefined,{signal:controller.signal,params:{mediaId}}).then(({data})=>{if(!controller.signal.aborted){objectUrl=URL.createObjectURL(data);setUrl(objectUrl)}}).catch(e=>{if(!controller.signal.aborted){setFailed(true);if(accessLost(e))onAccessLost(e)}})
  return()=>{controller.abort();if(objectUrl)URL.revokeObjectURL(objectUrl)}
 },[service,mediaId,attempt,onAccessLost])
 return url?<img src={url} alt={`질문 사진 ${index+1}`}/>:failed?<button type="button" onClick={()=>{setFailed(false);setAttempt(v=>v+1)}}>사진 다시 보기</button>:<span>사진을 불러오고 있어요.</span>
}

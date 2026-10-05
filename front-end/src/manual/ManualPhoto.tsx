import {useEffect,useState} from 'react'
import {Button} from '../ui/Button'
import {errorMessage, type ManualService} from './service'
import type {ManualPhotoAttachment} from './types'

/** Protected bytes only. URLs and in-flight reads are released on target change. */
export function ManualPhoto({photo,service}:{photo:ManualPhotoAttachment;service:ManualService}) {
 const [result,setResult]=useState({mediaId:'',url:'',error:''}),[refresh,setRefresh]=useState(0)
 const {url,error}=result.mediaId===photo.mediaId?result:{url:'',error:''}
 useEffect(()=>{
  const controller=new AbortController();let objectUrl=''
  void service.call('readManualPhoto',{mediaId:photo.mediaId},undefined,{signal:controller.signal}).then(result=>{
   if(controller.signal.aborted)return
   objectUrl=URL.createObjectURL(result.data);setResult({mediaId:photo.mediaId,url:objectUrl,error:''})
  }).catch(e=>{if(!controller.signal.aborted)setResult({mediaId:photo.mediaId,url:'',error:errorMessage(e)})})
  return()=>{controller.abort();if(objectUrl)URL.revokeObjectURL(objectUrl)}
 },[service,photo.mediaId,refresh])
 return <figure className="manual-photo">{url?<img src={url} alt={photo.title}/>:<div className="manual-photo-placeholder" role="status">{error?'사진을 불러오지 못했어요.':'사진을 불러오고 있어요.'}</div>}<figcaption>{photo.title}</figcaption>{error&&<Button intent="secondary" onClick={()=>{setResult({mediaId:'',url:'',error:''});setRefresh(v=>v+1)}}>사진 다시 불러오기</Button>}</figure>
}

import {useEffect,useRef,useState} from 'react'
import './ManualWorkerPreview.css'
import {Button} from '../ui/Button'
import {ManualFrame,ManualCard} from './ManualFrame'
import {ManualPhotoGrid} from './ManualPhotoGroup'
import type {ManualService} from './service'
import type {ManualWorkerPreview as Preview} from './types'
export function ManualWorkerPreview({preview,service,onClose}:{preview:Preview;service:ManualService;onClose:()=>void}) {
 const [page,setPage]=useState(0),sections=preview.content.sections,index=Math.min(page,Math.max(0,sections.length-1)),section=sections[index],last=index>=sections.length-1,root=useRef<HTMLDivElement>(null)
 useEffect(()=>{const body=root.current?.closest('main');if(body)body.scrollTop=0},[index])
 const footer=<div className="manual-button-row">{sections.length>1&&(index===0?<Button intent="secondary" onClick={onClose}>검토로 돌아가기</Button>:<Button intent="secondary" onClick={()=>setPage(index-1)}>이전</Button>)}{last?<Button onClick={onClose}>{sections.length?'검토 완료':'검토로 돌아가기'}</Button>:<Button onClick={()=>setPage(index+1)}>다음</Button>}</div>
 return <ManualFrame title="근무자 화면 미리보기" showProgress={false} onBack={onClose} footer={footer}><div ref={root} className="manual-stack manual-worker-preview"><p className="manual-preview-label">게시 전 미리보기</p><h2>매장 업무 안내</h2>
  {section?<section className="manual-card"><p className="manual-muted manual-caption">{index+1} / {sections.length}</p><h3>{section.title}</h3><div>{section.steps.map(step=><p key={step.id}>{step.instruction}</p>)}{!section.steps.length&&<p>업무 절차가 아직 정해지지 않았어요.</p>}</div>{!!section.photos.length&&<ManualPhotoGrid photos={section.photos} service={service}/>}</section>:<ManualCard title="업무 안내"><p>등록된 업무가 없어요.</p></ManualCard>}
 </div></ManualFrame>
}

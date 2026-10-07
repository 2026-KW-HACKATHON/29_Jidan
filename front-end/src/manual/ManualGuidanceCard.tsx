import {Button} from '../ui/Button'
import checkIcon from '../ui/assets/check.svg'
import {ManualCard} from './ManualFrame'
import type {ManualGuidanceCard as Card,ManualGuidancePhotoTarget} from './types'
import './ManualGuidanceCard.css'
const statusLabels={PENDING:'대기',CURRENT:'현재 항목',COMPLETED:'완료',NEEDS_DETAIL:'보완 필요'}
/** Server-owned progress is read-only, not an answer checkbox. */
export function ManualGuidanceCard({card,onPhotos}:{card:Card;onPhotos?:(target:ManualGuidancePhotoTarget)=>void}) {
 return <ManualCard title={card.title}>
  {card.type==='PROGRESS_CHECKLIST'?<ul className="manual-checklist">{card.items.map(item=><li key={item.id} data-state={item.status} aria-current={item.status==='CURRENT'?'step':undefined}>
   <span className="manual-progress-box" aria-label={statusLabels[item.status]}>{item.status==='COMPLETED'&&<img src={checkIcon} alt=""/>}{item.status==='NEEDS_DETAIL'&&'!'}</span>
   <div><p>{item.label}</p>{item.description&&<p className="manual-muted">{item.description}</p>}{item.status==='NEEDS_DETAIL'&&<small>보완 필요</small>}</div>
  </li>)}</ul>:<ul className="manual-info-list">{card.items.map(item=><li key={item.id}><p>{item.label}</p>{item.description&&<p className="manual-muted">{item.description}</p>}</li>)}</ul>}
  {card.footer&&<p className="manual-muted">{card.footer}</p>}
  {card.type==='PHOTO_SUGGESTIONS'&&card.attachmentTarget&&onPhotos&&<Button intent="secondary" onClick={()=>onPhotos(card.attachmentTarget!)}>사진 첨부하기</Button>}
 </ManualCard>
}

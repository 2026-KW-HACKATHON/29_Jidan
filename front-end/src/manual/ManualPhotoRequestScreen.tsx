import {Button} from '../ui/Button'
import {ManualFrame} from './ManualFrame'
import {ManualGuidanceCard} from './ManualGuidanceCard'
import type {ManualGuidancePhotos,ManualInterviewQuestion} from './types'
/** Optional attachment view. Closing it never submits an interview answer. */
export function ManualPhotoRequestScreen({question,contextLabel,card,stage,onAttach,onContinue,busy}:{question:ManualInterviewQuestion;contextLabel?:string;card:ManualGuidancePhotos;stage:number;onAttach:()=>void;onContinue:()=>void;busy:boolean}) {
 return <ManualFrame stage={stage} onBack={onContinue} footer={<div className="manual-stack"><Button disabled={!card.attachmentTarget} busy={busy} onClick={onAttach}>사진 첨부하기</Button><Button intent="secondary" disabled={busy} onClick={onContinue}>사진 없이 계속하기</Button></div>}><div className="manual-question-screen"><h2 className="manual-heading">{contextLabel??question.text}</h2>{!contextLabel&&question.guidance&&<p className="manual-muted">{question.guidance}</p>}<ManualGuidanceCard card={card}/></div></ManualFrame>
}

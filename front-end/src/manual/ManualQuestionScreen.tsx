import {useEffect,useRef} from 'react'
import {ManualGuidanceCard} from './ManualGuidanceCard'
import type {ManualInterviewQuestion,ManualGuidancePhotoTarget} from './types'
export function ManualQuestionScreen({question,onPhotos}:{question:ManualInterviewQuestion;onPhotos?:(target:ManualGuidancePhotoTarget)=>void}) {
 const root=useRef<HTMLDivElement>(null)
 useEffect(()=>{const body=root.current?.closest('main');if(body)body.scrollTop=0},[question.id])
 return <div ref={root} className="manual-question-screen"><h2 className="manual-heading">{question.text}</h2>{question.guidance&&<p className="manual-muted manual-question">{question.guidance}</p>}{question.guidanceCards?.map(card=><ManualGuidanceCard key={card.id} card={card} onPhotos={onPhotos}/>)}</div>
}

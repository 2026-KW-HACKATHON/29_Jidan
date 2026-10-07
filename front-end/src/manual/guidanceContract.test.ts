import {expect,it} from 'vitest'
import {schemas} from './contract.generated'
import {matches} from './validation'
import {interviewFixture} from '../dev/manualFixtures'
const id=()=>crypto.randomUUID()
const item={id:id(),label:'재고 정리'}
const checklist={id:id(),type:'PROGRESS_CHECKLIST',title:'공통 업무',items:[{...item,status:'PENDING'}]}
const valid=(name:string,value:unknown)=>matches(schemas[name],value,schemas)
it('구 질문과 안내 카드 없는 응답을 허용한다',()=>{
 expect(valid('ManualInterviewSession',interviewFixture)).toBe(true)
 expect(valid('ManualInterviewQuestion',{...interviewFixture.questions[0],guidance:null,guidanceCards:[]})).toBe(true)
})
it('진행 카드에 CURRENT는 0 또는 1개만 허용한다',()=>{
 expect(valid('ManualGuidanceCard',checklist)).toBe(true)
 expect(valid('ManualGuidanceCard',{...checklist,items:[{...item,status:'CURRENT'}]})).toBe(true)
 expect(valid('ManualGuidanceCard',{...checklist,items:[{...item,status:'CURRENT'},{id:id(),label:'시재',status:'CURRENT'}]})).toBe(false)
})
it('일반 목록은 진행 상태를 거절하고 빈 목록·알 수 없는 종류를 거절한다',()=>{
 expect(valid('ManualGuidanceCard',{...checklist,type:'LIST',items:[item]})).toBe(true)
 expect(valid('ManualGuidanceCard',{...checklist,type:'LIST'})).toBe(false)
 expect(valid('ManualGuidanceCard',{...checklist,items:[]})).toBe(false)
 expect(valid('ManualGuidanceCard',{...checklist,type:'UNKNOWN'})).toBe(false)
})
it('사진 추천은 준비 전 null 대상과 올바른 연결 대상만 허용한다',()=>{
 const photo={...checklist,type:'PHOTO_SUGGESTIONS',items:[item],attachmentTarget:null}
 expect(valid('ManualGuidanceCard',photo)).toBe(true)
 expect(valid('ManualGuidanceCard',{...photo,attachmentTarget:{intentId:id(),target:'SECTION',sectionId:null}})).toBe(false)
 expect(valid('ManualGuidanceCard',{...photo,attachmentTarget:{intentId:id(),target:'WORK_STRUCTURE',sectionId:null}})).toBe(true)
})
it('답변 질문 스냅샷은 평가 중에만 표시하며 미답변 스냅샷을 거절한다',()=>{
 const snapshot={...interviewFixture.questions[0],answered:true,guidanceCards:[checklist]}
 const session={...interviewFixture,phase:'PROCESSING',questions:[],processing:{taskId:id(),kind:'EVALUATION',attempt:1},lastAnsweredQuestion:snapshot}
 expect(valid('ManualInterviewSession',session)).toBe(true)
 expect(valid('ManualInterviewSession',{...session,lastAnsweredQuestion:{...snapshot,answered:false}})).toBe(false)
 expect(valid('ManualInterviewSession',{...interviewFixture,lastAnsweredQuestion:snapshot})).toBe(false)
})

import {cleanup,fireEvent,render,screen} from '@testing-library/react'
import {afterEach,expect,it,vi} from 'vitest'
import {ManualQuestionScreen} from './ManualQuestionScreen'
import {interviewFixture} from '../dev/manualFixtures'
import type {ManualGuidanceCard} from './types'
afterEach(cleanup)
const question=interviewFixture.questions[0]
it('일반 질문에서 없는 안내를 만들지 않고 긴 문구를 보존한다',()=>{
 const label='아주 긴 항목 '.repeat(20)
 render(<ManualQuestionScreen question={{...question,guidance:'설명',guidanceCards:[{id:'a',type:'LIST',title:'예시',items:[{id:'b',label}]}]}}/> )
 expect(screen.getByText('설명')).toBeVisible();expect(screen.getByText(label.trim())).toBeVisible();expect(screen.queryByRole('checkbox')).toBeNull()
})
it('진행 상태 4가지를 입력 없이 표시하고 보완을 완료로 취급하지 않는다',()=>{
 const card:ManualGuidanceCard={id:'a',type:'PROGRESS_CHECKLIST',title:'업무',items:['PENDING','CURRENT','COMPLETED','NEEDS_DETAIL'].map((status,i)=>({id:String(i),label:String(i),status:status as 'PENDING'}))}
 render(<ManualQuestionScreen question={{...question,guidanceCards:[card]}}/> )
 for(const label of ['대기','현재 항목','완료','보완 필요'])expect(screen.getByLabelText(label)).toBeInTheDocument()
 expect(screen.queryByRole('checkbox')).toBeNull()
})
it('사진 대상이 준비되지 않으면 첨부 동작을 만들지 않는다',()=>{
 const onPhotos=vi.fn();render(<ManualQuestionScreen question={{...question,guidanceCards:[{id:'a',type:'PHOTO_SUGGESTIONS',title:'추천',items:[{id:'b',label:'열쇠'}],attachmentTarget:null}]}} onPhotos={onPhotos}/>)
 expect(screen.queryByRole('button')).toBeNull()
})
it('같은 질문 갱신은 스크롤을 유지하고 새 질문만 초기화한다',()=>{
 const {rerender}=render(<main><ManualQuestionScreen question={question}/></main>);const main=screen.getByRole('main');main.scrollTop=120
 rerender(<main><ManualQuestionScreen question={{...question,guidance:'보완'}}/></main>);expect(main.scrollTop).toBe(120)
 rerender(<main><ManualQuestionScreen question={{...question,id:'new'}}/></main>);expect(main.scrollTop).toBe(0)
})

it('재정렬된 안정 ID와 null 안내를 유지하며 복수 사진 대상은 각 섹션으로 연결한다',()=>{
 const targets=[{intentId:'prior',target:'SECTION' as const,sectionId:'one'},{intentId:'prior',target:'SECTION' as const,sectionId:'two'}]
 const cards:ManualGuidanceCard[]=targets.map((target,i)=>({id:`card-${i}`,type:'PHOTO_SUGGESTIONS',title:`사진 ${i}`,items:[{id:`item-${i}`,label:`위치 ${i}`,description:null}],footer:null,attachmentTarget:target}))
 const onPhotos=vi.fn(),{rerender}=render(<ManualQuestionScreen question={{...question,guidance:null,guidanceCards:cards}} onPhotos={onPhotos}/>)
 const first=screen.getByText('위치 0').closest('li')
 rerender(<ManualQuestionScreen question={{...question,guidance:null,guidanceCards:[cards[1],cards[0]]}} onPhotos={onPhotos}/>)
 expect(screen.getByText('위치 0').closest('li')).toBe(first)
 screen.getAllByRole('button',{name:'사진 첨부하기'}).forEach(button=>fireEvent.click(button))
 expect(onPhotos.mock.calls.map(([target])=>target)).toEqual([targets[1],targets[0]])
 expect(screen.queryByRole('checkbox')).toBeNull()
})

import {Button} from '../ui/Button'
import {Checkbox} from '../ui/Checkbox'
import {ManualFrame,ManualCard} from './ManualFrame'
import type {ManualDraft} from './types'
export function ManualIssuesScreen({draft,checked,onChecked,disabled,onConfirm,onBack}:{draft:ManualDraft;checked:string[];onChecked:(checked:string[])=>void;disabled:boolean;onConfirm:()=>void;onBack:()=>void}) {
 const open=draft.issues.filter(i=>i.status==='OPEN')
 return <ManualFrame title="게시 전 확인" stage={4} onBack={onBack} footer={<Button disabled={disabled||open.some(i=>!checked.includes(`${draft.revision}:${i.id}`))} onClick={onConfirm}>부족한 내용을 확인했어요</Button>}><div className="manual-stack"><h2 className="manual-heading">추가 확인이 필요한 내용</h2><p className="manual-muted">아래 내용을 확인한 뒤 전체 검토로 돌아가 주세요.</p><ManualCard title="확인할 항목">{draft.issues.map(issue=><label className="manual-issue" key={issue.id}><Checkbox disabled={disabled||issue.status==='ACKNOWLEDGED'} checked={issue.status==='ACKNOWLEDGED'||checked.includes(`${draft.revision}:${issue.id}`)} onChange={e=>{const key=`${draft.revision}:${issue.id}`;onChecked(e.target.checked?[...checked,key]:checked.filter(id=>id!==key))}}/><span>{issue.description}</span></label>)}</ManualCard></div></ManualFrame>
}

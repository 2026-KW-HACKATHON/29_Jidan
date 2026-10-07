import {Button} from '../ui/Button'
import {Modal} from '../ui/Modal'
export function ManualErrorDialog({error,onClose,onRetry,onReload,busy=false,retryLabel='요청 다시 시도'}:{error:string;onClose:()=>void;onRetry?:()=>void;onReload?:()=>void;busy?:boolean;retryLabel?:string}) {
 return <Modal open={!!error} state="error" title="다시 확인해 주세요" description={error} onClose={onClose} confirmLabel={onRetry?retryLabel:onReload?'최신 내용 다시 불러오기':'확인'} onConfirm={onRetry??onReload??onClose} closeOnConfirm={!onRetry} busy={busy} summary={onRetry&&onReload?<Button intent="secondary" onClick={onReload}>최신 작성 상태 다시 불러오기</Button>:undefined}/>
}

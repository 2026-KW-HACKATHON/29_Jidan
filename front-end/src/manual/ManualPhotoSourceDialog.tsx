import {Button} from '../ui/Button'
import {Modal} from '../ui/Modal'
export type PhotoSource='camera'|'album'|'file'
export function ManualPhotoSourceDialog({open,onClose,onSelect}:{open:boolean;onClose:()=>void;onSelect:(source:PhotoSource)=>void}) {
 return <Modal open={open} title="사진 추가" description="사진을 가져올 방법을 선택해 주세요." showIcon={false} onClose={onClose} confirmLabel="돌아가기" className="manual-photo-source" summary={<div className="manual-stack"><Button intent="secondary" onClick={()=>onSelect('camera')}>카메라로 촬영</Button><Button intent="secondary" onClick={()=>onSelect('album')}>앨범에서 선택</Button><Button intent="secondary" onClick={()=>onSelect('file')}>파일에서 선택</Button></div>}/>
}

import { SingleSelectDialog } from '../../ui/SingleSelectDialog'
import { industries,type Industry } from './model'
export function IndustryPicker({value,onClose,onConfirm}:{value:Industry;onClose:()=>void;onConfirm:(value:Industry)=>void}){
 return <SingleSelectDialog title="업종 선택" description="매장에 해당하는 업종을 선택해 주세요." options={industries} value={value} onClose={onClose} onConfirm={onConfirm}/>
}

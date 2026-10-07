import {useMemo,useState} from 'react'
import {OwnerStore} from '../registration/owner/OwnerStore'
import {emptyDraft,validateOwner,type OwnerErrors} from '../registration/owner/model'
import {mutation} from '../api/operations'
import {useCommand} from '../async/useCommand'
import {MobileLayout} from '../ui/MobileLayout'
import {AppBar} from '../ui/AppBar'
import {Button} from '../ui/Button'
export function AddStore({onBack,onCreated}:{onBack:()=>void;onCreated:(id:string)=>void}){const [draft,setDraft]=useState({...emptyDraft}),[errors,setErrors]=useState<OwnerErrors>({}),[failed,setFailed]=useState(false),write=useMemo(()=>mutation(),[]),{busy,run}=useCommand();async function save(){const errors=validateOwner(draft,2);setErrors(errors);if(Object.keys(errors).length)return;setFailed(false);const industries={'음식점':'RESTAURANT','카페':'CAFE','편의점':'CONVENIENCE_STORE','기타':'OTHER','':'OTHER'} as const;const ok=await run(signal=>write('createMyOwnerStore',{signal,input:{name:draft.storeName.trim(),industry:industries[draft.industry],postalCode:draft.postcode,address:draft.address.trim(),detailAddress:draft.detailAddress.trim(),businessRegistrationNumber:draft.businessNumber.replace(/\D/g,''),phoneNumber:draft.storePhone.replace(/\D/g,'')}}),store=>onCreated(store.id));if(!ok)setFailed(true)}return <MobileLayout header={<AppBar title="매장 추가" onBack={onBack}/>} footer={<Button busy={busy} onClick={()=>void save()}>매장 등록 신청</Button>}><div className="home-content"><OwnerStore draft={draft} errors={errors} onChange={(key,value)=>setDraft(d=>({...d,[key]:value}))}/>{failed&&<p role="alert">매장을 등록하지 못했어요. 입력 내용과 사업자 번호를 확인해 주세요.</p>}</div></MobileLayout>}

import { ResponsiveSentences } from '../../ui/ResponsiveSentences'
import { NumberField } from './NumberField'
import { useState } from 'react'
import { InputField, SelectField } from '../../ui/Field'
import { Button } from '../../ui/Button'
import { IndustryPicker } from './IndustryPicker'
import { AddressPicker } from '../AddressPicker'
import type { AddressSearch } from '../postcode'
import { OwnerNotice } from './OwnerStep'
import type { OwnerFieldsProps } from './OwnerBasic'

export function OwnerStore({ draft, errors, onChange, onBlur, addressSearch }: OwnerFieldsProps & { addressSearch?: AddressSearch }) {
  const [picker, setPicker] = useState<'industry' | 'address' | null>(null)
  return <>
    <InputField id="owner-storeName" onBlur={() => onBlur?.('storeName')} label="매장명 *" placeholder="예: 지단 카페 광운대점" required maxLength={100} value={draft.storeName} error={errors.storeName} onChange={event => onChange('storeName', event.target.value)} />
    <SelectField id="owner-industry" label="업종 *" aria-haspopup="dialog" error={errors.industry} onClick={() => setPicker('industry')}>{draft.industry || '업종을 선택해 주세요'}</SelectField>
    <h3 className="owner-section-heading">위치 및 연락 정보</h3>
    <InputField label="우편번호" readOnly value={draft.postcode} placeholder="주소 검색으로 입력해 주세요" />
    <Button intent="secondary" onClick={() => setPicker('address')}>주소 검색</Button>
    <InputField id="owner-address" label="매장 주소 *" readOnly required value={draft.address} error={errors.address} placeholder="월계1동 소재 매장 주소" />
    <InputField id="owner-detailAddress" onBlur={() => onBlur?.('detailAddress')} label="상세주소" value={draft.detailAddress} maxLength={200} error={errors.detailAddress} placeholder="층·호수 등 상세주소" onChange={event => onChange('detailAddress', event.target.value)} />
    <NumberField format="business" id="owner-businessNumber" onBlur={() => onBlur?.('businessNumber')} label="사업자 번호 *" required value={draft.businessNumber} error={errors.businessNumber} placeholder="예: 000-00-00000" onValueChange={value => onChange('businessNumber', value)} />
    <NumberField format="telephone" id="owner-storePhone" onBlur={() => onBlur?.('storePhone')} label="매장 연락처 *" required value={draft.storePhone} error={errors.storePhone} placeholder="예: 02-123-4567" onValueChange={value => onChange('storePhone', value)} />
    <OwnerNotice><ResponsiveSentences lines={["운영자가 매장 소재지와 관리 권한을 확인해요.","이미 등록된 매장은 중복 등록하지 말고 운영자에게 관리 권한을 문의해 주세요."]}/></OwnerNotice>
    {picker === 'industry' && <IndustryPicker value={draft.industry} onClose={() => setPicker(null)} onConfirm={industry => { onChange('industry', industry); setPicker(null) }} />}
    {picker === 'address' && <AddressPicker search={addressSearch} onClose={() => setPicker(null)} onSelect={result => {
      onChange('postcode', result.postcode); onChange('address', result.address); onChange('detailAddress', ''); setPicker(null)
      requestAnimationFrame(() => document.getElementById('owner-detailAddress')?.focus())
    }} />}
  </>
}

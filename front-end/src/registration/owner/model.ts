export const industries = ['음식점', '카페', '편의점', '기타'] as const
export type Industry = '' | typeof industries[number]
export type OwnerDraft = {
  name: string; phone: string; storeName: string; industry: Industry
  postcode: string; address: string; detailAddress: string; businessNumber: string; storePhone: string
}
export type OwnerErrors = Partial<Record<keyof OwnerDraft, string>>
export const emptyDraft: OwnerDraft = { name: '', phone: '', storeName: '', industry: '', postcode: '', address: '', detailAddress: '', businessNumber: '', storePhone: '' }
export const digits = (value: string) => value.replace(/[\s-]/g, '')
// Format validation only. Registration and operating eligibility are verified by the server.
export function validBusinessNumber(value: string) {
  const number = digits(value)
  if (!/^\d{10}$/.test(number) || /^0+$/.test(number)) return false
  const values = [...number].map(Number)
  const sum = values.slice(0, 9).reduce((total, digit, i) => total + digit * [1, 3, 7, 1, 3, 7, 1, 3, 5][i], 0) + Math.floor(values[8] * 5 / 10)
  return (10 - sum % 10) % 10 === values[9]
}
export function validateOwner(draft: OwnerDraft, step: 1 | 2): OwnerErrors {
  const errors: OwnerErrors = {}
  if (step === 1) {
    if (!draft.name.trim() || draft.name.trim().length > 50) errors.name = '성명을 1~50자로 입력해 주세요.'
    if (!/^(010\d{8}|01[16789]\d{7,8})$/.test(digits(draft.phone))) errors.phone = '올바른 휴대전화 번호를 입력해 주세요.'
  } else {
    if (!draft.storeName.trim() || draft.storeName.trim().length > 100) errors.storeName = '매장명을 1~100자로 입력해 주세요.'
    if (!industries.includes(draft.industry as typeof industries[number])) errors.industry = '업종을 선택해 주세요.'
    if (!/^\d{5}$/.test(draft.postcode) || !draft.address.trim()) errors.address = '주소 검색으로 매장 주소를 선택해 주세요.'
    if (draft.address.length > 200) errors.address = '매장 주소를 200자 이내로 선택해 주세요.'
    if (draft.detailAddress.trim().length > 100) errors.detailAddress = '상세주소를 100자 이내로 입력해 주세요.'
    if (!validBusinessNumber(draft.businessNumber)) errors.businessNumber = '사업자 번호 10자리를 확인해 주세요.'
    if (!/^(02\d{7,8}|0(?:3[123]|4[1234]|5[12345]|6[1234])\d{7,8}|070\d{8}|010\d{8}|01[16789]\d{7,8})$/.test(digits(draft.storePhone))) errors.storePhone = '올바른 매장 연락처를 입력해 주세요.'
  }
  return errors
}
export function normalizeDraft(draft: OwnerDraft): OwnerDraft {
  return Object.fromEntries(Object.entries(draft).map(([key, value]) => [key, ['phone', 'businessNumber', 'storePhone'].includes(key) ? digits(value) : value.trim()])) as OwnerDraft
}
export function isDraft(value: unknown): value is OwnerDraft {
  if (!value || typeof value !== 'object') return false
  const record = value as Record<string, unknown>
  return Object.keys(emptyDraft).every(key => typeof record[key] === 'string' && (record[key] as string).length <= 250)
    && (record.industry === '' || industries.includes(record.industry as typeof industries[number]))
}

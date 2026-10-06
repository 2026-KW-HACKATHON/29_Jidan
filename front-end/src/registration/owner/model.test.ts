import { describe, expect, it } from 'vitest'
import { emptyDraft, isDraft, normalizeDraft, validateOwner, validBusinessNumber } from './model'

describe('점주 가입 검증', () => {
  it('빈 값과 공백만 있는 성명 및 잘못된 연락처를 거부한다', () => {
    expect(Object.keys(validateOwner(emptyDraft, 1))).toEqual(['name', 'phone'])
    expect(validateOwner({ ...emptyDraft, name: '  ', phone: '010abc12345678' }, 1).phone).toBeTruthy()
    expect(validateOwner({ ...emptyDraft, name: '김민수', phone: '010-1234-5678' }, 1)).toEqual({})
  })
  it.each(['', '123-45-6789a', '12345678901'])('잘못된 사업자 번호 %s', value => expect(validBusinessNumber(value)).toBe(false))
  it('명세의 10자리 숫자와 구분 문자를 검증한다', () => expect(validBusinessNumber('220-81-62517')).toBe(true))
  it('주소 검색 결과와 업종이 필요하고 상세주소는 선택이다', () => {
    const draft = { ...emptyDraft, storeName: '매장', industry: '음식점' as const, postcode: '01897', address: '서울 노원구 광운로 20', businessNumber: '2208162517', storePhone: '02-123-4567' }
    expect(validateOwner(draft, 2)).toEqual({})
    expect(validateOwner({ ...draft, postcode: '' }, 2).address).toBeTruthy()
    expect(validateOwner({ ...draft, detailAddress: 'x'.repeat(201) }, 2).detailAddress).toBeTruthy()
    expect(validateOwner({ ...draft, storePhone: '99912345678' }, 2).storePhone).toBeTruthy()
  })
  it('정규화 시 공백과 하이픈만 제거하고 문자를 숨기지 않는다', () => {
    expect(normalizeDraft({ ...emptyDraft, name: ' 김민수 ', phone: '010 - 1234 - 5678' })).toMatchObject({ name: '김민수', phone: '01012345678' })
    expect(isDraft({ ...emptyDraft, industry: 'unknown' })).toBe(false)
    expect(isDraft({ ...emptyDraft, name: 1 })).toBe(false)
  })
})

it('010 번호와 매장 전화번호의 명세 길이를 검증한다', () => {
  for (const phone of ['0101234567', '010123456789', '010-1234-56789']) expect(validateOwner({ ...emptyDraft, name: '김민수', phone }, 1).phone).toBeTruthy()
  expect(validateOwner({ ...emptyDraft, name: '김민수', phone: '01012345678' }, 1)).toEqual({})
  for (const storePhone of ['123456789', '01234567', '012345678901']) expect(validateOwner({ ...emptyDraft, storePhone }, 2).storePhone).toBeTruthy()
})

  it('외국인 성명의 공백, 하이픈, 아포스트로피와 비라틴 문자를 허용한다', () => {
    for (const name of ["Alex Kim", "Anne-Marie O'Neill", 'José García', '王小明', '김하늘빛사랑해', 'A'.repeat(50)]) {
      expect(validateOwner({ ...emptyDraft, name, phone: '01012345678' }, 1).name).toBeUndefined()
    }
    expect(validateOwner({ ...emptyDraft, name: 'A'.repeat(51) }, 1).name).toBeTruthy()
  })

it('사업자 번호와 매장 번호는 명세의 패턴을 따른다',()=>{for(const value of ['0000000000','1234567890'])expect(validBusinessNumber(value)).toBe(true);for(const phone of ['01112345678','0101234567'])expect(validateOwner({...emptyDraft,name:'김',phone},1).phone).toBeTruthy();expect(validateOwner({...emptyDraft,storePhone:'0351234567',detailAddress:'x'.repeat(200)},2).detailAddress).toBeUndefined()})

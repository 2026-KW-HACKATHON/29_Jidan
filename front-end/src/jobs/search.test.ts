import {expect,it} from 'vitest'
import {createHangulSearch} from './search'

it.each(['컴포즈','컴포즈커피','ㅋㅍㅈ','ㅋㅍ','컴ㅍ','커','커ㅍ','ᄏᄑᄌ','컴포즈'.normalize('NFD')])('완성·초성·입력 중 검색 %s',query=>{
  expect(createHangulSearch(query)(['컴포즈커피 광운대점'])).toBe(true)
})
it.each(['gs25','GS25','gs25ㄱㅇ','ㄱㅇㄷ'])('초성 검색에서도 영문·숫자 유지 %s',query=>{
  expect(createHangulSearch(query)(['GS25 광운대점'])).toBe(true)
})
it('띄어쓰기와 대소문자를 정규화하고 모든 검색 단어를 요구한다',()=>{
  expect(createHangulSearch(' ㅋㅍㅈ ㅇㄹ ')(['컴포즈 커피','음료 제조'])).toBe(true)
  expect(createHangulSearch('컴포즈커피')(['컴포즈 커피'])).toBe(true)
  expect(createHangulSearch('ㅋㅍㅈ 없는업무')(['컴포즈커피','음료 제조'])).toBe(false)
  expect(createHangulSearch(' \n\t ')([])).toBe(true)
})
it('완성된 검색어를 무조건 초성으로 바꾸거나 다른 필드 사이를 연결하지 않는다',()=>{
  expect(createHangulSearch('카페')(['커피'])).toBe(false)
  expect(createHangulSearch('없는매장')(['컴포즈커피'])).toBe(false)
  expect(createHangulSearch('ㅏ')(['카페'])).toBe(false)
  expect(createHangulSearch('커피')(['커','피'])).toBe(false)
  expect(createHangulSearch('ㄱㅍ')(['광운대','편의점'])).toBe(false)
})

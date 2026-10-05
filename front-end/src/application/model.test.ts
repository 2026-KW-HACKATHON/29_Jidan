import {expect,it} from 'vitest'
import {introductionCharacters,limitIntroduction,validIntroduction} from './model'
it('공백·개행·보이지 않는 문자만 있는 소개는 제출할 수 없다',()=>{
  for(const text of ['', ' \n\t　', '\u200b\u200c'])expect(validIntroduction(text)).toBe(false)
  expect(validIntroduction('경력이\n있어요')).toBe(true)
})
it('한글·이모지·결합 문자를 같은 글자 단위로 세고 500자에서 제한한다',()=>{
  for(const unit of ['가','🧑‍🍳','e\u0301']) {
    expect(introductionCharacters(unit)).toHaveLength(1)
    expect(validIntroduction(unit.repeat(500))).toBe(true)
    expect(validIntroduction(unit.repeat(501))).toBe(false)
    expect(limitIntroduction(unit.repeat(501))).toBe(unit.repeat(500))
  }
})

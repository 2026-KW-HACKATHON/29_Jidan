import { canBeChoseong, disassemble, getChoseong } from 'es-hangul'

function normalize(value: string) {
  // NFC covers decomposed input from macOS; standalone NFD initials need conversion.
  return value.normalize('NFC').toLocaleLowerCase('ko-KR')
    .replace(/[\u1100-\u1112]/gu, character => getChoseong(character))
    .replace(/\s+/gu, '')
}
/** Every word must match a field. A match never spans unrelated field boundaries. */
export function createHangulSearch(query: string) {
  const terms = query.trim().split(/\s+/u).filter(Boolean).map(value => {
    const text = normalize(value)
    const characters = [...text]
    const initials = characters.some(canBeChoseong) && characters.every(character => canBeChoseong(character) || /[a-z0-9_-]/u.test(character))
    return { text, initials, partial: /[가-힣]/u.test(text) ? disassemble(text) : null }
  })
  return (fields: readonly string[]) => {
    if (!terms.length) return true
    const indexed = fields.map(value => {
      const text = normalize(value)
      return { text, initials: getChoseong(text, { keepNonHangul: true }), partial: disassemble(text) }
    })
    return terms.every(term => indexed.some(field => field.text.includes(term.text)
      || term.initials && field.initials.includes(term.text)
      || term.partial !== null && field.partial.includes(term.partial)))
  }
}

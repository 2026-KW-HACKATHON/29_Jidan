export type NumberFormat = 'mobile' | 'telephone' | 'business'
export const onlyDigits = (value: string) => value.normalize('NFKC').replace(/\D/g, '')
export function formatNumber(value: string, format: NumberFormat) {
  let numbers = onlyDigits(value)
  const prefix = format === 'telephone' && numbers.startsWith('02') ? 2 : 3
  numbers = numbers.slice(0, format === 'business' ? 10 : prefix === 2 ? 10 : 11)
  const middle = format === 'business' ? 2 : numbers.startsWith('010') || numbers.startsWith('070') || numbers.length > prefix + 7 ? 4 : 3
  return [numbers.slice(0, prefix), numbers.slice(prefix, prefix + middle), numbers.slice(prefix + middle)].filter(Boolean).join('-')
}

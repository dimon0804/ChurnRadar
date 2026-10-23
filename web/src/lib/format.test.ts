import { describe, expect, it } from 'vitest'
import {
  countLabel,
  formatAgo,
  formatDate,
  formatDaysToExpiry,
  formatDecimal,
  formatMoney,
  formatMoneyCompact,
  formatPoints,
  formatScore,
  formatShare,
  plural,
} from './format'

// «~» в ожидаемой строке означает неразрывный пробел
const nb = (s: string) => s.replace(/~/g, '\u00a0')

describe('formatMoney', () => {
  it('formats eurocents with ru-RU grouping and euro prefix', () => {
    expect(formatMoney(123456)).toBe(nb('€1~234,56'))
    expect(formatMoney(54987)).toBe('€549,87')
    expect(formatMoney(100)).toBe('€1,00')
    expect(formatMoney(5)).toBe('€0,05')
    expect(formatMoney(0)).toBe('€0,00')
    expect(formatMoney(123456789012)).toBe(nb('€1~234~567~890,12'))
  })

  it('uses a typographic minus for negatives and never shows -0', () => {
    expect(formatMoney(-1200)).toBe('−€12,00')
    expect(formatMoney(-0)).toBe('€0,00')
    expect(formatMoney(-0.4)).toBe('€0,00')
  })

  it('can drop cents and add an explicit plus', () => {
    expect(formatMoney(123456, { cents: false })).toBe(nb('€1~235'))
    expect(formatMoney(1500, { signed: true })).toBe('+€15,00')
    expect(formatMoney(0, { signed: true })).toBe('€0,00')
  })

  it('rounds fractional cents', () => {
    expect(formatMoney(1234.6)).toBe('€12,35')
  })

  it('never leaks undefined or NaN', () => {
    expect(formatMoney(undefined)).toBe('—')
    expect(formatMoney(null)).toBe('—')
    expect(formatMoney(Number.NaN)).toBe('—')
    expect(formatMoney(Number.POSITIVE_INFINITY)).toBe('—')
  })
})

describe('formatMoneyCompact', () => {
  it('shortens large amounts', () => {
    expect(formatMoneyCompact(95000)).toBe('€950')
    expect(formatMoneyCompact(1_240_000)).toBe(nb('€12,4~тыс.'))
    expect(formatMoneyCompact(120_000_000)).toBe(nb('€1,2~млн'))
    expect(formatMoneyCompact(undefined)).toBe('—')
  })
})

describe('numbers', () => {
  it('formatDecimal trims zeros and groups thousands', () => {
    expect(formatDecimal(2.4)).toBe('2,4')
    expect(formatDecimal(2)).toBe('2')
    expect(formatDecimal(12345.67, 2)).toBe(nb('12~345,67'))
    expect(formatDecimal(-0.01)).toBe('0')
    expect(formatDecimal(-3.26, 1)).toBe('−3,3')
  })

  it('formatShare renders percent with a space', () => {
    expect(formatShare(0.123)).toBe(nb('12,3~%'))
    expect(formatShare(1)).toBe(nb('100~%'))
    expect(formatShare(undefined)).toBe('—')
  })

  it('formatScore clamps and rounds, dash for unknown', () => {
    expect(formatScore(78.4)).toBe('78')
    expect(formatScore(78.5)).toBe('79')
    expect(formatScore(120)).toBe('100')
    expect(formatScore(null)).toBe('—')
  })

  it('formatPoints marks positive contributions', () => {
    expect(formatPoints(31)).toBe('+31')
    expect(formatPoints(0)).toBe('0')
    expect(formatPoints(-4.26)).toBe('−4,3')
  })
})

describe('plural', () => {
  const forms = ['клиент', 'клиента', 'клиентов'] as const
  it.each([
    [1, 'клиент'],
    [2, 'клиента'],
    [5, 'клиентов'],
    [11, 'клиентов'],
    [12, 'клиентов'],
    [21, 'клиент'],
    [22, 'клиента'],
    [111, 'клиентов'],
    [0, 'клиентов'],
  ])('%i -> %s', (n, expected) => {
    expect(plural(n, forms)).toBe(expected)
  })

  it('countLabel groups digits', () => {
    expect(countLabel(1021, forms)).toBe(nb('1~021~клиент'))
    expect(countLabel(undefined, forms)).toBe('—')
  })
})

describe('dates', () => {
  const now = new Date('2026-10-24T12:00:00')

  it('formats ISO dates in Russian', () => {
    expect(formatDate('2026-10-20', { now })).toBe(nb('20~окт 2026'))
    expect(formatDate('2026-10-20', { short: true, now })).toBe(nb('20~окт'))
    expect(formatDate('2025-05-01', { short: true, now })).toBe(nb('1~мая 2025'))
    expect(formatDate('not a date')).toBe('—')
    expect(formatDate(null)).toBe('—')
  })

  it('formatAgo', () => {
    const t = 1_000_000
    expect(formatAgo(t, t + 300)).toBe(nb('0,3~с назад'))
    expect(formatAgo(t, t + 12_400)).toBe(nb('12~с назад'))
    expect(formatAgo(t, t + 5 * 60_000)).toBe(nb('5~мин назад'))
    expect(formatAgo(t, t + 3 * 3_600_000)).toBe(nb('3~ч назад'))
    expect(formatAgo(t, t - 500)).toBe(nb('0~с назад'))
    expect(formatAgo(null)).toBe('—')
  })

  it('formatDaysToExpiry', () => {
    expect(formatDaysToExpiry(2)).toBe(nb('через 2~дня'))
    expect(formatDaysToExpiry(0.2)).toBe('истекает сегодня')
    expect(formatDaysToExpiry(-5)).toBe(nb('истекла 5~дней назад'))
    expect(formatDaysToExpiry(null)).toBe('—')
  })
})

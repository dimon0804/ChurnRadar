export const NBSP = '\u00a0'
const MINUS = '−'
export const DASH = '—'

function isFiniteNumber(value: unknown): value is number {
  return typeof value === 'number' && Number.isFinite(value)
}

function groupDigits(digits: string): string {
  return digits.replace(/\B(?=(\d{3})+(?!\d))/g, NBSP)
}

export interface MoneyOptions {
  /** Показывать копейки (центы). По умолчанию да. */
  cents?: boolean
  /** Ставить «+» перед положительными суммами. */
  signed?: boolean
}

/**
 * Евроценты -> «€1 234,56». Разделитель групп — неразрывный пробел (как в ru-RU),
 * знак евро впереди, отрицательные суммы через типографский минус: «−€12,00».
 */
export function formatMoney(cents: number | null | undefined, options: MoneyOptions = {}): string {
  if (!isFiniteNumber(cents)) return DASH
  const { cents: withCents = true, signed = false } = options
  const rounded = Math.round(cents)
  const negative = rounded < 0
  const abs = Math.abs(rounded)

  let body: string
  if (withCents) {
    const euros = Math.floor(abs / 100)
    const rest = abs % 100
    body = `${groupDigits(String(euros))},${String(rest).padStart(2, '0')}`
  } else {
    body = groupDigits(String(Math.round(abs / 100)))
  }

  const isZero = body.replace(/[^1-9]/g, '') === ''
  const sign = negative && !isZero ? MINUS : signed && !isZero ? '+' : ''
  return `${sign}€${body}`
}

/** Компактная сумма для осей графиков: €950, €12,4 тыс., €1,2 млн. */
export function formatMoneyCompact(cents: number | null | undefined): string {
  if (!isFiniteNumber(cents)) return DASH
  const euros = cents / 100
  const abs = Math.abs(euros)
  const sign = euros < 0 ? MINUS : ''
  if (abs >= 1_000_000) return `${sign}€${formatDecimal(abs / 1_000_000, 1)}${NBSP}млн`
  if (abs >= 10_000) return `${sign}€${formatDecimal(abs / 1_000, 1)}${NBSP}тыс.`
  return formatMoney(cents, { cents: false })
}

export function formatDecimal(value: number | null | undefined, maxFractionDigits = 1): string {
  if (!isFiniteNumber(value)) return DASH
  const factor = 10 ** maxFractionDigits
  const rounded = Math.round(value * factor) / factor
  const negative = rounded < 0
  const [intPart = '0', fracPart] = Math.abs(rounded).toFixed(maxFractionDigits).split('.')
  const frac = fracPart?.replace(/0+$/, '')
  const body = groupDigits(intPart) + (frac ? `,${frac}` : '')
  return (negative && rounded !== 0 ? MINUS : '') + body
}

export function formatInt(value: number | null | undefined): string {
  if (!isFiniteNumber(value)) return DASH
  return formatDecimal(Math.round(value), 0)
}

/** Доля 0..1 -> «12,3 %». */
export function formatShare(share: number | null | undefined, digits = 1): string {
  if (!isFiniteNumber(share)) return DASH
  return `${formatDecimal(share * 100, digits)}${NBSP}%`
}

/** Score 0..100 -> целое, «—» если оценки нет. */
export function formatScore(score: number | null | undefined): string {
  if (!isFiniteNumber(score)) return DASH
  return String(Math.round(Math.min(100, Math.max(0, score))))
}

export function formatPoints(points: number | null | undefined): string {
  if (!isFiniteNumber(points)) return DASH
  const text = formatDecimal(points, 1)
  return points > 0.05 ? `+${text}` : text
}

/** Русское склонение: plural(5, ['клиент', 'клиента', 'клиентов']). */
export function plural(n: number, forms: readonly [string, string, string]): string {
  const abs = Math.abs(Math.trunc(n))
  const mod10 = abs % 10
  const mod100 = abs % 100
  if (mod10 === 1 && mod100 !== 11) return forms[0]
  if (mod10 >= 2 && mod10 <= 4 && (mod100 < 12 || mod100 > 14)) return forms[1]
  return forms[2]
}

export function countLabel(n: number | null | undefined, forms: readonly [string, string, string]): string {
  if (!isFiniteNumber(n)) return DASH
  return `${formatInt(n)}${NBSP}${plural(n, forms)}`
}

const MONTHS_SHORT = ['янв', 'фев', 'мар', 'апр', 'мая', 'июн', 'июл', 'авг', 'сен', 'окт', 'ноя', 'дек']

function parseDate(value: string | null | undefined): Date | null {
  if (!value) return null
  const date = /^\d{4}-\d{2}-\d{2}$/.test(value) ? new Date(`${value}T00:00:00`) : new Date(value)
  return Number.isNaN(date.getTime()) ? null : date
}

/** «24 окт 2026», без года для текущего года при short=true. */
export function formatDate(value: string | null | undefined, opts: { short?: boolean; now?: Date } = {}): string {
  const date = parseDate(value)
  if (!date) return DASH
  const now = opts.now ?? new Date()
  const base = `${date.getDate()}${NBSP}${MONTHS_SHORT[date.getMonth()]}`
  if (opts.short && date.getFullYear() === now.getFullYear()) return base
  return `${base} ${date.getFullYear()}`
}

export function formatDateTime(value: string | null | undefined, opts: { now?: Date } = {}): string {
  const date = parseDate(value)
  if (!date) return DASH
  const hh = String(date.getHours()).padStart(2, '0')
  const mm = String(date.getMinutes()).padStart(2, '0')
  return `${formatDate(value, { short: true, now: opts.now })}, ${hh}:${mm}`
}

/** Сколько прошло: «0,3 с назад», «12 с назад», «5 мин назад», «2 ч назад». */
export function formatAgo(fromMs: number | null | undefined, nowMs: number = Date.now()): string {
  if (!isFiniteNumber(fromMs)) return DASH
  const seconds = Math.max(0, (nowMs - fromMs) / 1000)
  if (seconds < 10) return `${formatDecimal(seconds, 1)}${NBSP}с назад`
  if (seconds < 60) return `${Math.floor(seconds)}${NBSP}с назад`
  const minutes = Math.floor(seconds / 60)
  if (minutes < 60) return `${minutes}${NBSP}мин назад`
  const hours = Math.floor(minutes / 60)
  if (hours < 24) return `${hours}${NBSP}ч назад`
  const days = Math.floor(hours / 24)
  return `${days}${NBSP}${plural(days, ['день', 'дня', 'дней'])} назад`
}

/** Дни до истечения: «через 3 дня», «сегодня», «истекла 2 дня назад». */
export function formatDaysToExpiry(days: number | null | undefined): string {
  if (!isFiniteNumber(days)) return DASH
  const whole = Math.round(days)
  if (whole === 0) return days < 0 ? 'истекла сегодня' : 'истекает сегодня'
  const forms = ['день', 'дня', 'дней'] as const
  if (whole > 0) return `через ${whole}${NBSP}${plural(whole, forms)}`
  return `истекла ${-whole}${NBSP}${plural(-whole, forms)} назад`
}

export function formatDuration(ms: number | null | undefined): string {
  if (!isFiniteNumber(ms)) return DASH
  if (ms < 1000) return `${Math.round(ms)}${NBSP}мс`
  return `${formatDecimal(ms / 1000, 1)}${NBSP}с`
}

/** Компактное число для осей графиков: 950, 1 600, 12 тыс., 1,2 млн. */
export function formatCompact(value: number | null | undefined): string {
  if (!isFiniteNumber(value)) return DASH
  const abs = Math.abs(value)
  const sign = value < 0 ? MINUS : ''
  if (abs >= 1_000_000) return `${sign}${formatDecimal(abs / 1_000_000, 1)}${NBSP}млн`
  if (abs >= 10_000) return `${sign}${formatDecimal(abs / 1_000, abs >= 100_000 ? 0 : 1)}${NBSP}тыс.`
  return formatDecimal(value, abs < 10 ? 1 : 0)
}

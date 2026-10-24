import { NBSP } from './format'

/**
 * На какой момент посчитаны данные.
 *
 * Данные кейса — снимок на фиксированный момент (reference_time), и все сроки, «сегодня» и
 * «деньги закончатся» считаются от него, а не от текущей даты. Если момент не задан, сервер
 * считает от текущего времени — это «данные в реальном времени».
 */
export type DataClock =
  | { kind: 'snapshot'; at: Date; precise: boolean }
  | { kind: 'live' }

const DAY_MS = 86_400_000
const MINUTE_MS = 60_000
/** Если момент данных отличается от «сейчас» меньше чем на это, считаем, что данные живые. */
const LIVE_TOLERANCE_MS = 2 * 60 * 60 * 1000

function toDate(value: string | null | undefined): Date | null {
  if (!value) return null
  const d = new Date(value)
  return Number.isNaN(d.getTime()) ? null : d
}

function localDay(ms: number): string {
  const d = new Date(ms)
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`
}

/** Явный reference_time из ответа API: строка — снимок, null — живые данные, undefined — поля нет. */
export function clockFromReference(reference: string | null | undefined): DataClock | null {
  if (reference === null) return { kind: 'live' }
  const at = toDate(reference)
  return at ? { kind: 'snapshot', at, precise: true } : null
}

/**
 * По таймлайну сводки: последний день факта — день, на который сняты данные. Если он раньше
 * сегодняшнего (с запасом в сутки на часовые пояса), данные — снимок на этот день.
 */
export function clockFromTimeline(
  timeline: ReadonlyArray<{ date: string; kind: string }> | null | undefined,
  nowMs: number = Date.now(),
): DataClock | null {
  const facts = (timeline ?? []).filter((p) => p.kind === 'fact' && /^\d{4}-\d{2}-\d{2}$/.test(p.date))
  if (facts.length === 0) return null
  const last = facts.map((p) => p.date).sort().at(-1) as string
  const yesterday = localDay(nowMs - DAY_MS)
  if (last >= yesterday) return { kind: 'live' }
  const [y, m, d] = last.split('-').map(Number) as [number, number, number]
  return { kind: 'snapshot', at: new Date(y, m - 1, d), precise: false }
}

/**
 * По услугам клиента: expires_at − days_to_expiry = момент, от которого сервер считал сроки.
 * Берём самый частый результат (с точностью до минуты), чтобы округление дней не сбивало время.
 */
export function clockFromServices(
  services: ReadonlyArray<{ expires_at: string; days_to_expiry?: number | null }> | null | undefined,
  nowMs: number = Date.now(),
): DataClock | null {
  const candidates: number[] = []
  for (const s of services ?? []) {
    const expires = toDate(s.expires_at)
    if (!expires || typeof s.days_to_expiry !== 'number' || !Number.isFinite(s.days_to_expiry)) continue
    const at = expires.getTime() - s.days_to_expiry * DAY_MS
    candidates.push(Math.round(at / MINUTE_MS) * MINUTE_MS)
  }
  if (candidates.length === 0) return null

  const counts = new Map<number, number>()
  for (const c of candidates) counts.set(c, (counts.get(c) ?? 0) + 1)
  let best = candidates[0] as number
  let bestCount = 0
  for (const [value, count] of counts) {
    if (count > bestCount || (count === bestCount && value < best)) {
      best = value
      bestCount = count
    }
  }
  if (bestCount === 1) {
    const sorted = [...candidates].sort((a, b) => a - b)
    best = sorted[Math.floor(sorted.length / 2)] as number
  }

  if (Math.abs(best - nowMs) < LIVE_TOLERANCE_MS) return { kind: 'live' }
  return { kind: 'snapshot', at: new Date(best), precise: bestCount > 1 }
}

/** Первый найденный источник побеждает: явное поле API, потом услуги, потом таймлайн. */
export function resolveDataClock(...sources: Array<DataClock | null | undefined>): DataClock | null {
  return sources.find((s): s is DataClock => Boolean(s)) ?? null
}

function pad(n: number): string {
  return String(n).padStart(2, '0')
}

/**
 * «19.10.2026, 15:00 UTC» для точного момента и «19.10.2026» для дня.
 * Время показываем в UTC: так момент снимка совпадает с данными кейса в любом часовом поясе.
 */
export function formatClockMoment(clock: Extract<DataClock, { kind: 'snapshot' }>): string {
  const d = clock.at
  if (!clock.precise) return `${pad(d.getDate())}.${pad(d.getMonth() + 1)}.${d.getFullYear()}`
  const date = `${pad(d.getUTCDate())}.${pad(d.getUTCMonth() + 1)}.${d.getUTCFullYear()}`
  return `${date}, ${pad(d.getUTCHours())}:${pad(d.getUTCMinutes())}${NBSP}UTC`
}

export function dataClockLabel(clock: DataClock): string {
  if (clock.kind === 'live') return 'Данные в реальном времени'
  return `Данные на ${formatClockMoment(clock)} — момент снимка`
}

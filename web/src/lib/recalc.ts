import { countLabel, formatInt, formatMoney } from './format'

export interface RecalcSummary {
  customers_total?: number
  by_level: { high?: number }
  expected_loss: number
}

export interface RecalcEventLike {
  model_version: number
  changed_customer_ids: readonly number[]
  full?: boolean
  summary?: RecalcSummary | null
}

export interface RecalcNotice {
  title: string
  detail: string | null
}

/** Сколько клиентов затронул пересчёт; null — пересчитаны все (full без списка). */
export function changedCount(event: RecalcEventLike): number | null {
  if (event.changed_customer_ids.length > 0) return event.changed_customer_ids.length
  return event.full ? null : 0
}

/**
 * Текст ненавязчивого уведомления о пересчёте.
 * Возвращает null, если пересчёт ничего не поменял — тогда и показывать нечего.
 */
export function describeRecalc(event: RecalcEventLike, before?: RecalcSummary | null): RecalcNotice | null {
  const count = changedCount(event)
  const after = event.summary && event.summary.customers_total !== 0 ? event.summary : null
  const deltas: string[] = []
  if (before && after) {
    const hb = before.by_level.high
    const ha = after.by_level.high
    if (typeof hb === 'number' && typeof ha === 'number' && hb !== ha) {
      deltas.push(`высокий риск ${formatInt(hb)} → ${formatInt(ha)}`)
    }
    if (Math.round(before.expected_loss / 100) !== Math.round(after.expected_loss / 100)) {
      deltas.push(
        `потеря ${formatMoney(before.expected_loss, { cents: false })} → ${formatMoney(after.expected_loss, { cents: false })}`,
      )
    }
  }
  if (count === 0 && deltas.length === 0) return null

  const who =
    count === null
      ? 'пересчитаны все клиенты'
      : `изменил${count === 1 ? 'ся' : 'ось'} ${countLabel(count, ['клиент', 'клиента', 'клиентов'])}`
  const detail = deltas.length > 0 ? deltas.join(' · ') : null
  return {
    title: `Оценки пересчитаны: модель v${event.model_version}, ${who}`,
    detail: detail ? detail.charAt(0).toUpperCase() + detail.slice(1) : null,
  }
}

/**
 * Какие строки поменялись между двумя загрузками одной и той же страницы таблицы.
 * Сравниваем только тех, кто был и до, и после: переход по страницам или сортировка
 * подсвечивать ничего не должны.
 */
export function diffRows<T extends { id: number }>(
  prev: ReadonlyMap<number, string>,
  items: readonly T[],
  signature: (item: T) => string,
): { changed: number[]; next: Map<number, string> } {
  const next = new Map<number, string>()
  const changed: number[] = []
  for (const item of items) {
    const sig = signature(item)
    next.set(item.id, sig)
    const old = prev.get(item.id)
    if (old !== undefined && old !== sig) changed.push(item.id)
  }
  return { changed, next }
}

/** Направление изменения score: для стрелки ↑/↓ рядом с оценкой. */
export function scoreTrend(before: number | null | undefined, after: number | null | undefined): 'up' | 'down' | null {
  if (typeof before !== 'number' || typeof after !== 'number') return null
  const diff = Math.round(after) - Math.round(before)
  if (diff === 0) return null
  return diff > 0 ? 'up' : 'down'
}

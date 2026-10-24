import type { ModelSettingsPreview, PreviewCustomerChange, RiskLevel } from '@/api/types'
import { formatDecimal, formatInt, formatMoney, NBSP, plural } from './format'
import { RISK_LABEL } from './labels'

export type MoveKind = 'into_high' | 'out_of_high' | 'other'

/** Куда движется клиент: в высокий риск, из него или между остальными уровнями. */
export function moveKind(change: Pick<PreviewCustomerChange, 'level_before' | 'level_after'>): MoveKind {
  if (change.level_after === 'high') return 'into_high'
  if (change.level_before === 'high') return 'out_of_high'
  return 'other'
}

export function moveText(change: Pick<PreviewCustomerChange, 'level_before' | 'level_after'>): string {
  return `${RISK_LABEL[change.level_before as RiskLevel]} → ${RISK_LABEL[change.level_after as RiskLevel]}`
}

/** Разница в евро со знаком: «+€12», «−€87». Меньше полуевро — не показываем. */
export function moneyDelta(before: number, after: number): string | null {
  const delta = after - before
  if (Math.abs(delta) < 50) return null
  return formatMoney(delta, { cents: false, signed: true })
}

/** Разница долей в процентных пунктах: «+1,2 п.п.». */
export function shareDelta(before: number, after: number): string | null {
  const delta = after - before
  if (Math.abs(delta) < 0.0005) return null
  return `${delta > 0 ? '+' : '−'}${formatDecimal(Math.abs(delta) * 100, 1)}${NBSP}п.п.`
}

export function countDelta(before: number, after: number): string | null {
  const delta = after - before
  if (delta === 0) return null
  return `${delta > 0 ? '+' : '−'}${formatInt(Math.abs(delta))}`
}

export function scoreDelta(before: number | null, after: number | null): string | null {
  if (before === null || after === null) return null
  const delta = Math.round(after) - Math.round(before)
  if (delta === 0) return null
  return `${delta > 0 ? '+' : '−'}${Math.abs(delta)}`
}

/** Честная подпись, когда превью посчитано по выборке. */
export function approximateNote(p: Pick<ModelSettingsPreview, 'exact' | 'sample_size' | 'customers_total'>): string | null {
  if (p.exact) return null
  return (
    `Клиентов больше ${formatInt(p.sample_size)}, поэтому посчитали выборку из ${formatInt(p.sample_size)} ` +
    `${plural(p.sample_size, ['клиента', 'клиентов', 'клиентов'])} и растянули цифры на все ${formatInt(p.customers_total)}. ` +
    'Итог приблизительный, а в списке только клиенты из выборки. Точные цифры будут после «Применить».'
  )
}

/** Сводка движения одной строкой: «2 попадут в высокий риск, 1 выйдет из него». */
export function movesSummary(p: Pick<ModelSettingsPreview, 'changes_total' | 'into_high' | 'out_of_high'>): string {
  if (p.changes_total === 0) return 'Ни один клиент не сменит уровень риска'
  const parts: string[] = []
  if (p.into_high) parts.push(`${formatInt(p.into_high)} ${plural(p.into_high, ['попадёт', 'попадут', 'попадут'])} в высокий риск`)
  if (p.out_of_high) parts.push(`${formatInt(p.out_of_high)} ${plural(p.out_of_high, ['выйдет', 'выйдут', 'выйдут'])} из высокого`)
  const other = p.changes_total - p.into_high - p.out_of_high
  if (other) parts.push(`${formatInt(other)} ${plural(other, ['перейдёт', 'перейдут', 'перейдут'])} между средним и низким`)
  return parts.join(', ')
}

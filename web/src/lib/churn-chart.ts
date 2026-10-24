import type { Summary, TimelinePoint } from '@/api/types'
import { formatCompact, formatDate, formatDecimal, formatInt, plural } from '@/lib/format'

export type DeletedServices = NonNullable<Summary['deleted_services']>

export interface ChurnRow {
  date: string
  kind: 'fact' | 'forecast'
  /** Ушло услуг за день (факт), всего. */
  fact: number | null
  /** Из них с замеченной датой удаления — сплошной столбец. */
  factExact: number | null
  /** Из них с оценочной датой — бледный столбец поверх. */
  factEstimated: number | null
  forecast: number | null
  atRisk: number | null
  loss: number | null
}

const SERVICES = ['услуга', 'услуги', 'услуг'] as const

function num(v: unknown): number | null {
  return typeof v === 'number' && Number.isFinite(v) ? v : null
}

export function buildRows(timeline: readonly TimelinePoint[]): ChurnRow[] {
  const sorted = [...timeline].filter((p) => typeof p.date === 'string').sort((a, b) => a.date.localeCompare(b.date))
  return sorted.map((p) => {
    const isFact = p.kind !== 'forecast'
    const fact = isFact ? num(p.deletions) : null
    const estimated = fact === null ? null : Math.min(fact, Math.max(0, num(p.estimated_deletions) ?? 0))
    return {
      date: p.date,
      kind: isFact ? 'fact' : 'forecast',
      fact,
      factExact: fact === null || estimated === null ? null : fact - estimated,
      factEstimated: estimated,
      forecast: isFact ? null : num(p.expected_nonrenewals),
      atRisk: num(p.at_risk_services),
      loss: isFact ? null : num(p.expected_loss),
    }
  })
}

export interface ChurnStats {
  factDays: number
  forecastDays: number
  firstDate: string | null
  /** Последний день факта — день снимка данных, граница с прогнозом. */
  snapshotDate: string | null
  firstForecast: string | null
  lastDate: string | null
  deletions: number
  estimatedDeletions: number
  expectedNonrenewals: number
  atRiskNow: number | null
  atRiskEnd: number | null
  hasForecast: boolean
  hasEstimated: boolean
}

export function churnStats(rows: readonly ChurnRow[]): ChurnStats {
  const fact = rows.filter((r) => r.kind === 'fact')
  const forecast = rows.filter((r) => r.kind === 'forecast')
  const withRisk = rows.filter((r) => r.atRisk !== null)
  const estimatedDeletions = fact.reduce((s, r) => s + (r.factEstimated ?? 0), 0)
  return {
    factDays: fact.length,
    forecastDays: forecast.length,
    firstDate: rows[0]?.date ?? null,
    snapshotDate: fact.at(-1)?.date ?? null,
    firstForecast: forecast[0]?.date ?? null,
    lastDate: rows.at(-1)?.date ?? null,
    deletions: fact.reduce((s, r) => s + (r.fact ?? 0), 0),
    estimatedDeletions,
    expectedNonrenewals: forecast.reduce((s, r) => s + (r.forecast ?? 0), 0),
    atRiskNow: withRisk[0]?.atRisk ?? null,
    atRiskEnd: withRisk.at(-1)?.atRisk ?? null,
    hasForecast: forecast.some((r) => r.forecast !== null),
    hasEstimated: estimatedDeletions > 0,
  }
}

/** Ширина оси под самую длинную подпись, чтобы «1 600» не превращалось в «|600». */
export function axisWidth(max: number): number {
  // recharts округляет верх шкалы вверх, берём с запасом
  const label = formatCompact(Math.ceil(Math.max(0, max) * 1.25))
  return Math.max(32, Math.round(label.length * 7.5 + 12))
}

export function countAxisWidth(rows: readonly ChurnRow[]): number {
  return axisWidth(Math.max(0, ...rows.map((r) => Math.max(r.fact ?? 0, r.forecast ?? 0))))
}

export function riskAxisWidth(rows: readonly ChurnRow[]): number {
  return axisWidth(Math.max(0, ...rows.map((r) => r.atRisk ?? 0)))
}

export interface FactGapNote {
  title: string
  text: string
}

/**
 * Подпись для пустой зоны факта: почему столбцов нет и что с этим делать.
 * null — удаления в окне есть, подпись не нужна.
 */
export function factGapNote(stats: ChurnStats, deleted: DeletedServices | null | undefined): FactGapNote | null {
  if (stats.factDays === 0 || stats.deletions > 0) return null
  const days = `${stats.factDays} ${plural(stats.factDays, ['день', 'дня', 'дней'])}`
  if (!deleted || deleted.total === 0) {
    return {
      title: 'В данных нет истории удалений',
      text: 'Факт начнёт копиться с момента загрузки.',
    }
  }
  const when = deleted.last_date
    ? `, последнее — ${deleted.last_estimated ? 'примерно ' : ''}${formatDate(deleted.last_date)}${deleted.last_estimated ? ' (оценка: окончание оплаты плюс 5 дней)' : ''}`
    : ''
  return {
    title: `За ${days} удалений нет`,
    text: `В данных ${formatInt(deleted.total)} ${plural(deleted.total, ['удалённая услуга', 'удалённые услуги', 'удалённых услуг'])}${when}, это раньше окна графика. Новые удаления начнут копиться с момента загрузки.`,
  }
}

export function formatForecast(v: number | null): string {
  if (v === null) return '—'
  if (v === 0) return '0'
  // доли меньше 0,05 при одном знаке превратились бы в «≈0», а непродление всё-таки возможно
  return `≈${formatDecimal(v, Math.abs(v) < 0.05 ? 2 : 1)}`
}

/** Высота прогнозного столбца: нулевые и почти нулевые дни без столбца, иначе обводка ляжет пунктиром на ось. */
export function forecastBar(row: ChurnRow): number | null {
  return row.forecast !== null && row.forecast >= 0.01 ? row.forecast : null
}

/** Текст для скринридера: что на графике, одной фразой на серию. */
export function chartAriaLabel(stats: ChurnStats, horizonDays?: number): string {
  const parts = [
    `График оттока: удаления за последние ${stats.factDays} ${plural(stats.factDays, ['день', 'дня', 'дней'])} и прогноз на ${horizonDays ?? stats.forecastDays} ${plural(horizonDays ?? stats.forecastDays, ['день', 'дня', 'дней'])}.`,
    `Ушло услуг: ${formatInt(stats.deletions)}${stats.hasEstimated ? `, из них у ${formatInt(stats.estimatedDeletions)} дата оценочная` : ''}.`,
  ]
  if (stats.hasForecast) parts.push(`Ожидаем непродлений: ${formatForecast(stats.expectedNonrenewals)}.`)
  if (stats.atRiskNow !== null) {
    parts.push(
      `Под риском на момент снимка ${formatInt(stats.atRiskNow)} ${plural(stats.atRiskNow, SERVICES)}` +
        (stats.atRiskEnd !== null && stats.atRiskEnd !== stats.atRiskNow ? `, к концу прогноза ${formatInt(stats.atRiskEnd)}.` : '.'),
    )
  }
  parts.push('Все числа по дням — по кнопке «Таблица».')
  return parts.join(' ')
}

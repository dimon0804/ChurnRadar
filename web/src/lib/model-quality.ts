import type { ModelQuality, ModelSettings, QualityScenario, Summary } from '@/api/types'
import { DASH, formatDecimal } from '@/lib/format'

export type ScenarioKey = 'stream' | 'calendar'
export type MethodKey = 'model' | 'autoprolong_off' | 'prolong_wait' | 'random' | 'segment_oracle'
export type RocMethod = Exclude<MethodKey, 'random'>

export const SCENARIOS: readonly ScenarioKey[] = ['stream', 'calendar']

export const SCENARIO_LABEL: Record<ScenarioKey, string> = {
  stream: 'Живой поток',
  calendar: 'Продления по сроку',
}

export const SCENARIO_HINT: Record<ScenarioKey, string> = {
  stream:
    'Тот же поток событий, что крутит симулятор на стенде: продления, пополнения и удаления выпадают случайным услугам, не глядя на дату окончания срока.',
  calendar:
    'Услуга продлевается ровно в день окончания срока. Заплатит клиент, пополнит баланс или откажется, решает скрытое поведение его сегмента, которого модель не видит.',
}

export const METHOD_LABEL: Record<MethodKey, string> = {
  model: 'Модель (risk-score)',
  autoprolong_off: 'Правило «автопродление выключено»',
  prolong_wait: 'Правило «ждёт оплаты»',
  random: 'Случайный порядок',
  segment_oracle: 'Скрытый сегмент (модели недоступен)',
}

export const METHOD_ORDER: readonly MethodKey[] = ['model', 'autoprolong_off', 'prolong_wait', 'random', 'segment_oracle']
export const ROC_ORDER: readonly RocMethod[] = ['model', 'autoprolong_off', 'prolong_wait', 'segment_oracle']

/** «×3,3» — во сколько раз чаще среднего. */
export function formatLift(lift: number | null | undefined): string {
  if (typeof lift !== 'number' || !Number.isFinite(lift)) return DASH
  return `×${formatDecimal(lift, lift < 1 ? 2 : 1)}`
}

/** AUC с двумя знаками через запятую: 0,77. */
export function formatAuc(value: number | null | undefined): string {
  if (typeof value !== 'number' || !Number.isFinite(value)) return DASH
  return value.toFixed(2).replace('.', ',')
}

export function levelRow(scenario: QualityScenario, level: 'low' | 'medium' | 'high' | 'all') {
  return scenario.levels.find((row) => row.level === level)
}

/** Диапазон значения по двум сценариям: «0,77–0,93» или одно число, если совпало. */
export function scenarioRange(values: readonly (number | null | undefined)[], format: (v: number) => string): string {
  const nums = values.filter((v): v is number => typeof v === 'number' && Number.isFinite(v))
  if (nums.length === 0) return DASH
  const lo = Math.min(...nums)
  const hi = Math.max(...nums)
  const a = format(lo)
  const b = format(hi)
  return a === b ? a : `${a}–${b}`
}

export interface Headline {
  aucRange: string
  liftRange: string
  precisionRange: string
  accounts: number
}

export function headline(q: ModelQuality): Headline {
  const sc = SCENARIOS.map((k) => q.scenarios[k])
  return {
    aucRange: scenarioRange(sc.map((s) => s.ranking.churned.model.roc_auc), formatAuc),
    liftRange: scenarioRange(sc.map((s) => levelRow(s, 'high')?.churn_lift), (v) => formatDecimal(v, 1)),
    precisionRange: scenarioRange(sc.map((s) => s.thresholds.high.churned.precision), (v) => formatDecimal(v * 100, 0)),
    accounts: q.params.accounts,
  }
}

export interface LiftRow {
  level: 'low' | 'medium' | 'high'
  lift: number | null
  rate: number | null
  n: number
}

export function liftRows(scenario: QualityScenario, target: 'churned' | 'lost_any' = 'churned'): LiftRow[] {
  return (['low', 'medium', 'high'] as const).map((level) => {
    const row = levelRow(scenario, level)
    return {
      level,
      lift: (target === 'churned' ? row?.churn_lift : row?.lost_any_lift) ?? null,
      rate: (target === 'churned' ? row?.churn_rate : row?.lost_any_rate) ?? null,
      n: row?.n ?? 0,
    }
  })
}

export type RocRow = { fpr: number; random: number } & Partial<Record<RocMethod, number>>

export function rocRows(scenario: QualityScenario): RocRow[] {
  const { fpr, tpr } = scenario.roc
  return fpr.map((x, i) => {
    const row: RocRow = { fpr: x, random: x }
    for (const m of ROC_ORDER) {
      const v = tpr[m]?.[i]
      if (typeof v === 'number') row[m] = v
    }
    return row
  })
}

export interface CalibrationRow {
  group: number
  label: string
  predicted: number | null
  actual: number | null
  n: number
}

export function calibrationRows(scenario: QualityScenario): CalibrationRow[] {
  return scenario.calibration.map((row) => ({
    group: row.group,
    label: `${Math.round(row.score_min)}–${Math.round(row.score_max)}`,
    predicted: row.predicted_share,
    actual: row.actual_share,
    n: row.n,
  }))
}

export interface ScoreBin {
  from: number
  to: number
  mid: number
  count: number
}

/** Накопленные корзины summary.risk_scores (le 10, 20, …, 100) -> клиентов в каждом интервале. */
export function scoreHistogram(riskScores: Summary['risk_scores'] | null | undefined): ScoreBin[] {
  const buckets = [...(riskScores?.buckets ?? [])]
    .filter((b) => Number.isFinite(b.le) && b.le <= 100)
    .sort((a, b) => a.le - b.le)
  let prevLe = 0
  let prevCount = 0
  return buckets.map((b) => {
    const bin = { from: prevLe, to: b.le, mid: (prevLe + b.le) / 2, count: Math.max(0, b.count - prevCount) }
    prevLe = b.le
    prevCount = b.count
    return bin
  })
}

/**
 * Чем текущие настройки модели отличаются от тех, на которых шла проверка.
 * Пустой список — проверка относится ровно к текущим весам и порогам.
 */
export function settingsDrift(q: ModelQuality, s: ModelSettings | null | undefined): string[] {
  if (!s) return []
  const out: string[] = []
  const tested = q.params.weights
  for (const w of s.weights) {
    const was = tested[w.code]
    if (!w.enabled && was !== undefined) out.push(`«${w.title}» выключен`)
    else if (w.enabled && was === undefined) out.push(`«${w.title}» включён`)
    else if (w.enabled && was !== undefined && Math.abs(w.weight - was) > 1e-9)
      out.push(`вес «${w.title}»: ${formatDecimal(was, 1)} → ${formatDecimal(w.weight, 1)}`)
  }
  const t = q.params.thresholds
  if (Math.abs(s.thresholds.medium - t.medium) > 1e-9 || Math.abs(s.thresholds.high - t.high) > 1e-9) {
    out.push(`пороги ${formatDecimal(t.medium, 0)}/${formatDecimal(t.high, 0)} → ${formatDecimal(s.thresholds.medium, 0)}/${formatDecimal(s.thresholds.high, 0)}`)
  }
  if (s.horizon_days !== q.params.horizon_days) out.push(`горизонт ${q.params.horizon_days} → ${s.horizon_days} дн.`)
  return out
}

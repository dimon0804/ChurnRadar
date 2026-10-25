import type { QualityHonesty, QualityHonestyMethod, QualityReliabilityPoint } from '@/api/types'
import { formatAuc, formatLift, SCENARIO_LABEL, SCENARIOS, type ScenarioKey } from './model-quality'
import { DASH, formatDecimal } from './format'

export type HonestyMethod = 'rules' | 'rules_calibrated' | 'logreg' | 'gbm'
export type ReliabilitySeries = 'rules' | 'rules_calibrated' | 'logreg'

export const HONESTY_METHODS: readonly HonestyMethod[] = ['rules', 'rules_calibrated', 'logreg', 'gbm']
export const RELIABILITY_SERIES: readonly ReliabilitySeries[] = ['rules', 'rules_calibrated', 'logreg']

export const HONESTY_METHOD_LABEL: Record<HonestyMethod, string> = {
  rules: 'Правила, сырая вероятность',
  rules_calibrated: 'Правила после калибровки',
  logreg: 'Логистическая регрессия',
  gbm: 'Градиентный бустинг',
}

/** Короткие имена для связного текста. */
const SHORT: Record<HonestyMethod, string> = {
  rules: 'правила',
  rules_calibrated: 'правила с калибровкой',
  logreg: 'логистическая регрессия',
  gbm: 'градиентный бустинг',
}

const num = (v: number | null | undefined): v is number => typeof v === 'number' && Number.isFinite(v)

/** Brier с тремя знаками: 0,178. */
export function formatBrier(value: number | null | undefined): string {
  if (!num(value)) return DASH
  return value.toFixed(3).replace('.', ',')
}

export function scenarioOf(h: QualityHonesty, key: ScenarioKey) {
  return h.calibration.by_scenario[key]
}

export function methodOf(h: QualityHonesty, key: ScenarioKey, m: HonestyMethod): QualityHonestyMethod | undefined {
  return scenarioOf(h, key)?.methods[m]
}

/** Точки reliability-диаграммы: пустые корзины выкинуты, порядок по предсказанию. */
export function reliabilitySeries(h: QualityHonesty, key: ScenarioKey): Record<ReliabilitySeries, QualityReliabilityPoint[]> {
  const rel = scenarioOf(h, key)?.reliability ?? {}
  const out = {} as Record<ReliabilitySeries, QualityReliabilityPoint[]>
  for (const s of RELIABILITY_SERIES) {
    out[s] = (rel[s] ?? [])
      .filter((p) => p.n > 0 && num(p.predicted) && num(p.actual))
      .sort((a, b) => a.predicted - b.predicted)
  }
  return out
}

/** Верх осей диаграммы: по самой большой точке с запасом, шаг 0,1, не меньше 0,1 и не больше 1. */
export function reliabilityDomain(series: Record<ReliabilitySeries, QualityReliabilityPoint[]>): number {
  let max = 0
  for (const pts of Object.values(series)) for (const p of pts) max = Math.max(max, p.predicted, p.actual)
  return Math.min(1, Math.max(0.1, Math.ceil(max * 10 + 1e-9) / 10))
}

export interface BrierRow {
  scenario: ScenarioKey
  raw: number | null
  calibrated: number | null
  /** Brier, если бы калибровку обучили на этом же сценарии */
  ownFit: number | null
  better: boolean | null
  fittedHere: boolean
}

export function brierRows(h: QualityHonesty): BrierRow[] {
  const fit = h.calibration.fit_scenario
  return SCENARIOS.map((key) => {
    const raw = methodOf(h, key, 'rules')?.service_brier ?? null
    const calibrated = methodOf(h, key, 'rules_calibrated')?.service_brier ?? null
    const ownFit = h.calibration.brier_cross?.[key]?.[key] ?? null
    return {
      scenario: key,
      raw,
      calibrated,
      ownFit: num(ownFit) ? ownFit : null,
      better: num(raw) && num(calibrated) ? calibrated < raw : null,
      fittedHere: fit === key,
    }
  })
}

function argBest(
  h: QualityHonesty,
  key: ScenarioKey,
  methods: readonly HonestyMethod[],
  metric: keyof QualityHonestyMethod,
  lowerIsBetter = false,
): { method: HonestyMethod; value: number } | null {
  let best: { method: HonestyMethod; value: number } | null = null
  for (const m of methods) {
    const v = methodOf(h, key, m)?.[metric]
    if (!num(v)) continue
    if (!best || (lowerIsBetter ? v < best.value : v > best.value)) best = { method: m, value: v }
  }
  return best
}

/** Разница AUC меньше этого — «на одном уровне»: на нескольких тысячах клиентов это шум. */
export const AUC_TIE = 0.005

export interface ScenarioVerdict {
  scenario: ScenarioKey
  winner: 'rules' | 'ml' | 'tie' | null
  text: string
}

export interface ChallengerVerdict {
  scenarios: ScenarioVerdict[]
  summary: string
}

/** Честный вывод «правила против ML» по цифрам: кто точнее ранжирует и кто лучше откалиброван. */
export function challengerVerdict(h: QualityHonesty): ChallengerVerdict {
  const scenarios = SCENARIOS.filter((k) => scenarioOf(h, k)).map((key): ScenarioVerdict => {
    const rules = argBest(h, key, ['rules', 'rules_calibrated'], 'customer_roc_auc')
    const ml = argBest(h, key, ['logreg', 'gbm'], 'customer_roc_auc')
    const brier = argBest(h, key, HONESTY_METHODS, 'service_brier', true)
    const label = SCENARIO_LABEL[key]
    if (!rules || !ml) return { scenario: key, winner: null, text: `${label}: для сравнения не хватает цифр.` }
    const delta = ml.value - rules.value
    let head: string
    let winner: ScenarioVerdict['winner']
    if (Math.abs(delta) < AUC_TIE) {
      winner = 'tie'
      head = `правила и ML на одном уровне: AUC по клиентам ${formatAuc(rules.value)} у правил и ${formatAuc(ml.value)} у лучшей модели (${SHORT[ml.method]})`
    } else if (delta > 0) {
      winner = 'ml'
      head = `${SHORT[ml.method]} ранжирует точнее правил: AUC по клиентам ${formatAuc(ml.value)} против ${formatAuc(rules.value)}`
    } else {
      winner = 'rules'
      head = `правила ранжируют точнее: AUC по клиентам ${formatAuc(rules.value)} против ${formatAuc(ml.value)} у лучшей модели (${SHORT[ml.method]})`
    }
    const liftRules = methodOf(h, key, rules.method)?.customer_lift_top10
    const liftMl = methodOf(h, key, ml.method)?.customer_lift_top10
    const lift =
      num(liftRules) && num(liftMl) ? ` В верхних 10 % клиентов: ${formatLift(liftRules)} у правил, ${formatLift(liftMl)} у ML.` : ''
    const cal = brier ? ` Точнее всех вероятность у метода «${HONESTY_METHOD_LABEL[brier.method]}» (Brier ${formatBrier(brier.value)}).` : ''
    return { scenario: key, winner, text: `${label}: ${head}.${lift}${cal}` }
  })

  const winners = scenarios.map((s) => s.winner)
  let summary: string
  if (winners.length === 0) summary = 'Сравнение с ML-моделями ещё не посчитано.'
  else if (winners.every((w) => w === 'ml'))
    summary =
      'ML-модели немного точнее в обоих сценариях. Правила оставляем рабочими, потому что каждую причину видно в карточке клиента; регрессия считается рядом как контрольная и подсказывает, где правила недобирают.'
  else if (winners.every((w) => w === 'rules' || w === 'tie'))
    summary = 'ML-модели на тех же признаках не дают заметного выигрыша, так что правила здесь ничего не теряют, а объяснить их проще.'
  else
    summary =
      'Однозначного победителя нет: в одном сценарии точнее ML, в другом правила или ничья. Регрессия остаётся контрольной моделью, решения принимаются по правилам.'
  return { scenarios, summary }
}

export interface GroupRow {
  label: string
  cap: number | null
  baseline: boolean
  levelChanges: number
  auc: Partial<Record<ScenarioKey, number | null>>
  aucDelta: Partial<Record<ScenarioKey, number | null>>
  lift: Partial<Record<ScenarioKey, number | null>>
  /** AUC вырос в обоих сценариях больше, чем на шум */
  improves: boolean
}

export function groupRows(h: QualityHonesty): GroupRow[] {
  const groups = h.groups ?? []
  const base = groups.find((g) => g.cap === null) ?? groups[0]
  return groups.map((g) => {
    const auc: GroupRow['auc'] = {}
    const aucDelta: GroupRow['aucDelta'] = {}
    const lift: GroupRow['lift'] = {}
    for (const key of SCENARIOS) {
      const v = g.scenarios[key]?.customer_roc_auc
      const b = base?.scenarios[key]?.customer_roc_auc
      auc[key] = num(v) ? v : null
      aucDelta[key] = num(v) && num(b) ? v - b : null
      const l = g.scenarios[key]?.high_lift ?? g.scenarios[key]?.customer_lift_top10
      lift[key] = num(l) ? l : null
    }
    const baseline = g === base
    const improves = !baseline && SCENARIOS.every((k) => num(aucDelta[k]) && (aucDelta[k] as number) >= AUC_TIE)
    return { label: g.label, cap: g.cap ?? null, baseline, levelChanges: g.level_changes, auc, aucDelta, lift, improves }
  })
}

/** Почему группа по умолчанию выключена (или что потолок помогает) — по цифрам таблицы. */
export function groupsVerdict(rows: readonly GroupRow[]): string {
  const variants = rows.filter((r) => !r.baseline)
  if (variants.length === 0) return 'Варианты группы ещё не посчитаны.'
  const helping = variants.filter((r) => r.improves)
  if (helping.length === 0) {
    return 'Ни один потолок не поднимает AUC в обоих сценариях, поэтому по умолчанию группа выключена. Включать её стоит, если вам важнее, чтобы три платёжных сигнала не складывались в один большой риск.'
  }
  const best = helping.reduce((a, b) => ((a.cap ?? 0) > (b.cap ?? 0) ? a : b))
  return `Потолок ${formatDecimal(best.cap, 0)} поднимает AUC в обоих сценариях, группу можно включить на странице модели.`
}

/** «+0,01» / «−0,02» / «0» для разницы AUC. */
export function formatAucDelta(delta: number | null | undefined): string {
  if (!num(delta)) return DASH
  if (Math.abs(delta) < 0.005) return '±0'
  return `${delta > 0 ? '+' : '−'}${formatAuc(Math.abs(delta))}`
}

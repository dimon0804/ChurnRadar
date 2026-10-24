/**
 * Подготовка рядов для графиков. Сервер отдаёт готовые агрегаты, здесь только раскладка
 * под Recharts и подписи — без пересчёта денег, чтобы цифры на графике совпадали с таблицами.
 */
import type {
  CustomerCharts,
  FeatureContribution,
  FeatureWeight,
  HistoryPoint,
  Marketing,
  ModelSettingsPreview,
  RiskDistribution,
  RiskLevel,
  Summary,
} from '@/api/types'
import { countLabel, formatDecimal, formatInt, formatMoney, formatShare } from '@/lib/format'
import { PAYMENT_TERM_LABEL, RISK_LABEL, RISK_LABEL_LONG } from '@/lib/labels'
import { FEATURE_TITLE } from '@/lib/reasons'

export const SCORED_LEVELS = ['high', 'medium', 'low'] as const satisfies readonly RiskLevel[]
export type ScoredLevel = (typeof SCORED_LEVELS)[number]

const money = (v: number | null | undefined) => formatMoney(v, { cents: false })

// --- распределение score -------------------------------------------------------------------------

export interface HistogramRow {
  label: string
  from: number
  to: number
  high: number
  medium: number
  low: number
  total: number
  loss: number
}

export const bucketLabel = (from: number, to: number) => `${from}–${to}`

export function histogramRows(d: RiskDistribution | undefined): HistogramRow[] {
  return (d?.buckets ?? []).map((b) => ({
    label: bucketLabel(b.from, b.to),
    from: b.from,
    to: b.to,
    high: b.by_level.high,
    medium: b.by_level.medium,
    low: b.by_level.low,
    total: b.customers,
    loss: b.expected_loss,
  }))
}

export function histogramAria(rows: HistogramRow[]): string {
  const total = rows.reduce((s, r) => s + r.total, 0)
  if (total === 0) return 'Распределение risk-score: клиентов с оценкой пока нет.'
  const peak = rows.reduce((a, b) => (b.total > a.total ? b : a))
  const high = rows.filter((r) => r.from >= 60).reduce((s, r) => s + r.total, 0)
  return (
    `Распределение risk-score ${countLabel(total, ['клиента', 'клиентов', 'клиентов'])} по корзинам по 10 пунктов. ` +
    `Больше всего клиентов в корзине ${peak.label}: ${formatInt(peak.total)}. ` +
    `Со score от 60: ${formatInt(high)}. Таблица — по кнопке «Таблица».`
  )
}

// --- выручка по уровням ---------------------------------------------------------------------------

export interface LevelSlice {
  level: RiskLevel
  label: string
  value: number
  customers: number
  share: number
}

export function revenueByLevel(d: RiskDistribution | undefined): LevelSlice[] {
  const levels = d?.levels ?? []
  const total = levels.reduce((s, l) => s + Math.max(l.monthly_revenue, 0), 0)
  return levels
    .filter((l) => l.monthly_revenue > 0)
    .map((l) => ({
      level: l.level,
      label: RISK_LABEL_LONG[l.level],
      value: l.monthly_revenue,
      customers: l.customers,
      share: total > 0 ? l.monthly_revenue / total : 0,
    }))
}

export function slicesAria(title: string, slices: { label: string; value: number; share: number }[]): string {
  if (slices.length === 0) return `${title}: данных пока нет.`
  return `${title}: ${slices.map((s) => `${s.label} ${money(s.value)} (${formatShare(s.share, 0)})`).join(', ')}. Таблица — по кнопке «Таблица».`
}

// --- деньги по причинам ---------------------------------------------------------------------------

export interface ReasonLossRow {
  code: string
  title: string
  loss: number
  customers: number
  primary: number
  share: number
}

export function lossByReason(reasons: Summary['top_reasons'] | undefined, limit = 8): ReasonLossRow[] {
  const rows = (reasons ?? [])
    .filter((r) => r.customers > 0)
    .map((r) => ({
      code: r.code,
      title: FEATURE_TITLE[r.code] ?? r.title,
      loss: Math.max(r.expected_loss ?? 0, 0),
      customers: r.customers,
      primary: r.primary_customers ?? 0,
      share: 0,
    }))
    .sort((a, b) => b.loss - a.loss || b.customers - a.customers)
    .slice(0, limit)
  const max = Math.max(1, ...rows.map((r) => r.loss))
  return rows.map((r) => ({ ...r, share: r.loss / max }))
}

// --- история сводки -------------------------------------------------------------------------------

export interface HistoryRow {
  at: string
  t: number
  loss: number
  high: number
  atRisk: number
  customers: number
  version: number
  full: boolean
}

export function historyRows(items: HistoryPoint[] | undefined): HistoryRow[] {
  return (items ?? [])
    .map((i) => ({
      at: i.at,
      t: Date.parse(i.at),
      loss: i.expected_loss,
      high: i.by_level.high,
      atRisk: i.by_level.high + i.by_level.medium,
      customers: i.customers_total,
      version: i.model_version,
      full: i.full,
    }))
    .filter((r) => Number.isFinite(r.t))
}

export function historyAria(rows: HistoryRow[]): string {
  if (rows.length < 2) return 'История ключевых метрик: точек пока мало.'
  const first = rows[0]!
  const last = rows[rows.length - 1]!
  return (
    `Как менялись метрики за ${countLabel(rows.length, ['точку', 'точки', 'точек'])}: ожидаемая потеря от ${money(first.loss)} до ${money(last.loss)}, ` +
    `клиентов высокого риска от ${formatInt(first.high)} до ${formatInt(last.high)}. Таблица — по кнопке «Таблица».`
  )
}

/** Изменение последней точки к первой: «+€120» и «−3». */
export function historyDelta(rows: HistoryRow[]): { loss: number; high: number } | null {
  if (rows.length < 2) return null
  const first = rows[0]!
  const last = rows[rows.length - 1]!
  return { loss: last.loss - first.loss, high: last.high - first.high }
}

/** Подпись оси времени: часы, если история за сутки, иначе дата. */
export function timeTick(t: number, spanMs: number): string {
  const d = new Date(t)
  if (spanMs <= 36 * 3600_000) return d.toLocaleTimeString('ru-RU', { hour: '2-digit', minute: '2-digit' })
  return d.toLocaleDateString('ru-RU', { day: 'numeric', month: 'short' })
}

// --- водопад вклада причин ------------------------------------------------------------------------

export interface WaterfallRow {
  code: string
  title: string
  /** Невидимая подставка столбца. */
  base: number
  value: number
  end: number
  kind: 'step' | 'total'
}

const WATERFALL_STEPS = 6

/** Ступени от 0 до score: каждая причина добавляет свой вклад; мелочь собирается в «Остальное». */
export function waterfallRows(features: FeatureContribution[] | undefined, score: number | null | undefined): WaterfallRow[] {
  const positive = (features ?? []).filter((f) => f.contribution >= 0.05).sort((a, b) => b.contribution - a.contribution)
  if (positive.length === 0 || typeof score !== 'number') return []
  const head = positive.length > WATERFALL_STEPS ? positive.slice(0, WATERFALL_STEPS - 1) : positive
  const tail = positive.slice(head.length)
  const steps = head.map((f) => ({ code: f.code, title: f.title || FEATURE_TITLE[f.code] || f.code, value: f.contribution }))
  if (tail.length > 0) {
    steps.push({ code: '_rest', title: `Остальное (${tail.length})`, value: tail.reduce((s, f) => s + f.contribution, 0) })
  }
  let acc = 0
  const rows: WaterfallRow[] = steps.map((s) => {
    const row = { ...s, base: acc, end: acc + s.value, kind: 'step' as const }
    acc += s.value
    return row
  })
  rows.push({ code: '_total', title: 'Итого risk-score', base: 0, value: score, end: score, kind: 'total' })
  return rows
}

export function waterfallAria(rows: WaterfallRow[]): string {
  const total = rows.find((r) => r.kind === 'total')
  if (!total) return 'Вклад причин в risk-score: заметных причин нет.'
  const steps = rows.filter((r) => r.kind === 'step')
  return `Risk-score ${formatDecimal(total.value, 0)} складывается из: ${steps.map((s) => `${s.title} +${formatDecimal(s.value, 1)}`).join(', ')}. Таблица — по кнопке «Таблица».`
}

// --- баланс против продлений ----------------------------------------------------------------------

export interface RunwayRow {
  date: string
  t: number
  balance: number
  paid: number
  unpaid: number
  renewals: number
}

export function runwayRows(runway: CustomerCharts['runway'] | undefined): RunwayRow[] {
  return (runway?.points ?? []).map((p) => ({ ...p, t: Date.parse(`${p.date}T00:00:00Z`) }))
}

export function runwayAria(runway: CustomerCharts['runway'] | undefined, fmtDate: (iso: string) => string): string {
  if (!runway) return 'Баланс против графика продлений: нет данных.'
  const start = money(runway.start_balance)
  if (runway.renewals === 0) return `На балансе ${start}. Автопродлений в ближайшие ${runway.days} дн. нет, баланс не тратится.`
  const out = runway.runs_out_at
    ? `Денег не хватит на продление ${fmtDate(runway.runs_out_at)}.`
    : `Денег хватит на все ${countLabel(runway.renewals, ['продление', 'продления', 'продлений'])} за ${runway.days} дн.`
  return `На балансе ${start}, впереди ${countLabel(runway.renewals, ['автопродление', 'автопродления', 'автопродлений'])}. ${out} Таблица — по кнопке «Таблица».`
}

// --- платежи по месяцам ---------------------------------------------------------------------------

export interface PaymentRow {
  month: string
  label: string
  paid: number
  payments: number
  failed: number
}

export function monthLabel(month: string): string {
  const [y, m] = month.split('-').map(Number)
  if (!y || !m) return month
  const name = new Date(Date.UTC(y, m - 1, 1)).toLocaleDateString('ru-RU', { month: 'short', timeZone: 'UTC' }).replace('.', '')
  return `${name} ${String(y).slice(2)}`
}

export function paymentRows(payments: CustomerCharts['payments'] | undefined): PaymentRow[] {
  return (payments ?? []).map((p) => ({ ...p, label: monthLabel(p.month) }))
}

export function paymentsAria(rows: PaymentRow[]): string {
  const paid = rows.reduce((s, r) => s + r.paid, 0)
  const failed = rows.reduce((s, r) => s + r.failed, 0)
  const active = rows.filter((r) => r.paid > 0).length
  if (paid === 0 && failed === 0) return 'Платежей за 12 месяцев не было.'
  return (
    `За 12 месяцев проведено ${money(paid)}, платежи были в ${countLabel(active, ['месяце', 'месяцах', 'месяцах'])}` +
    `${failed > 0 ? `, не прошло ${countLabel(failed, ['платёж', 'платежа', 'платежей'])}` : ''}. Таблица — по кнопке «Таблица».`
  )
}

// --- маркетинг ------------------------------------------------------------------------------------

export interface SegmentRow {
  level: RiskLevel
  label: string
  mrr: number
  kept: number
  loss: number
  ltv: number
  ltvAvg: number
  customers: number
}

/** Сегменты с выручкой: MRR делится на «останется» и «ожидаемая потеря за месяц». */
export function segmentRows(m: Marketing | undefined): SegmentRow[] {
  return (m?.segments ?? [])
    .filter((s) => s.level !== 'unknown' || s.mrr > 0)
    .map((s) => {
      const loss = Math.min(Math.max(s.expected_mrr_loss, 0), s.mrr)
      return {
        level: s.level,
        label: RISK_LABEL[s.level],
        mrr: s.mrr,
        kept: s.mrr - loss,
        loss,
        ltv: s.ltv,
        ltvAvg: s.ltv_avg,
        customers: s.customers,
      }
    })
}

export interface TermSlice {
  term: string
  label: string
  value: number
  services: number
  share: number
}

export function termSlices(m: Marketing | undefined): TermSlice[] {
  const terms = (m?.payment_terms ?? []).filter((t) => t.mrr > 0)
  const total = terms.reduce((s, t) => s + t.mrr, 0)
  return terms
    .map((t) => ({
      term: t.term,
      label: PAYMENT_TERM_LABEL[t.term as keyof typeof PAYMENT_TERM_LABEL] ?? t.term,
      value: t.mrr,
      services: t.services,
      share: total > 0 ? t.mrr / total : 0,
    }))
    .sort((a, b) => b.value - a.value)
}

export interface FunnelStep {
  key: string
  label: string
  hint: string
  value: number
  /** Доля от первой ступени. */
  share: number
  /** Доля от предыдущей ступени; у первой — null. */
  fromPrev: number | null
  href: string | null
}

/** Воронка «клиенты → платящие → под риском → высокий риск» из маркетинговых метрик. */
export function funnelSteps(m: Marketing | undefined): FunnelStep[] {
  if (!m) return []
  const seg = (level: RiskLevel) => m.segments.find((s) => s.level === level)?.customers ?? 0
  const raw = [
    { key: 'all', label: 'Все клиенты', hint: 'Аккаунты в базе', value: m.customers_total, href: '/customers' },
    { key: 'paying', label: 'Платящие', hint: 'Есть действующие платные услуги', value: m.paying_customers, href: null },
    { key: 'risk', label: 'Под риском', hint: 'Средний и высокий риск', value: seg('high') + seg('medium'), href: '/customers?level=high,medium' },
    { key: 'high', label: 'Высокий риск', hint: 'Score выше порога высокого риска', value: seg('high'), href: '/customers?level=high' },
  ]
  const first = Math.max(raw[0]!.value, 1)
  return raw.map((s, i) => ({
    ...s,
    share: s.value / first,
    fromPrev: i === 0 ? null : raw[i - 1]!.value > 0 ? s.value / raw[i - 1]!.value : 0,
  }))
}

// --- модель ---------------------------------------------------------------------------------------

export interface FeatureLossRow {
  code: string
  title: string
  loss: number
  customers: number
  enabled: boolean
}

/** Вклад каждого признака в общую ожидаемую потерю. Выключенные признаки — с нулём, чтобы их было видно. */
export function featureLossRows(reasons: Summary['top_reasons'] | undefined, weights: FeatureWeight[] | undefined): FeatureLossRow[] {
  const byCode = new Map((reasons ?? []).map((r) => [r.code, r]))
  const rows: FeatureLossRow[] = (weights ?? []).map((w) => ({
    code: w.code,
    title: w.title,
    loss: Math.max(byCode.get(w.code)?.expected_loss ?? 0, 0),
    customers: byCode.get(w.code)?.customers ?? 0,
    enabled: w.enabled && w.weight > 0,
  }))
  for (const r of reasons ?? []) {
    if (!rows.some((x) => x.code === r.code)) {
      rows.push({ code: r.code, title: FEATURE_TITLE[r.code] ?? r.title, loss: Math.max(r.expected_loss ?? 0, 0), customers: r.customers, enabled: true })
    }
  }
  return rows.sort((a, b) => b.loss - a.loss || Number(b.enabled) - Number(a.enabled) || a.title.localeCompare(b.title, 'ru'))
}

export interface LevelCompareRow {
  level: ScoredLevel
  label: string
  before: number
  after: number
}

export function whatIfLevelRows(preview: ModelSettingsPreview | undefined): LevelCompareRow[] {
  if (!preview) return []
  return SCORED_LEVELS.map((level) => ({
    level,
    label: RISK_LABEL[level],
    before: preview.before.by_level[level],
    after: preview.after.by_level[level],
  }))
}

export function compareAria(rows: LevelCompareRow[]): string {
  if (rows.length === 0) return 'Сравнение уровней появится, как только вы что-нибудь поменяете.'
  return `Клиенты по уровням, было и станет: ${rows.map((r) => `${r.label} ${formatInt(r.before)} → ${formatInt(r.after)}`).join(', ')}. Таблица — по кнопке «Таблица».`
}

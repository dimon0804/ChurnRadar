import { describe, expect, it } from 'vitest'
import type { Marketing, ModelSettingsPreview, RiskDistribution } from '@/api/types'
import {
  featureLossRows,
  funnelSteps,
  histogramAria,
  histogramRows,
  historyDelta,
  historyRows,
  lossByReason,
  monthLabel,
  paymentRows,
  revenueByLevel,
  segmentRows,
  termSlices,
  waterfallRows,
  whatIfLevelRows,
} from './charts'
import { clearReason, clearScoreRange, parseCustomersState, reasonHref, scoreRangeHref, serializeCustomersState, toCustomersQuery } from './url-state'

const bucket = (i: number, high = 0, medium = 0, low = 0) => ({
  from: i * 10,
  to: i * 10 + 10,
  customers: high + medium + low,
  expected_loss: (high + medium + low) * 100,
  by_level: { high, medium, low },
})

const distribution: RiskDistribution = {
  buckets: Array.from({ length: 10 }, (_, i) => (i === 3 ? bucket(3, 0, 2, 5) : i === 9 ? bucket(9, 4) : bucket(i))),
  levels: [
    { level: 'high', customers: 4, monthly_revenue: 6000, expected_loss: 3000 },
    { level: 'medium', customers: 2, monthly_revenue: 2000, expected_loss: 500 },
    { level: 'low', customers: 5, monthly_revenue: 2000, expected_loss: 50 },
    { level: 'unknown', customers: 3, monthly_revenue: 0, expected_loss: 0 },
  ],
  scored_at: null,
  model_version: 1,
}

describe('гистограмма и выручка по уровням', () => {
  it('раскладывает корзины по уровням и подписывает диапазоны', () => {
    const rows = histogramRows(distribution)
    expect(rows).toHaveLength(10)
    expect(rows[3]).toMatchObject({ label: '30–40', medium: 2, low: 5, total: 7 })
    expect(histogramAria(rows)).toContain('корзине 30–40: 7')
    expect(histogramAria(histogramRows(undefined))).toContain('пока нет')
  })

  it('доли выручки без уровней с нулевой выручкой', () => {
    const slices = revenueByLevel(distribution)
    expect(slices.map((s) => s.level)).toEqual(['high', 'medium', 'low'])
    expect(slices[0]!.share).toBeCloseTo(0.6)
  })
})

describe('деньги по причинам', () => {
  it('сортирует по потере, отбрасывает причины без клиентов и считает долю от максимума', () => {
    const rows = lossByReason([
      { code: 'inactivity', title: 'x', customers: 3, primary_customers: 1, expected_loss: 500 },
      { code: 'prolong_wait', title: 'y', customers: 2, primary_customers: 2, expected_loss: 2000 },
      { code: 'suspended', title: 'z', customers: 0, primary_customers: 0, expected_loss: 0 },
    ])
    expect(rows.map((r) => r.code)).toEqual(['prolong_wait', 'inactivity'])
    expect(rows[0]).toMatchObject({ title: 'Ждёт оплаты', share: 1 })
    expect(rows[1]!.share).toBeCloseTo(0.25)
  })
})

describe('история сводки', () => {
  const item = (at: string, loss: number, high: number) => ({
    at,
    model_version: 1,
    full: false,
    customers_total: 10,
    by_level: { high, medium: 1, low: 5, unknown: 0 },
    services_at_risk: 3,
    expected_loss: loss,
    exposure: 0,
    monthly_revenue_total: 0,
    revenue_at_risk_share: 0,
  })

  it('считает изменение последней точки к первой', () => {
    const rows = historyRows([item('2026-10-19T15:00:00Z', 1000, 3), item('2026-10-19T15:10:00Z', 1500, 2)])
    expect(rows[1]).toMatchObject({ loss: 1500, high: 2, atRisk: 3 })
    expect(historyDelta(rows)).toEqual({ loss: 500, high: -1 })
    expect(historyDelta(rows.slice(0, 1))).toBeNull()
  })
})

describe('водопад вклада причин', () => {
  const f = (code: string, contribution: number) => ({ code, title: code, group: 'g', value: 1, weight: 10, contribution, explanation: '' })

  it('ступени нарастают до score, мелочь собирается в «Остальное»', () => {
    const features = [f('a', 30), f('b', 20), f('c', 10), f('d', 5), f('e', 3), f('g', 1), f('h', 0.5), f('zero', 0)]
    const rows = waterfallRows(features, 69.5)
    expect(rows.map((r) => r.code)).toEqual(['a', 'b', 'c', 'd', 'e', '_rest', '_total'])
    expect(rows[1]).toMatchObject({ base: 30, end: 50 })
    expect(rows[5]).toMatchObject({ title: 'Остальное (2)', value: 1.5 })
    expect(rows.at(-1)).toMatchObject({ kind: 'total', end: 69.5 })
  })

  it('без оценки или без причин — пусто', () => {
    expect(waterfallRows([f('a', 10)], null)).toEqual([])
    expect(waterfallRows([f('a', 0)], 0)).toEqual([])
  })
})

describe('платежи по месяцам', () => {
  it('подписывает месяцы по-русски', () => {
    expect(monthLabel('2026-09')).toMatch(/^сен/)
    expect(monthLabel('2026-09')).toMatch(/26$/)
    expect(paymentRows([{ month: '2025-12', paid: 1, payments: 1, failed: 0 }])[0]!.label).toMatch(/^дек/)
  })
})

const marketing = {
  customers_total: 100,
  paying_customers: 80,
  segments: [
    { level: 'high', customers: 10, mrr: 1000, arpu: 100, monthly_churn: 0.5, ltv: 2000, ltv_avg: 200, expected_mrr_loss: 600 },
    { level: 'medium', customers: 20, mrr: 2000, arpu: 100, monthly_churn: 0.2, ltv: 8000, ltv_avg: 400, expected_mrr_loss: 300 },
    { level: 'low', customers: 50, mrr: 5000, arpu: 100, monthly_churn: 0.05, ltv: 50000, ltv_avg: 1000, expected_mrr_loss: 100 },
    { level: 'unknown', customers: 20, mrr: 0, arpu: 0, monthly_churn: null, ltv: 0, ltv_avg: 0, expected_mrr_loss: 0 },
  ],
  payment_terms: [
    { term: 'month', services: 10, mrr: 6000, share: 0.75 },
    { term: 'hour', services: 2, mrr: 2000, share: 0.25 },
    { term: 'year', services: 0, mrr: 0, share: 0 },
  ],
} as unknown as Marketing

describe('маркетинг', () => {
  it('воронка вложена и ссылается на списки', () => {
    const steps = funnelSteps(marketing)
    expect(steps.map((s) => s.value)).toEqual([100, 80, 30, 10])
    expect(steps[2]!.fromPrev).toBeCloseTo(30 / 80)
    expect(steps[3]!.href).toBe('/customers?level=high')
    expect(funnelSteps(undefined)).toEqual([])
  })

  it('MRR сегмента делится на «останется» и потерю, сегмент без выручки скрыт', () => {
    const rows = segmentRows(marketing)
    expect(rows.map((r) => r.level)).toEqual(['high', 'medium', 'low'])
    expect(rows[0]).toMatchObject({ kept: 400, loss: 600, ltvAvg: 200 })
  })

  it('периоды оплаты без выручки не попадают в донат', () => {
    const slices = termSlices(marketing)
    expect(slices.map((s) => s.term)).toEqual(['month', 'hour'])
    expect(slices[0]!.label).toBe('месяц')
  })
})

describe('модель', () => {
  it('вклад признаков: выключенные с нулём в конце', () => {
    const weights = [
      { code: 'prolong_wait', title: 'Ждёт оплаты', weight: 85, enabled: true },
      { code: 'suspended', title: 'Сервер выключен', weight: 50, enabled: false },
      { code: 'inactivity', title: 'Давно не платил', weight: 20, enabled: true },
    ] as never
    const rows = featureLossRows(
      [
        { code: 'inactivity', title: 'x', customers: 3, expected_loss: 100 },
        { code: 'prolong_wait', title: 'y', customers: 5, expected_loss: 900 },
      ],
      weights,
    )
    expect(rows.map((r) => r.code)).toEqual(['prolong_wait', 'inactivity', 'suspended'])
    expect(rows[2]).toMatchObject({ loss: 0, enabled: false })
  })

  it('«было / станет» по уровням', () => {
    const totals = (high: number, medium: number, low: number) => ({ by_level: { high, medium, low, unknown: 0 } })
    const preview = { before: totals(5, 10, 20), after: totals(8, 9, 18) } as unknown as ModelSettingsPreview
    expect(whatIfLevelRows(preview)).toEqual([
      { level: 'high', label: 'Высокий', before: 5, after: 8 },
      { level: 'medium', label: 'Средний', before: 10, after: 9 },
      { level: 'low', label: 'Низкий', before: 20, after: 18 },
    ])
  })
})

describe('фильтры причины и диапазона в адресе', () => {
  const parse = (qs: string) => parseCustomersState(new URLSearchParams(qs))

  it('читает причину, область и диапазон score', () => {
    const s = parse('reason=prolong_wait&reason_scope=main&score_min=30&score_max=40&view=chart')
    expect(s).toMatchObject({ reason: 'prolong_wait', reasonScope: 'main', scoreMin: 30, scoreMax: 40, view: 'chart' })
    expect(toCustomersQuery(s)).toMatchObject({ reason: 'prolong_wait', reason_scope: 'main', score_min: 30, score_max: 40 })
    expect(serializeCustomersState(s).toString()).toBe('reason=prolong_wait&reason_scope=main&score_min=30&score_max=40&view=chart')
  })

  it('отбрасывает мусор и перевёрнутый диапазон', () => {
    const s = parse('reason=DROP%20TABLE&reason_scope=main&score_min=50&score_max=40')
    expect(s.reason).toBeUndefined()
    expect(s.reasonScope).toBeUndefined()
    expect(s.scoreMin).toBeUndefined()
    expect(parse('score_min=-5&score_max=200').scoreMax).toBeUndefined()
    expect(toCustomersQuery(parse('reason_scope=main')).reason_scope).toBeUndefined()
  })

  it('сброс чипов и ссылки', () => {
    const s = parse('reason=inactivity&score_min=0&score_max=10&page=3&level=high')
    expect(serializeCustomersState(clearReason(s)).toString()).toBe('level=high&score_min=0&score_max=10')
    expect(serializeCustomersState(clearScoreRange(s)).toString()).toBe('level=high&reason=inactivity')
    expect(reasonHref('prolong_wait')).toBe('/customers?reason=prolong_wait')
    expect(reasonHref('prolong_wait', 'main')).toBe('/customers?reason=prolong_wait&reason_scope=main')
    expect(scoreRangeHref(90, 100)).toBe('/customers?score_min=90&score_max=100')
  })
})

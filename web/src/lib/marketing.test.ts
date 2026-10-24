import { describe, expect, it } from 'vitest'
import type { Marketing, RetentionScenario } from '@/api/types'
import { NBSP } from './format'
import {
  eur,
  formatMonths,
  formatRatio,
  formatTenure,
  GLOSSARY,
  kpiHints,
  parseEuro,
  parsePercent,
  ratioVerdict,
  retentionSentence,
  targetSentence,
} from './marketing'

const s = (text: string) => text.replaceAll(NBSP, ' ')

function scenario(overrides: Partial<RetentionScenario> = {}): RetentionScenario {
  return {
    levels: ['high'],
    customers: 4,
    mrr: 312_189,
    ltv_now: 430_143,
    ltv_retained: 4_180_842,
    ltv_uplift: 3_750_699,
    saved_mrr: 204_514,
    target_monthly_churn: 0.0747,
    target_source: 'fact',
    ...overrides,
  }
}

function marketing(overrides: Partial<Marketing> = {}): Marketing {
  return {
    horizon_days: 30,
    customers_total: 10,
    active_customers: 6,
    paying_customers: 6,
    mrr: 315_891,
    arpu: 52_649,
    arppu: 52_649,
    churn: {
      customer_horizon: 0.651,
      customer_monthly: 0.651,
      revenue_horizon: 0.727,
      revenue_monthly: 0.727,
      expected_customers_lost: 3.9,
      expected_mrr_loss: 229_783,
      fact: {
        customers_observed: 10,
        customers_left: 4,
        exposure_months: 53.57,
        monthly_churn: 0.0747,
        avg_tenure_months: 5.36,
        avg_lifetime_left_months: 5.31,
      },
    },
    retention: { customer_monthly: 0.349, revenue_monthly: 0.273 },
    lifetime: { expected_months: 1.54, capped: false, cap_months: 60, fact_avg_months: 5.36 },
    ltv: 80_852,
    ltv_total: 437_233,
    ltv_at_risk: 437_233,
    ltv_fact: { total: 32_487, avg_per_customer: 3_249, payments: 66, avg_check: 492 },
    concentration: { top_customers: 1, top_share: 0.605 },
    auto_prolong: { customers: 6, customers_share: 1, mrr_share: 0.768 },
    payment_terms: [],
    segments: [],
    retention_scenarios: [scenario()],
    unit_economics: { margin: 1, cac: null, ltv_cac: null, payback_months: null },
    settings: { margin: 1, cac: null, updated_at: null, updated_by: null },
    scored_at: '2026-10-19T15:00:05Z',
    model_version: 1,
    ...overrides,
  }
}

describe('formatting', () => {
  it('rounds money to whole euros', () => {
    expect(s(eur(315_891))).toBe('€3 159')
  })

  it('shows months with the cap', () => {
    expect(s(formatMonths(1.54))).toBe('1,5 мес.')
    expect(s(formatMonths(13.4))).toBe('13 мес.')
    expect(s(formatMonths(60, true))).toBe('больше 60 мес.')
    expect(formatMonths(null)).toBe('—')
  })

  it('turns days with us into words', () => {
    expect(formatTenure(0.02)).toBe('меньше дня')
    expect(s(formatTenure(32))).toBe('32 дня')
    expect(s(formatTenure(88))).toBe('3 мес.')
    expect(s(formatTenure(400))).toBe('1,1 года')
    expect(s(formatTenure(370))).toBe('1 год')
    expect(s(formatTenure(800))).toBe('2,2 года')
    expect(s(formatTenure(1825))).toBe('5 лет')
  })

  it('reads LTV/CAC like a marketer', () => {
    expect(formatRatio(4.25)).toBe('4,3×')
    expect(ratioVerdict(null)).toBe('none')
    expect(ratioVerdict(0.8)).toBe('loss')
    expect(ratioVerdict(2)).toBe('thin')
    expect(ratioVerdict(3)).toBe('healthy')
  })
})

describe('inputs', () => {
  it('parses euros into cents', () => {
    expect(parseEuro('25')).toBe(2_500)
    expect(parseEuro('12,50')).toBe(1_250)
    expect(parseEuro(' 1 200.5 ')).toBe(120_050)
    expect(parseEuro('')).toBeNull()
    expect(parseEuro('abc')).toBeNaN()
    expect(parseEuro('1,234')).toBeNaN()
  })

  it('parses percent into a share', () => {
    expect(parsePercent('40')).toBeCloseTo(0.4)
    expect(parsePercent('37,5%')).toBeCloseTo(0.375)
    expect(parsePercent('много')).toBeNaN()
  })
})

describe('retention', () => {
  it('says how much LTV grows if we keep high-risk customers', () => {
    const text = s(retentionSentence(scenario(), 437_233))
    expect(text).toBe('Если удержим клиентов высокого риска (4 клиента), LTV базы вырастет на €37 507 (+858 %).')
  })

  it('names both levels and explains the target churn', () => {
    const both = scenario({ levels: ['high', 'medium'], customers: 6 })
    expect(s(retentionSentence(both, 0))).toContain('клиентов высокого и среднего риска (6 клиентов)')
    expect(s(targetSentence(both))).toContain('к обычному оттоку по истории: 7,5 % в месяц')
    expect(s(targetSentence(both))).toContain('€2 045')
    expect(targetSentence(scenario({ target_source: 'low_risk' }))).toContain('к оттоку клиентов низкого риска')
  })

  it('is honest when there is nothing to compare with or nobody to keep', () => {
    const none = scenario({ ltv_uplift: null, ltv_retained: null, saved_mrr: null, target_monthly_churn: null, target_source: 'none' })
    expect(retentionSentence(none, 100)).toMatch(/^Не с чем сравнить/)
    expect(targetSentence(none)).toBe('')
    expect(retentionSentence(scenario({ customers: 0 }), 100)).toMatch(/удерживать некого/)
  })
})

describe('hints', () => {
  it('explain formulas with the numbers on screen', () => {
    const hints = kpiHints(marketing())
    expect(s(hints.arpu)).toContain('€3 159 ÷ 6 = €526')
    expect(s(hints.ltv)).toContain('уйдёт 65 % клиентов')
    expect(hints.ltv).toContain('выручка, а не прибыль')
    expect(s(hints.revenueChurn)).toContain('€2 298 из €3 159')
    expect(hints.ltvCac).toContain('Стоимости привлечения в данных нет')
  })

  it('mention margin and payback once they are set', () => {
    const m = marketing({ unit_economics: { margin: 0.4, cac: 5_000, ltv_cac: 0.6, payback_months: 0.24 } })
    const hints = kpiHints(m)
    expect(s(hints.ltv)).toContain('Учтена маржа 40 %')
    expect(s(hints.ltvCac)).toContain('€50 ÷ €211')
  })
})

describe('glossary', () => {
  it('covers every term the page uses', () => {
    const terms = GLOSSARY.map((g) => g.term)
    for (const term of ['MRR', 'ARPU', 'ARPPU', 'LTV', 'CAC', 'Churn', 'Retention', 'Gross Revenue Churn', 'Payback']) {
      expect(terms).toContain(term)
    }
    for (const g of GLOSSARY) expect(g.text.length).toBeLessThan(200)
  })
})

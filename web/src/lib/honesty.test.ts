import { createElement, type ReactElement } from 'react'
import { renderToStaticMarkup } from 'react-dom/server'
import { describe, expect, it } from 'vitest'
// тот же файл, что api отдаёт на /api/v1/model/quality
import resultsJson from '../../../tools/calibration/results/results.json?raw'
import type { QualityHonesty, QualityHonestyMethod } from '@/api/types'
import { BrierCard, ChallengerCard, HonestyGroupsCard } from '@/components/quality-honesty'
import { NBSP } from './format'
import {
  brierRows,
  challengerVerdict,
  formatAucDelta,
  formatBrier,
  groupRows,
  groupsVerdict,
  reliabilityDomain,
  reliabilitySeries,
} from './honesty'

const nb = (s: string) => s.split(NBSP).join(' ')
const html = (el: ReactElement) => nb(renderToStaticMarkup(el))

const m = (auc: number, brier: number, lift = 3): QualityHonestyMethod => ({
  customer_roc_auc: auc,
  service_roc_auc: auc - 0.02,
  customer_lift_top10: lift,
  service_brier: brier,
})

function fixture(over: Partial<Record<'rules' | 'rules_calibrated' | 'logreg' | 'gbm', QualityHonestyMethod>> = {}, calendar = over): QualityHonesty {
  const methods = (o: typeof over) => ({
    rules: m(0.78, 0.178),
    rules_calibrated: m(0.77, 0.167),
    logreg: m(0.777, 0.17),
    gbm: m(0.76, 0.175),
    ...o,
  })
  return {
    calibration: {
      fit_scenario: 'calendar',
      services: 1000,
      brier_cross: { stream: { stream: 0.16, calendar: 0.09 }, calendar: { stream: 0.19, calendar: 0.066 } },
      by_scenario: {
        stream: {
          methods: methods(over),
          reliability: {
            rules: [
              { n: 10, predicted: 0.3, actual: 0.2 },
              { n: 0, predicted: 0.5, actual: 0 },
              { n: 12, predicted: 0.05, actual: 0.1 },
            ],
            rules_calibrated: [{ n: 22, predicted: 0.12, actual: 0.13 }],
          },
        },
        calendar: { methods: methods(calendar), reliability: {} },
      },
    },
    groups: [
      { label: 'без групп', cap: null, level_changes: 0, levels: {}, scenarios: { stream: { customer_roc_auc: 0.78, high_lift: 3.2 }, calendar: { customer_roc_auc: 0.92, high_lift: 5.2 } } },
      { label: 'потолок 70', cap: 70, level_changes: 16, levels: {}, scenarios: { stream: { customer_roc_auc: 0.781, high_lift: 3.4 }, calendar: { customer_roc_auc: 0.91, high_lift: 5.4 } } },
    ],
  }
}

describe('reliability', () => {
  it('выкидывает пустые корзины и сортирует по предсказанию', () => {
    const s = reliabilitySeries(fixture(), 'stream')
    expect(s.rules.map((p) => p.predicted)).toEqual([0.05, 0.3])
    expect(s.logreg).toEqual([])
    expect(reliabilityDomain(s)).toBe(0.4)
  })

  it('пустой сценарий не ломает диаграмму', () => {
    const s = reliabilitySeries(fixture(), 'calendar')
    expect(reliabilityDomain(s)).toBe(0.1)
  })
})

describe('Brier', () => {
  it('сырая против калиброванной по сценариям, меньше — лучше', () => {
    const rows = brierRows(fixture())
    expect(rows.map((r) => [r.scenario, r.better, r.fittedHere])).toEqual([
      ['stream', true, false],
      ['calendar', true, true],
    ])
    expect(rows[0]?.ownFit).toBe(0.16)
    expect(formatBrier(0.17790683)).toBe('0,178')
    expect(formatBrier(null)).toBe('—')
  })
})

describe('кто лучше: правила или ML', () => {
  it('ничья, если разница AUC меньше порога шума', () => {
    const v = challengerVerdict(fixture())
    expect(v.scenarios.map((s) => s.winner)).toEqual(['tie', 'tie'])
    expect(v.summary).toMatch(/не дают заметного выигрыша/)
  })

  it('ML впереди в обоих сценариях — так и пишем, без хвастовства за правила', () => {
    const v = challengerVerdict(fixture({ gbm: m(0.8, 0.16, 3.6) }))
    expect(v.scenarios.every((s) => s.winner === 'ml')).toBe(true)
    expect(v.scenarios[0]?.text).toContain('градиентный бустинг ранжирует точнее правил: AUC по клиентам 0,80 против 0,78')
    expect(v.scenarios[0]?.text).toContain('Градиентный бустинг» (Brier 0,160)')
    expect(v.summary).toMatch(/ML-модели немного точнее в обоих сценариях/)
  })

  it('разные победители в сценариях — победителя нет', () => {
    const v = challengerVerdict(fixture({ rules: m(0.82, 0.17) }, { gbm: m(0.9, 0.06) }))
    expect(v.scenarios.map((s) => s.winner)).toEqual(['rules', 'ml'])
    expect(v.summary).toMatch(/Однозначного победителя нет/)
  })
})

describe('группы признаков', () => {
  it('разница AUC считается от варианта без групп', () => {
    const rows = groupRows(fixture())
    expect(rows[0]?.baseline).toBe(true)
    expect(rows[1]?.aucDelta.calendar).toBeCloseTo(-0.01)
    expect(rows[1]?.improves).toBe(false)
    expect(groupsVerdict(rows)).toMatch(/по умолчанию группа выключена/)
    expect(formatAucDelta(-0.01)).toBe('−0,01')
    expect(formatAucDelta(0.001)).toBe('±0')
  })
})

describe('настоящий results.json', () => {
  const honesty = (JSON.parse(resultsJson) as { honesty?: QualityHonesty }).honesty

  it.runIf(Boolean(honesty))('карточки собираются из файла расчёта', () => {
    const h = honesty as QualityHonesty
    expect(brierRows(h)).toHaveLength(2)
    expect(challengerVerdict(h).scenarios).toHaveLength(2)
    const challenger = html(createElement(ChallengerCard, { h, scenario: 'calendar' }))
    expect(challenger).toContain('Логистическая регрессия')
    expect(challenger).toContain('Градиентный бустинг')
    expect(html(createElement(BrierCard, { h }))).toContain('Меньше — лучше')
    const groups = html(createElement(HonestyGroupsCard, { h }))
    expect(groups).toContain('(по умолчанию)')
    expect(groups).toContain(groupsVerdict(groupRows(h)))
  })
})

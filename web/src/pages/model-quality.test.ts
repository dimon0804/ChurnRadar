import { createElement, type ReactElement } from 'react'
import { renderToStaticMarkup } from 'react-dom/server'
import { MemoryRouter } from 'react-router'
import { describe, expect, it } from 'vitest'
// тот же файл, что api отдаёт на /api/v1/model/quality: тест ловит расхождение панели и tools/calibration
import resultsJson from '../../../tools/calibration/results/results.json?raw'
import type { ModelQuality, ModelSettings } from '@/api/types'
import { NBSP } from '@/lib/format'
import {
  formatAuc,
  formatLift,
  headline,
  liftRows,
  rocRows,
  scoreHistogram,
  settingsDrift,
} from '@/lib/model-quality'
import { BaselinesCard, MethodCard, QualityHeadline, RobustnessCard, ThresholdCard } from './model-quality'

const q = {
  ...(JSON.parse(resultsJson) as Omit<ModelQuality, 'current_model'>),
  current_model: { riskcore_sources: 'abc', matches: true },
} as ModelQuality

const nb = (s: string) => s.split(NBSP).join(' ')
const html = (el: ReactElement) => nb(renderToStaticMarkup(createElement(MemoryRouter, null, el)))

describe('данные проверки', () => {
  it('в results.json есть оба сценария, ROC и прогоны на устойчивость', () => {
    expect(Object.keys(q.scenarios).sort()).toEqual(['calendar', 'stream'])
    expect(q.scenarios.stream.roc.fpr).toHaveLength(101)
    expect(q.robustness.length).toBeGreaterThan(1)
  })

  it('заголовок собирает диапазоны по двум сценариям', () => {
    const h = headline(q)
    const aucs = [q.scenarios.stream, q.scenarios.calendar].map((s) => s.ranking.churned.model.roc_auc as number)
    expect(h.aucRange).toBe(`${formatAuc(Math.min(...aucs))}–${formatAuc(Math.max(...aucs))}`)
    expect(h.liftRange).toMatch(/^\d+,\d–\d+,\d$/)
  })

  it('lift растёт от низкого риска к высокому в обоих сценариях', () => {
    for (const sc of [q.scenarios.stream, q.scenarios.calendar]) {
      const [low, medium, high] = liftRows(sc).map((r) => r.lift as number)
      expect(low).toBeLessThan(1)
      expect(medium).toBeLessThan(high as number)
      expect(high).toBeGreaterThan(2)
    }
  })

  it('ROC: случайный порядок — диагональ, кривая модели не ниже неё в среднем', () => {
    const rows = rocRows(q.scenarios.calendar)
    expect(rows[0]?.random).toBe(0)
    expect(rows.at(-1)?.model).toBe(1)
    const above = rows.filter((r) => (r.model ?? 0) >= r.random).length
    expect(above).toBe(rows.length)
  })
})

describe('помощники', () => {
  it('formatLift и formatAuc', () => {
    expect(formatLift(3.344)).toBe('×3,3')
    expect(formatLift(0.443)).toBe('×0,44')
    expect(formatLift(null)).toBe('—')
    expect(formatAuc(0.7653)).toBe('0,77')
  })

  it('гистограмма из накопленных корзин', () => {
    const bins = scoreHistogram({
      scored: 10,
      avg: 30,
      max: 90,
      buckets: [
        { le: 20, count: 4 },
        { le: 10, count: 1 },
        { le: 100, count: 10 },
      ],
    })
    expect(bins.map((b) => [b.from, b.to, b.count])).toEqual([
      [0, 10, 1],
      [10, 20, 3],
      [20, 100, 6],
    ])
    expect(scoreHistogram(undefined)).toEqual([])
  })

  it('settingsDrift видит изменённый вес и порог', () => {
    const weights = Object.entries(q.params.weights).map(([code, weight]) => ({
      code,
      title: code,
      description: '',
      group: 'renewal',
      weight,
      min: 0,
      max: 100,
      enabled: true,
    }))
    const same = {
      version: 1,
      horizon_days: q.params.horizon_days,
      thresholds: { ...q.params.thresholds },
      weights,
    } as unknown as ModelSettings
    expect(settingsDrift(q, same)).toEqual([])
    const changed = {
      ...same,
      thresholds: { medium: 30, high: q.params.thresholds.high },
      weights: weights.map((w, i) => (i === 0 ? { ...w, weight: w.weight + 5 } : w)),
    } as unknown as ModelSettings
    const drift = settingsDrift(q, changed)
    expect(drift).toHaveLength(2)
    expect(drift[0]).toContain('вес')
    expect(drift[1]).toContain('пороги')
  })
})

describe('страница «Качество модели»', () => {
  it('показывает AUC, lift и долю ушедших среди «высоких»', () => {
    const out = html(createElement(QualityHeadline, { q }))
    const h = headline(q)
    expect(out).toContain('ROC AUC')
    expect(out).toContain(h.aucRange)
    expect(out).toContain(`в ${h.liftRange} раза`)
    expect(out).toContain('data-testid="quality-lift"')
    expect(out).toContain('data-testid="quality-auc"')
  })

  it('таблица бейзлайнов перечисляет все методы', () => {
    const out = html(createElement(BaselinesCard, { sc: q.scenarios.stream }))
    for (const label of ['Модель (risk-score)', 'автопродление выключено', 'ждёт оплаты', 'Случайный порядок', 'Скрытый сегмент']) {
      expect(out).toContain(label)
    }
  })

  it('точность и полнота порога «высокий» совпадают с данными', () => {
    const sc = q.scenarios.calendar
    const out = html(createElement(ThresholdCard, { sc, q }))
    expect(out).toContain(`${Math.round((sc.thresholds.high.churned.precision as number) * 100)} %`)
    expect(out).toContain(`${Math.round((sc.thresholds.high.churned.recall as number) * 100)} %`)
  })

  it('устойчивость и честный блок про ограничения', () => {
    expect(html(createElement(RobustnessCard, { q }))).toContain('seed 43')
    const method = html(createElement(MethodCard, { q }))
    expect(method).toContain('Ограничения')
    expect(method).toContain('частично совпадают с признаками модели')
    expect(method).toContain('python -m tools.calibration')
  })
})

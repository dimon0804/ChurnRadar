import { describe, expect, it } from 'vitest'
import type { TimelinePoint } from '@/api/types'
import {
  axisWidth,
  buildRows,
  chartAriaLabel,
  churnStats,
  countAxisWidth,
  factGapNote,
  forecastBar,
  formatForecast,
  riskAxisWidth,
} from './churn-chart'

function fact(date: string, deletions: number, estimated = 0, atRisk: number | null = null): TimelinePoint {
  return {
    date,
    kind: 'fact',
    deletions,
    estimated_deletions: estimated,
    at_risk_services: atRisk,
    expected_nonrenewals: null,
    expected_loss: null,
  }
}

function forecast(date: string, expected: number, atRisk: number, loss = 1000): TimelinePoint {
  return {
    date,
    kind: 'forecast',
    deletions: null,
    at_risk_services: atRisk,
    expected_nonrenewals: expected,
    expected_loss: loss,
  }
}

const TIMELINE: TimelinePoint[] = [
  forecast('2026-10-21', 0.5, 9),
  fact('2026-10-18', 3, 1),
  forecast('2026-10-20', 2.25, 12),
  fact('2026-10-19', 0, 0, 12),
]

describe('buildRows', () => {
  it('сортирует по дате и раскладывает серии по типу точки', () => {
    const rows = buildRows(TIMELINE)
    expect(rows.map((r) => r.date)).toEqual(['2026-10-18', '2026-10-19', '2026-10-20', '2026-10-21'])
    expect(rows[0]).toMatchObject({ kind: 'fact', fact: 3, factExact: 2, factEstimated: 1, forecast: null, atRisk: null })
    expect(rows[1]).toMatchObject({ kind: 'fact', fact: 0, atRisk: 12 })
    expect(rows[2]).toMatchObject({ kind: 'forecast', fact: null, factExact: null, forecast: 2.25, atRisk: 12, loss: 1000 })
  })

  it('без estimated_deletions считает все удаления точными и не даёт оценке превысить факт', () => {
    const old: TimelinePoint = { date: '2026-10-01', kind: 'fact', deletions: 2 }
    expect(buildRows([old])[0]).toMatchObject({ factExact: 2, factEstimated: 0 })
    const broken = fact('2026-10-02', 1, 5)
    expect(buildRows([broken])[0]).toMatchObject({ factExact: 0, factEstimated: 1 })
  })

  it('отбрасывает нечисловые значения', () => {
    const bad = { ...forecast('2026-10-20', Number.NaN, 3), at_risk_services: null }
    expect(buildRows([bad])[0]).toMatchObject({ forecast: null, atRisk: null })
  })
})

describe('churnStats', () => {
  it('находит снимок, начало прогноза и итоги по сериям', () => {
    const stats = churnStats(buildRows(TIMELINE))
    expect(stats).toMatchObject({
      factDays: 2,
      forecastDays: 2,
      snapshotDate: '2026-10-19',
      firstForecast: '2026-10-20',
      lastDate: '2026-10-21',
      deletions: 3,
      estimatedDeletions: 1,
      atRiskNow: 12,
      atRiskEnd: 9,
      hasForecast: true,
      hasEstimated: true,
    })
    expect(stats.expectedNonrenewals).toBeCloseTo(2.75)
  })
})

describe('factGapNote', () => {
  const empty = churnStats(buildRows([fact('2026-10-18', 0), fact('2026-10-19', 0, 0, 4), forecast('2026-10-20', 1, 4)]))

  it('молчит, когда удаления в окне есть', () => {
    expect(factGapNote(churnStats(buildRows(TIMELINE)), { total: 6, last_date: '2026-10-18', last_estimated: false })).toBeNull()
  })

  it('объясняет, что удаления были раньше окна, и помечает оценку даты', () => {
    const note = factGapNote(empty, { total: 6, last_date: '2026-09-19', last_estimated: true })
    expect(note?.title).toBe('За 2 дня удалений нет')
    expect(note?.text).toContain('6 удалённых услуг')
    expect(note?.text).toContain('примерно 19 сен 2026')
    expect(note?.text).toContain('оценка')
    expect(note?.text).toContain('с момента загрузки')
  })

  it('не пишет «примерно», если дата удаления замечена точно', () => {
    const note = factGapNote(empty, { total: 1, last_date: '2026-09-19', last_estimated: false })
    expect(note?.text).toContain('1 удалённая услуга, последнее — 19 сен 2026,')
    expect(note?.text).not.toContain('оценка')
  })

  it('без удалённых услуг говорит, что истории нет', () => {
    expect(factGapNote(empty, { total: 0, last_date: null, last_estimated: false })?.title).toBe('В данных нет истории удалений')
    expect(factGapNote(empty, undefined)?.text).toBe('Факт начнёт копиться с момента загрузки.')
  })
})

describe('прогноз по дню', () => {
  it('малые доли не округляются до нуля', () => {
    expect(formatForecast(null)).toBe('—')
    expect(formatForecast(0)).toBe('0')
    expect(formatForecast(0.046)).toBe('≈0,05')
    expect(formatForecast(2.25)).toBe('≈2,3')
  })

  it('почти нулевые дни рисуются без столбца', () => {
    const [past, tiny, real] = buildRows([forecast('2026-10-20', 0.004, 1), forecast('2026-10-21', 0.3, 1), fact('2026-10-19', 1)])
    expect(forecastBar(tiny!)).toBeNull()
    expect(forecastBar(real!)).toBe(0.3)
    expect(forecastBar(past!)).toBeNull()
  })
})

describe('оси и подписи', () => {
  it('ширина оси растёт с длиной подписи', () => {
    expect(axisWidth(0)).toBe(32)
    expect(axisWidth(1600)).toBeGreaterThan(axisWidth(10))
    const rows = buildRows(TIMELINE)
    expect(countAxisWidth(rows)).toBe(axisWidth(3))
    expect(riskAxisWidth(rows)).toBe(axisWidth(12))
  })

  it('aria-описание называет все три серии', () => {
    const label = chartAriaLabel(churnStats(buildRows(TIMELINE)), 30)
    expect(label).toContain('прогноз на 30 дней')
    expect(label).toContain('Ушло услуг: 3, из них у 1 дата оценочная')
    expect(label).toContain('Ожидаем непродлений: ≈2,8')
    expect(label).toContain('Под риском на момент снимка 12 услуг, к концу прогноза 9')
    expect(label).toContain('«Таблица»')
  })
})

import { describe, expect, it } from 'vitest'
import { clockFromReference, clockFromServices, clockFromTimeline, dataClockLabel, resolveDataClock } from './data-clock'

const NOW = new Date('2026-10-24T12:00:00Z').getTime()

describe('clockFromReference', () => {
  it('reads an explicit snapshot moment', () => {
    const clock = clockFromReference('2026-10-19T15:00:00Z')
    expect(clock).toEqual({ kind: 'snapshot', at: new Date('2026-10-19T15:00:00Z'), precise: true })
    expect(dataClockLabel(clock!)).toBe('Данные на 19.10.2026, 15:00 UTC — момент снимка')
  })

  it('treats null as live data and a missing field as unknown', () => {
    expect(clockFromReference(null)).toEqual({ kind: 'live' })
    expect(clockFromReference(undefined)).toBeNull()
    expect(clockFromReference('не дата')).toBeNull()
  })
})

describe('clockFromTimeline', () => {
  const timeline = (lastFact: string) => [
    { date: '2026-10-01', kind: 'fact' },
    { date: lastFact, kind: 'fact' },
    { date: '2026-11-01', kind: 'forecast' },
  ]

  it('detects a frozen snapshot by the last fact day', () => {
    const clock = clockFromTimeline(timeline('2026-10-19'), NOW)
    expect(clock?.kind).toBe('snapshot')
    expect(dataClockLabel(clock!)).toBe('Данные на 19.10.2026 — момент снимка')
  })

  it('treats today or yesterday as live data', () => {
    expect(clockFromTimeline(timeline('2026-10-24'), NOW)).toEqual({ kind: 'live' })
    expect(clockFromTimeline(timeline('2026-10-23'), NOW)).toEqual({ kind: 'live' })
  })

  it('returns null without facts', () => {
    expect(clockFromTimeline([], NOW)).toBeNull()
    expect(clockFromTimeline(undefined, NOW)).toBeNull()
  })
})

describe('clockFromServices', () => {
  it('restores the moment from expiry dates, ignoring rounding of days', () => {
    const clock = clockFromServices(
      [
        { expires_at: '2025-09-14T16:00:00Z', days_to_expiry: -399.96 },
        { expires_at: '2025-10-14T15:00:00Z', days_to_expiry: -370 },
        { expires_at: '2026-03-14T15:00:00Z', days_to_expiry: -219 },
      ],
      NOW,
    )
    expect(clock).toEqual({ kind: 'snapshot', at: new Date('2026-10-19T15:00:00Z'), precise: true })
  })

  it('says live when the moment is close to now', () => {
    const clock = clockFromServices([{ expires_at: '2026-10-25T12:00:00Z', days_to_expiry: 1 }], NOW)
    expect(clock).toEqual({ kind: 'live' })
  })

  it('shows only the day when there is a single uncertain estimate', () => {
    const clock = clockFromServices([{ expires_at: '2026-10-20T15:00:00Z', days_to_expiry: 0.99 }], NOW)
    expect(clock?.kind).toBe('snapshot')
    expect(clock?.kind === 'snapshot' && clock.precise).toBe(false)
  })

  it('skips services without numbers', () => {
    expect(
      clockFromServices(
        [
          { expires_at: 'x', days_to_expiry: 1 },
          { expires_at: '2026-10-20T15:00:00Z', days_to_expiry: null },
        ],
        NOW,
      ),
    ).toBeNull()
  })
})

describe('resolveDataClock', () => {
  it('takes the first known source', () => {
    expect(resolveDataClock(null, undefined, { kind: 'live' })).toEqual({ kind: 'live' })
    expect(resolveDataClock(null, undefined)).toBeNull()
  })

  it('labels live data', () => {
    expect(dataClockLabel({ kind: 'live' })).toBe('Данные в реальном времени')
  })
})

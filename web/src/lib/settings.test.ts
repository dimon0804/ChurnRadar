import { describe, expect, it } from 'vitest'
import type { ModelSettings } from '@/api/types'
import { parseLiveEvent } from '@/api/events'
import { applyPatch, groupWeights, isEmptyPatch, mergePatch, weightStep } from './settings'

const base: ModelSettings = {
  version: 3,
  updated_at: '2026-10-24T10:00:00Z',
  horizon_days: 30,
  thresholds: { medium: 35, high: 65 },
  weights: [
    { code: 'a', title: 'A', description: '', group: 'renewal', weight: 30, min: 0, max: 100, enabled: true },
    { code: 'b', title: 'B', description: '', group: 'finance', weight: 1.5, min: 0, max: 5, enabled: true },
    { code: 'c', title: 'C', description: '', group: 'renewal', weight: 10, min: 0, max: 100, enabled: false },
  ],
}

describe('mergePatch', () => {
  it('merges weights by code and keeps the latest value', () => {
    const merged = mergePatch(
      { weights: [{ code: 'a', weight: 31 }], thresholds: { medium: 30 } },
      { weights: [{ code: 'a', weight: 35 }, { code: 'b', enabled: false }], thresholds: { high: 70 }, horizon_days: 14 },
    )
    expect(merged).toEqual({
      weights: [
        { code: 'a', weight: 35 },
        { code: 'b', enabled: false },
      ],
      thresholds: { medium: 30, high: 70 },
      horizon_days: 14,
    })
  })

  it('keeps weight and enabled changes for the same feature together', () => {
    expect(mergePatch({ weights: [{ code: 'a', weight: 40 }] }, { weights: [{ code: 'a', enabled: false }] }).weights).toEqual([
      { code: 'a', weight: 40, enabled: false },
    ])
  })

  it('detects empty patches', () => {
    expect(isEmptyPatch({})).toBe(true)
    expect(isEmptyPatch({ weights: [] })).toBe(true)
    expect(isEmptyPatch({ horizon_days: 7 })).toBe(false)
  })
})

describe('applyPatch', () => {
  it('applies a partial patch optimistically without touching other fields', () => {
    const next = applyPatch(base, { weights: [{ code: 'c', enabled: true }], thresholds: { high: 80 } })
    expect(next.weights.find((w) => w.code === 'c')).toMatchObject({ enabled: true, weight: 10 })
    expect(next.weights.find((w) => w.code === 'a')).toBe(base.weights[0])
    expect(next.thresholds).toEqual({ medium: 35, high: 80 })
    expect(next.horizon_days).toBe(30)
  })
})

describe('groups and steps', () => {
  it('groups weights in server order', () => {
    expect(groupWeights(base.weights).map(([g, ws]) => [g, ws.map((w) => w.code)])).toEqual([
      ['renewal', ['a', 'c']],
      ['finance', ['b']],
    ])
  })

  it('uses fine steps for narrow ranges', () => {
    expect(weightStep({ min: 0, max: 5 })).toBe(0.1)
    expect(weightStep({ min: 0, max: 100 })).toBe(1)
    expect(weightStep({ min: 3, max: 3 })).toBe(1)
  })
})

describe('parseLiveEvent', () => {
  const payload = {
    model_version: 12,
    scored_at: '2026-10-24T10:00:00Z',
    changed_customer_ids: [3, 7],
    summary: { customers_total: 10, by_level: { high: 3 }, expected_loss: 54000, exposure: 120000 },
  }

  it('accepts a bare payload', () => {
    expect(parseLiveEvent('scores.updated', JSON.stringify(payload))?.changed_customer_ids).toEqual([3, 7])
  })

  it('unwraps a NATS envelope', () => {
    const envelope = { event_id: 'x', type: 'scores.updated', occurred_at: '', schema_version: 1, payload }
    expect(parseLiveEvent('scores.updated', JSON.stringify(envelope))?.model_version).toBe(12)
  })

  it('returns null for garbage', () => {
    expect(parseLiveEvent('settings.updated', 'not json')).toBeNull()
    expect(parseLiveEvent('settings.updated', '{"foo":1}')).toBeNull()
  })
})

import { describe, expect, it } from 'vitest'
import { changedCount, describeRecalc, diffRows, scoreTrend } from './recalc'

// «~» в ожидаемой строке означает неразрывный пробел
const nb = (s: string) => s.replace(/~/g, ' ')
const summary = (high: number, loss: number) => ({ customers_total: 10, by_level: { high }, expected_loss: loss })

describe('describeRecalc', () => {
  it('names the model version and the number of changed customers', () => {
    const notice = describeRecalc({ model_version: 7, changed_customer_ids: [1, 2, 3], summary: summary(4, 229783) })
    expect(notice).toEqual({ title: nb('Оценки пересчитаны: модель v7, изменилось 3~клиента'), detail: null })
  })

  it('uses the singular form for one customer', () => {
    expect(describeRecalc({ model_version: 2, changed_customer_ids: [5] })?.title).toBe(
      nb('Оценки пересчитаны: модель v2, изменился 1~клиент'),
    )
  })

  it('says everyone was recalculated for a full recalc', () => {
    expect(describeRecalc({ model_version: 8, changed_customer_ids: [], full: true })?.title).toBe(
      'Оценки пересчитаны: модель v8, пересчитаны все клиенты',
    )
  })

  it('adds the shift of key numbers', () => {
    const notice = describeRecalc(
      { model_version: 9, changed_customer_ids: [1], summary: summary(3, 215000) },
      summary(4, 229783),
    )
    expect(notice?.detail).toBe(nb('Высокий риск 4 → 3 · потеря €2~298 → €2~150'))
  })

  it('stays silent when nothing changed', () => {
    expect(describeRecalc({ model_version: 3, changed_customer_ids: [] }, summary(4, 100))).toBeNull()
    expect(changedCount({ model_version: 3, changed_customer_ids: [] })).toBe(0)
    expect(changedCount({ model_version: 3, changed_customer_ids: [], full: true })).toBeNull()
  })
})

describe('diffRows', () => {
  const sig = (r: { id: number; v: number }) => String(r.v)

  it('finds rows whose values changed and ignores new rows', () => {
    const prev = new Map([
      [1, '10'],
      [2, '20'],
    ])
    const { changed, next } = diffRows(
      prev,
      [
        { id: 1, v: 10 },
        { id: 2, v: 21 },
        { id: 3, v: 30 },
      ],
      sig,
    )
    expect(changed).toEqual([2])
    expect([...next.keys()]).toEqual([1, 2, 3])
  })

  it('finds nothing on the first load', () => {
    expect(diffRows(new Map(), [{ id: 1, v: 1 }], sig).changed).toEqual([])
  })
})

describe('scoreTrend', () => {
  it('compares rounded scores', () => {
    expect(scoreTrend(60, 72)).toBe('up')
    expect(scoreTrend(72, 60)).toBe('down')
    expect(scoreTrend(60.2, 60.4)).toBeNull()
    expect(scoreTrend(null, 60)).toBeNull()
  })
})

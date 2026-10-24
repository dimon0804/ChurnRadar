import { describe, expect, it } from 'vitest'
import { approximateNote, countDelta, moneyDelta, movesSummary, moveKind, moveText, scoreDelta, shareDelta } from './what-if'

describe('what-if', () => {
  it('делит переходы на «в высокий», «из высокого» и остальные', () => {
    expect(moveKind({ level_before: 'medium', level_after: 'high' })).toBe('into_high')
    expect(moveKind({ level_before: 'high', level_after: 'low' })).toBe('out_of_high')
    expect(moveKind({ level_before: 'low', level_after: 'medium' })).toBe('other')
    expect(moveText({ level_before: 'medium', level_after: 'high' })).toBe('Средний → Высокий')
  })

  it('показывает разницу со знаком и прячет копеечную', () => {
    expect(moneyDelta(22_978_300, 22_103_000)).toBe('−€8 753')
    expect(moneyDelta(100, 1_100)).toBe('+€10')
    expect(moneyDelta(100, 120)).toBeNull()
    expect(shareDelta(0.25, 0.262)).toBe('+1,2 п.п.')
    expect(shareDelta(0.25, 0.2502)).toBeNull()
    expect(countDelta(4, 3)).toBe('−1')
    expect(countDelta(3, 3)).toBeNull()
    expect(scoreDelta(57.2, 71.9)).toBe('+15')
    expect(scoreDelta(null, 40)).toBeNull()
  })

  it('честно подписывает расчёт по выборке', () => {
    expect(approximateNote({ exact: true, sample_size: 10, customers_total: 10 })).toBeNull()
    const note = approximateNote({ exact: false, sample_size: 2000, customers_total: 100_000 })
    expect(note).toContain('выборку из 2 000 клиентов')
    expect(note).toContain('100 000')
    expect(note).toContain('приблизительный')
  })

  it('собирает движение клиентов в одну фразу', () => {
    expect(movesSummary({ changes_total: 0, into_high: 0, out_of_high: 0 })).toBe('Ни один клиент не сменит уровень риска')
    expect(movesSummary({ changes_total: 4, into_high: 2, out_of_high: 1 })).toBe(
      '2 попадут в высокий риск, 1 выйдет из высокого, 1 перейдёт между средним и низким',
    )
  })
})

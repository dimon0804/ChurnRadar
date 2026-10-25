import { describe, expect, it } from 'vitest'
import { NBSP } from './format'
import { comparePair, describeChange, toggleCompare } from './settings-changes'

const titles = new Map([['failed_payments', 'Неуспешные платежи']])
const groups = new Map([['payment', 'Оплата']])
const say = (path: string, old: unknown, next: unknown) => describeChange({ path, old, new: next }, titles, groups).split(NBSP).join(' ')

describe('describeChange', () => {
  it('веса, пороги и горизонт — как раньше', () => {
    expect(say('weights.failed_payments.weight', 20, 35.5)).toBe('«Неуспешные платежи»: 20 → 35,5')
    expect(say('weights.failed_payments.enabled', true, false)).toBe('«Неуспешные платежи» выключен')
    expect(say('thresholds.high', 65, 70)).toBe('Порог высокого риска: 65 → 70')
    expect(say('horizon_days', 30, 14)).toBe('Горизонт прогноза: 30 → 14 дн.')
  })

  it('группы признаков и калибровка по-человечески', () => {
    expect(say('groups.payment.enabled', false, true)).toBe('Группа «Оплата» включена')
    expect(say('groups.payment.cap', 85, 70)).toBe('Потолок группы «Оплата»: 85 → 70')
    expect(say('groups.other.enabled', true, false)).toBe('Группа «other» выключена')
    expect(say('calibrated', true, false)).toBe('Деньги считаются по сырому score')
    expect(say('calibrated', false, true)).toBe('Деньги считаются по калиброванной вероятности')
  })

  it('неизвестный путь показывается как есть', () => {
    expect(say('something.new', null, 3)).toBe('something.new: — → 3')
  })
})

describe('выбор версий для сравнения', () => {
  it('держит максимум две версии и упорядочивает пару', () => {
    let s: number[] = []
    s = toggleCompare(s, 5)
    expect(comparePair(s)).toBeNull()
    s = toggleCompare(s, 3)
    expect(comparePair(s)).toEqual({ from: 3, to: 5 })
    s = toggleCompare(s, 7)
    expect(s).toEqual([3, 7])
    s = toggleCompare(s, 3)
    expect(s).toEqual([7])
  })
})

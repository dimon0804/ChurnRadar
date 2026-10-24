import { describe, expect, it } from 'vitest'
import type { Marketing } from '@/api/types'
import { DEFAULT_MASS_LEVELS, massCount, urgentText } from './contact-today'
import { balanceLabel, NBSP, sentences, spendableBalance } from './format'
import { cacSourceText } from './marketing'
import { bestAction, FOCUS_LABEL } from './reasons'

const s = (text: string) => text.replaceAll(NBSP, ' ')

describe('замечания жюри', () => {
  it('B2: после сокращения с точкой вторую точку не ставим', () => {
    expect(sentences('Окупается за 0,6 мес.', 'Здоровая экономика')).toBe('Окупается за 0,6 мес. Здоровая экономика.')
    expect(sentences('Раз', null, '', 'Два?')).toBe('Раз. Два?')
    expect(sentences('Окупается за 0,6 мес.', 'Здоровая экономика')).not.toContain('..')
  })

  it('B2: откуда CAC — «задан администратором» и дата', () => {
    const m = {
      unit_economics: { margin: 1, cac: 4_000, ltv_cac: 2, payback_months: 0.6 },
      settings: { margin: 1, cac: 4_000, updated_at: '2026-10-19T15:00:00Z', updated_by: 'admin' },
    } as unknown as Marketing
    expect(s(cacSourceText(m) ?? '')).toBe('CAC задан администратором 19 окт 2026')
    expect(cacSourceText({ ...m, unit_economics: { ...m.unit_economics, cac: null } } as Marketing)).toBeNull()
  })

  it('4: главная причина риска и самое выгодное действие подписаны по-разному', () => {
    expect(FOCUS_LABEL.reason).toBe('Главная причина риска')
    expect(FOCUS_LABEL.action).toBe('Самое выгодное действие')
    const next = { code: 'insufficient_funds', title: 'Напомнить пополнить', expected_saving: 500 }
    expect(bestAction(next, { code: 'autoprolong_off_expiring' })).toBe(next)
    expect(bestAction(null, { code: 'all_services_gone' })).toEqual({ code: 'all_services_gone', title: 'Предложить вернуться' })
    expect(bestAction(null, { code: 'prolong_wait' })).toBeNull()
  })

  it('7: баланс с бонусами подписан одинаково', () => {
    const c = { balance: 4_332, bonus_balance: 615, bonus_state: 'unlocked' }
    expect(spendableBalance(c)).toEqual({ total: 4_947, bonus: 615 })
    expect(s(balanceLabel(c))).toBe('Баланс (вкл. бонусы €6,15)')
    expect(balanceLabel({ ...c, bonus_state: 'locked' })).toBe('Баланс')
    expect(spendableBalance({ ...c, bonus_state: 'locked' }).total).toBe(4_332)
  })

  it('8: массовая рассылка по умолчанию — высокий и средний риск', () => {
    const byLevel = { high: 12, medium: 19, low: 53 }
    expect(DEFAULT_MASS_LEVELS).toEqual(['high', 'medium'])
    expect(massCount(byLevel)).toBe(31)
    expect(massCount(byLevel, ['high', 'medium', 'low'])).toBe(84)
    expect(massCount(undefined)).toBe(0)
  })

  it('9: «срочно» — флаг по сроку, а не по уровню', () => {
    expect(s(urgentText({ urgent: true, deadline_days: 1 }) ?? '')).toBe('Срочно: срок через 1 день')
    expect(urgentText({ urgent: true, deadline_days: 0.1 })).toBe('Срочно: срок сегодня')
    expect(urgentText({ urgent: false, deadline_days: 1 })).toBeNull()
    expect(urgentText({})).toBeNull()
  })
})

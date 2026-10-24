import { describe, expect, it } from 'vitest'
import { isChurned, reasonAction, reasonTooltip, shortReasonTitle } from './reasons'

const long = {
  code: 'insufficient_funds',
  title: 'На продления за 30 дн. нужно €4 696,68, а на балансе €286,57: не хватит €4 410,11',
  action: 'Нет денег на продление: напомнить пополнить баланс до 21.10.2026',
}

describe('shortReasonTitle', () => {
  it('uses the feature title instead of the long explanation', () => {
    expect(shortReasonTitle(long)).toBe('Не хватит денег на продление')
  })

  it('takes the short label from the API first', () => {
    expect(shortReasonTitle({ ...long, label: 'Нет денег' }, { insufficient_funds: 'Другое' })).toBe('Нет денег')
    expect(shortReasonTitle({ ...long, label: '  ' })).toBe('Не хватит денег на продление')
  })

  it('prefers titles from the server', () => {
    expect(shortReasonTitle(long, new Map([['insufficient_funds', 'Мало денег']]))).toBe('Мало денег')
    expect(shortReasonTitle({ code: 'new_feature', title: 'Длинно' }, { new_feature: 'Новый признак' })).toBe('Новый признак')
  })

  it('falls back to a short head of an unknown reason', () => {
    expect(shortReasonTitle({ code: 'x', title: 'Странный сигнал: подробности дальше' })).toBe('Странный сигнал')
    const text = 'Очень длинное объяснение без двоеточия, которое не помещается в короткую подпись таблицы'
    expect(shortReasonTitle({ code: 'x', title: text })).toBe(text)
  })
})

describe('reasonAction', () => {
  it('returns the server action or a known action for customers without a score', () => {
    expect(reasonAction(long)).toBe(long.action)
    expect(reasonAction({ code: 'all_services_gone', title: 'Все услуги клиента удалены или заблокированы' })).toBe(
      'Предложить вернуться',
    )
    expect(reasonAction({ code: 'inactivity', title: 'Давно', action: '  ' })).toBeNull()
  })

  it('puts the full text and the action into the tooltip', () => {
    expect(reasonTooltip(long)).toBe(`${long.title}\nЧто сделать: ${long.action}`)
    expect(reasonTooltip({ code: 'x', title: 'Текст' })).toBe('Текст')
  })
})

describe('isChurned', () => {
  it('marks customers whose services are all closed', () => {
    expect(isChurned({ risk_level: 'unknown', reasons: [{ code: 'all_services_gone' }] })).toBe(true)
    expect(isChurned({ risk_level: 'unknown', reasons: [{ code: 'other', label: 'Уже ушёл' }] })).toBe(true)
    expect(isChurned({ risk_level: 'unknown', reasons: [], services: [{ status: 'deleted' }, { status: 'blocked' }] })).toBe(true)
  })

  it('keeps «no score» for other cases', () => {
    expect(isChurned({ risk_level: 'unknown', reasons: [{ code: 'no_paid_services' }], services: [{ status: 'active' }] })).toBe(false)
    expect(isChurned({ risk_level: 'unknown', reasons: [], services: [] })).toBe(false)
    expect(isChurned({ risk_level: 'high', reasons: [{ code: 'all_services_gone' }] })).toBe(false)
  })
})

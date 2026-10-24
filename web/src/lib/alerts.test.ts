import { describe, expect, it } from 'vitest'
import { badgeText, eventLabel, periodLabel, previewTitle, timezoneOptions } from './alerts'

describe('badgeText', () => {
  it('сворачивает большие числа', () => {
    expect(badgeText(3)).toBe('3')
    expect(badgeText(9)).toBe('9')
    expect(badgeText(42)).toBe('9+')
  })
})

describe('превью уведомлений', () => {
  it('подставляет порог и период', () => {
    expect(previewTitle('loss_growth', 10, 24)).toBe('Ожидаемая потеря выросла на 12,5 % за сутки')
    expect(previewTitle('high_count_growth', 10, 72)).toBe('В высоком риске на 13 клиентов больше, чем 3 дня назад')
    expect(previewTitle('risk_share', 40)).toBe('Под риском 41,3 % выручки')
  })

  it('называет периоды по-человечески', () => {
    expect(periodLabel(1)).toBe('за час')
    expect(periodLabel(168)).toBe('за неделю')
  })
})

describe('подписи', () => {
  it('знает сводку, проверку и старые записи журнала', () => {
    expect(eventLabel('digest')).toBe('Сводка за сутки')
    expect(eventLabel('test')).toBe('Проверка')
    expect(eventLabel('bulk')).toBe('Массовое изменение')
    expect(eventLabel('что-то новое')).toBe('Уведомление')
  })

  it('добавляет пояс браузера, если его нет в списке', () => {
    expect(timezoneOptions('America/New_York')[0]).toEqual({ value: 'America/New_York', label: 'America/New_York' })
    expect(timezoneOptions('Europe/Moscow').some((o) => o.value === 'Europe/Moscow')).toBe(true)
  })
})

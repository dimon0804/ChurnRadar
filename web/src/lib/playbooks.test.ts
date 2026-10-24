import { describe, expect, it } from 'vitest'
import type { PlaybookPreset, PlaybookUpliftWindow } from '@/api/playbooks'
import {
  buildInput,
  defaultWindow,
  describeAction,
  describeConditions,
  emptyDraft,
  formatPp,
  parseEuros,
  presetDraft,
  ruleSentence,
  shareLabel,
  toDraft,
  upliftRows,
} from './playbooks'

const PRESET: PlaybookPreset = {
  code: 'big_autoprolong_off',
  name: 'Автопродление выключено у крупного клиента',
  description: 'Письмо и уведомление',
  conditions: { autoprolong_off: true, min_mrr: 3000, no_email_days: 14 },
  actions: [
    { type: 'email', template_code: 'autoprolong_off_expiring' },
    { type: 'notify', username: 'admin' },
  ],
}

describe('parseEuros', () => {
  it('понимает запятую, пробелы и знак евро', () => {
    expect(parseEuros('50')).toBe(5000)
    expect(parseEuros('49,9')).toBe(4990)
    expect(parseEuros('€1 000')).toBe(100000)
    expect(parseEuros('')).toBeNull()
    expect(parseEuros('abc')).toBeNaN()
    expect(parseEuros('1,234')).toBeNaN()
  })
})

describe('buildInput', () => {
  it('пустая форма не сохраняется: нет условия и названия', () => {
    const { input, errors } = buildInput(emptyDraft())
    expect(input).toBeNull()
    expect(errors.join(' ')).toMatch(/хотя бы одно условие/)
    expect(errors.join(' ')).toMatch(/Назовите/)
  })

  it('собирает тело запроса: деньги в центах, шаблон по причине — null', () => {
    const draft = { ...emptyDraft(), name: ' Высокий риск ', levels: ['high' as const], minLoss: '50', noEmailDays: '14' }
    const { input, errors } = buildInput(draft)
    expect(errors).toEqual([])
    expect(input).toMatchObject({
      name: 'Высокий риск',
      mode: 'simulation',
      enabled: true,
      control_share: 0.2,
      conditions: { risk_levels: ['high'], min_expected_loss: 5000, no_email_days: 14 },
      actions: [{ type: 'email', template_code: null }],
    })
  })

  it('ловит ошибки в числах и пустые действия', () => {
    const draft = { ...emptyDraft(), name: 'x', levels: ['high' as const], email: false, minMrr: '100', maxMrr: '10', cooldownDays: '0' }
    const { input, errors } = buildInput(draft)
    expect(input).toBeNull()
    expect(errors).toContain('Выберите хотя бы одно действие')
    expect(errors).toContain('MRR «от» больше, чем «до»')
    expect(errors.some((e) => e.startsWith('Не чаще раза в дней'))).toBe(true)
  })

  it('статус «отложить» требует срок', () => {
    const draft = { ...emptyDraft(), name: 'x', becameHigh: true, email: false, status: true, statusDays: '' }
    expect(buildInput(draft).errors.join(' ')).toMatch(/Отложить на дней/)
    const ok = buildInput({ ...draft, statusDays: '3' })
    expect(ok.input?.actions).toEqual([{ type: 'status', status: 'snoozed', days: 3 }])
  })
})

describe('шаблон сценария и обратное преобразование', () => {
  it('подставляет текущего пользователя в уведомление и остаётся в симуляции', () => {
    const draft = presetDraft(PRESET, 'anna')
    expect(draft.notify).toBe(true)
    expect(draft.notifyUser).toBe('anna')
    expect(draft.minMrr).toBe('30')
    expect(draft.mode).toBe('simulation')
    const { input } = buildInput(draft)
    expect(input?.conditions).toEqual({ autoprolong_off: true, min_mrr: 3000, no_email_days: 14 })
    expect(input?.preset).toBe('big_autoprolong_off')
  })

  it('toDraft → buildInput возвращает то же правило', () => {
    const original = {
      name: 'Сценарий',
      description: null,
      enabled: false,
      mode: 'live' as const,
      conditions: { risk_levels: ['high' as const, 'medium' as const], min_expected_loss: 12345, expires_within_days: 3 },
      actions: [{ type: 'status' as const, status: 'resolved' as const }],
      control_share: 0.3,
      max_per_day: 10,
      cooldown_days: 7,
      preset: null,
    }
    expect(buildInput(toDraft(original)).input).toEqual(original)
  })
})

describe('описание словами', () => {
  it('условия и действия читаются как фраза', () => {
    const reasons = { insufficient_funds: 'Не хватит денег на продление' }
    expect(describeConditions({ main_reason: 'insufficient_funds', expires_within_days: 3 }, reasons)).toEqual([
      'главная причина — «Не хватит денег на продление»',
      'списание в ближайшие 3 дн.',
    ])
    expect(describeAction({ type: 'email', template_code: null })).toBe('письмо по главной причине')
    expect(describeAction({ type: 'status', status: 'snoozed', days: 5 })).toBe('отложить на 5 дн.')
    expect(ruleSentence({ risk_levels: ['high'] }, [{ type: 'notify', username: 'admin' }])).toBe(
      'Если риск высокий → уведомить admin',
    )
  })
})

describe('эффект', () => {
  const window = (days: number, enough: boolean): PlaybookUpliftWindow => ({
    days,
    treated_n: enough ? 100 : 3,
    control_n: enough ? 40 : 1,
    enough,
    summary: '',
    metrics: [
      {
        code: 'retained',
        title: 'Не ушли',
        treated: 80,
        control: 24,
        treated_share: 0.8,
        control_share: 0.6,
        diff: 0.2,
        ci_low: 0.03,
        ci_high: 0.37,
        verdict: 'better',
      },
    ],
  })

  it('процентные пункты со знаком', () => {
    expect(formatPp(0.123)).toBe('+12,3 п.п.')
    expect(formatPp(-0.05)).toBe('−5,0 п.п.')
    expect(formatPp(0)).toBe('0,0 п.п.')
    expect(formatPp(null)).toBe('—')
  })

  it('строки графика в процентах', () => {
    expect(upliftRows(window(7, true))).toEqual([{ metric: 'Не ушли', treated: 80, control: 60 }])
  })

  it('по умолчанию самое длинное окно, где хватает данных', () => {
    expect(defaultWindow([window(0, true), window(7, true), window(14, false)])).toBe(7)
    expect(defaultWindow([window(0, false), window(7, false)])).toBe(0)
  })

  it('доля контроля словами', () => {
    expect(shareLabel(0)).toBe('без контроля')
    expect(shareLabel(0.2)).toBe('20%')
  })
})

import { createElement } from 'react'
import { renderToStaticMarkup } from 'react-dom/server'
import { MemoryRouter } from 'react-router'
import { describe, expect, it, vi } from 'vitest'
import type { CampaignPreview, ContactTodayItem, CustomerAction, MailStats } from '@/api/types'
import { ContactTodayList } from '@/components/contact-today'
import {
  actionStatus,
  campaignSummary,
  CONTACT_LIST_CSV_URL,
  emailedUpdate,
  exportCsvUrl,
  exportUrlFromState,
  logLine,
  resolvedUpdate,
  savingText,
  snoozeUpdate,
  statsLine,
} from './contact-today'
import { insertAt, previewCaption, renderTemplate, unknownPlaceholders } from './mail'
import { DEFAULT_CUSTOMERS_STATE } from './url-state'

const NOW = new Date('2026-10-24T12:00:00Z')

function action(over: Partial<CustomerAction> = {}): CustomerAction {
  return {
    status: 'emailed',
    until: '2026-10-31T09:00:00Z',
    note: 'Пополните баланс до 24 октября',
    updated_at: '2026-10-24T09:00:00Z',
    updated_by: 'analyst',
    expected_loss_at_mark: 138497,
    risk_level_at_mark: 'high',
    active: true,
    return_reason: null,
    ...over,
  }
}

function item(over: Partial<ContactTodayItem> = {}): ContactTodayItem {
  return {
    id: 9,
    email: 'user-0009@example.test',
    risk_level: 'high',
    risk_score: 72.4,
    expected_loss: 138497,
    main_reason: 'Не хватит денег на продление',
    main_reason_code: 'insufficient_funds',
    next_action: { code: 'insufficient_funds', title: 'Напомнить пополнить баланс', expected_saving: 120000 },
    deadline_days: null,
    urgent: false,
    action: null,
    ...over,
  }
}

describe('exportCsvUrl', () => {
  it('без параметров — голый путь', () => {
    expect(exportCsvUrl()).toBe('/api/v1/customers/export.csv')
  })

  it('переносит фильтры и сортировку таблицы, но не страницу', () => {
    const url = exportUrlFromState({
      ...DEFAULT_CUSTOMERS_STATE,
      sort: 'expected_loss',
      order: 'asc',
      levels: ['high', 'medium'],
      search: '  example.test ',
      page: 3,
      pageSize: 50,
    })
    const params = new URL(url, 'http://x').searchParams
    expect(Object.fromEntries(params)).toEqual({ sort: 'expected_loss', order: 'asc', level: 'high,medium', search: 'example.test' })
  })

  it('выгрузка из блока — вся очередь по деньгам', () => {
    expect(CONTACT_LIST_CSV_URL).toBe('/api/v1/customers/export.csv?sort=expected_loss&order=desc&worklist=true')
  })
})

describe('тело отметки', () => {
  it('решено, написал сам и отложить', () => {
    expect(resolvedUpdate()).toEqual({ status: 'resolved' })
    expect(resolvedUpdate('  ответил ')).toEqual({ status: 'resolved', note: 'ответил' })
    expect(emailedUpdate()).toEqual({ status: 'emailed' })
    expect(snoozeUpdate(3)).toEqual({ status: 'snoozed', snooze_days: 3 })
  })
})

describe('savingText', () => {
  it('показывает экономию только когда она есть', () => {
    expect(savingText({ code: 'x', title: 'y', expected_saving: 138500 })).toBe('Сбережёт до €1 385')
    expect(savingText({ code: 'x', title: 'y', expected_saving: 0 })).toBeNull()
    expect(savingText(null)).toBeNull()
  })
})

describe('actionStatus — жизненный цикл', () => {
  it('нет отметки', () => {
    expect(actionStatus(null, NOW)).toBeNull()
  })

  it('письмо отправлено — ждём ответа до даты', () => {
    const s = actionStatus(action(), NOW)
    expect(s?.tone).toBe('emailed')
    expect(s?.label).toBe('Письмо отправлено')
    expect(s?.detail).toContain('31 окт')
    expect(s?.detail).toContain('analyst')
  })

  it('ответа нет — пора напомнить', () => {
    const s = actionStatus(action({ active: false, return_reason: 'no_reply' }), NOW)
    expect(s).toMatchObject({ tone: 'returned', label: 'Ответа нет — напомнить' })
  })

  it('решено и риск вырос после отметки', () => {
    expect(actionStatus(action({ status: 'resolved', until: null }), NOW)?.label).toBe('Решено')
    const grew = actionStatus(action({ status: 'resolved', until: null, active: false, return_reason: 'risk_grew' }), NOW)
    expect(grew?.label).toBe('Риск вырос после отметки')
  })

  it('отложено и отложка кончилась', () => {
    const snoozed = action({ status: 'snoozed', until: '2026-10-27T12:00:00Z' })
    expect(actionStatus(snoozed, NOW)?.label).toBe('Отложено до 27 окт')
    expect(actionStatus({ ...snoozed, active: false, return_reason: 'snooze_over' }, NOW)?.tone).toBe('expired')
  })
})

describe('журнал и сводки', () => {
  const entry = { until: null, note: null, expected_loss: 1, email_id: null, changed_at: '2026-10-24T09:00:00Z', changed_by: 'a' }

  it('строка журнала', () => {
    expect(logLine({ ...entry, status: 'emailed', note: 'Тема', email_id: 5 })).toBe('Письмо отправлено: «Тема»')
    expect(logLine({ ...entry, status: 'resolved' })).toBe('Решено')
    expect(logLine({ ...entry, status: 'cleared' })).toBe('Вернули в очередь')
    expect(logLine({ ...entry, status: 'snoozed', until: '2026-10-27T12:00:00Z' })).toMatch(/^Отложили до 27/)
  })

  it('статистика «помогло»', () => {
    const stats: MailStats = {
      period_days: 30, sent: 12, failed: 1, queued: 0, customers: 10, risk_down: 3, topped_up: 3, helped: 5, helped_share: 0.5,
    }
    expect(statsLine(stats)).toBe('За 30 дней 12 писем, помогло 5 из 10 клиентов (50 %)')
    expect(statsLine({ ...stats, sent: 0 })).toBeNull()
    expect(statsLine(undefined)).toBeNull()
  })

  it('итог предпросмотра рассылки', () => {
    const p: CampaignPreview = {
      total: 6, to_send: 4, skipped_recent: 1, skipped_no_email: 1, not_found: 0, templates: [], sample: null, enabled: true,
    }
    expect(campaignSummary(p, 7)).toBe(
      'Уйдёт 4 письма; 1 клиента пропустим: им уже писали за 7 дней; у 1 клиента нет email',
    )
  })
})

describe('шаблоны в редакторе', () => {
  it('предпросмотр с примером и проверка подстановок', () => {
    const values = { date: '24 октября', service: 'storage-00019' }
    expect(renderTemplate('До {{date}}, {{ service }}, {{oops}}', values)).toBe('До 24 октября, storage-00019, {{oops}}')
  })

  it('says whose data the template preview shows and whether it fits', () => {
    const base = { customer_id: 9, to: 'user-0009@example.test', subject: 's', text: 't' }
    expect(previewCaption({ ...base, applicable: true })).toBe(
      'Так письмо увидит клиент (настоящие данные: клиент 9, user-0009@example.test)',
    )
    expect(previewCaption({ ...base, applicable: false })).toContain('Такой причины у него нет')
    expect(previewCaption({ ...base, customer_id: null, to: null, applicable: false })).toContain('заглушки')
    expect(unknownPlaceholders('{{date}} {{oops}} {{x}} {{oops}}', ['date'])).toEqual(['oops', 'x'])
  })

  it('вставка подстановки в позицию курсора', () => {
    expect(insertAt('До . Спасибо', 3, 3, '{{date}}')).toEqual({ text: 'До {{date}}. Спасибо', cursor: 11 })
    expect(insertAt('abc', 1, 2, 'X')).toEqual({ text: 'aXc', cursor: 2 })
  })
})

describe('ContactTodayList', () => {
  const render = (items: ContactTodayItem[], marked = false) =>
    renderToStaticMarkup(
      createElement(
        MemoryRouter,
        null,
        createElement(ContactTodayList, { items, marked, onAction: vi.fn(), onWrite: vi.fn() }),
      ),
    )

  it('строка: ссылка в карточку, действие, экономия, сумма и кнопки письма', () => {
    const html = render([item(), item({ id: 10, email: 'user-0010@example.test', expected_loss: 648, next_action: null })])
    expect(html).toContain('href="/customers/9?sort=expected_loss&amp;order=desc"')
    expect(html).toContain('Напомнить пополнить баланс')
    expect(html).toContain('Сбережёт до €1 200')
    expect(html).toContain('€1 385')
    expect(html).toContain('Написать')
    expect(html).toContain('Отметить')
    expect(html).not.toMatch(/звон/i)
    // без готового действия показываем главную причину
    expect(html).toContain('Не хватит денег на продление')
    expect(html.match(/data-testid="contact-today-row"/g)).toHaveLength(2)
  })

  it('в отмеченных — статус и кнопка «Вернуть»', () => {
    const html = render([item({ action: action() })], true)
    expect(html).toContain('data-testid="action-badge"')
    expect(html).toContain('Письмо отправлено')
    expect(html).toContain('Вернуть')
    expect(html).not.toContain('Отметить')
  })
})

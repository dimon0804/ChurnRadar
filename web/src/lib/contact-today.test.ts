import { createElement } from 'react'
import { renderToStaticMarkup } from 'react-dom/server'
import { MemoryRouter } from 'react-router'
import { describe, expect, it, vi } from 'vitest'
import type { CallTodayItem, CustomerAction } from '@/api/types'
import { CallTodayList } from '@/components/call-today'
import {
  actionStatus,
  CALL_LIST_CSV_URL,
  doneUpdate,
  exportCsvUrl,
  exportUrlFromState,
  savingText,
  snoozeUpdate,
} from './call-today'
import { DEFAULT_CUSTOMERS_STATE } from './url-state'

const NOW = new Date('2026-10-24T12:00:00Z')

function action(over: Partial<CustomerAction> = {}): CustomerAction {
  return {
    status: 'done',
    snoozed_until: null,
    note: null,
    updated_at: '2026-10-24T09:00:00Z',
    updated_by: 'analyst',
    expected_loss_at_mark: 138497,
    risk_level_at_mark: 'high',
    active: true,
    returned: false,
    ...over,
  }
}

function item(over: Partial<CallTodayItem> = {}): CallTodayItem {
  return {
    id: 9,
    email: 'user-0009@example.test',
    risk_level: 'high',
    risk_score: 72.4,
    expected_loss: 138497,
    main_reason: 'На балансе мало денег',
    next_action: { code: 'insufficient_funds', title: 'Напомнить пополнить баланс', expected_saving: 120000 },
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

  it('выгрузка из блока — очередь звонков по деньгам', () => {
    expect(CALL_LIST_CSV_URL).toBe('/api/v1/customers/export.csv?sort=expected_loss&order=desc&worklist=true')
  })
})

describe('тело отметки', () => {
  it('сделано и отложить', () => {
    expect(doneUpdate()).toEqual({ status: 'done' })
    expect(doneUpdate('  позвонили ')).toEqual({ status: 'done', note: 'позвонили' })
    expect(snoozeUpdate(3)).toEqual({ status: 'snoozed', snooze_days: 3 })
  })
})

describe('savingText', () => {
  it('показывает экономию только когда она есть', () => {
    expect(savingText({ code: 'x', title: 'y', expected_saving: 138500 })).toBe('Сбережёт до €1 385')
    expect(savingText({ code: 'x', title: 'y', expected_saving: 0 })).toBeNull()
    expect(savingText({ code: 'x', title: 'y' })).toBeNull()
    expect(savingText(null)).toBeNull()
  })
})

describe('actionStatus', () => {
  it('нет отметки', () => {
    expect(actionStatus(null, NOW)).toBeNull()
  })

  it('сделано — с автором и датой', () => {
    const s = actionStatus(action(), NOW)
    expect(s?.tone).toBe('done')
    expect(s?.label).toBe('Сделано')
    expect(s?.detail).toContain('analyst')
  })

  it('риск вырос после «Сделано»', () => {
    expect(actionStatus(action({ active: false, returned: true }), NOW)?.tone).toBe('returned')
  })

  it('отложено и отложка истекла', () => {
    const snoozed = action({ status: 'snoozed', snoozed_until: '2026-10-27T12:00:00Z' })
    expect(actionStatus(snoozed, NOW)?.label).toBe('Отложено до 27 окт')
    expect(actionStatus({ ...snoozed, active: false }, NOW)?.tone).toBe('expired')
  })
})

describe('CallTodayList', () => {
  const render = (items: CallTodayItem[], marked = false) =>
    renderToStaticMarkup(
      createElement(MemoryRouter, null, createElement(CallTodayList, { items, marked, onAction: vi.fn() })),
    )

  it('строка: ссылка в карточку, действие, экономия, сумма и кнопки', () => {
    const html = render([item(), item({ id: 10, email: 'user-0010@example.test', expected_loss: 648, next_action: null })])
    expect(html).toContain('href="/customers/9?sort=expected_loss&amp;order=desc"')
    expect(html).toContain('Напомнить пополнить баланс')
    expect(html).toContain('Сбережёт до €1 200')
    expect(html).toContain('€1 385')
    expect(html).toContain('Сделано')
    expect(html).toContain('Отложить')
    // без готового действия показываем главную причину
    expect(html).toContain('На балансе мало денег')
    expect(html.match(/data-testid="call-today-row"/g)).toHaveLength(2)
  })

  it('в отмеченных — статус и кнопка «Вернуть»', () => {
    const html = render([item({ action: action() })], true)
    expect(html).toContain('data-testid="action-badge"')
    expect(html).toContain('Вернуть')
    expect(html).not.toContain('Отложить')
  })
})

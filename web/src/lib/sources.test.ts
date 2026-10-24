import { describe, expect, it } from 'vitest'
import {
  connectorKindLabel,
  formatRelative,
  intervalLabel,
  intervalOptions,
  isRunActive,
  metricaSnippet,
  parseClientIdsCsv,
  runDetailsText,
  statusView,
  tokenText,
  triggerLabel,
} from './sources'

const NB = ' '

describe('parseClientIdsCsv', () => {
  it('reads rows without a header, comma separated', () => {
    const r = parseClientIdsCsv('1600000000123,12\n1600000000456,7\n')
    expect(r.errors).toEqual([])
    expect(r.header).toBe(false)
    expect(r.items).toEqual([
      { client_id: '1600000000123', account_id: 12 },
      { client_id: '1600000000456', account_id: 7 },
    ])
  })

  it('skips the header and accepts semicolons, tabs, quotes and CRLF', () => {
    expect(parseClientIdsCsv('client_id;account_id\r\n"111";"5"\r\n').items).toEqual([{ client_id: '111', account_id: 5 }])
    expect(parseClientIdsCsv('client_id\taccount_id\n222\t6').items).toEqual([{ client_id: '222', account_id: 6 }])
    // Excel сохраняет CSV с BOM
    expect(parseClientIdsCsv(String.fromCharCode(0xfeff) + 'client_id,account_id\n333,7').items).toEqual([{ client_id: '333', account_id: 7 }])
  })

  it('follows the column order from the header', () => {
    const r = parseClientIdsCsv('account_id,client_id\n9,333\n')
    expect(r.header).toBe(true)
    expect(r.items).toEqual([{ client_id: '333', account_id: 9 }])
  })

  it('reports errors with line numbers and ignores blank lines', () => {
    const r = parseClientIdsCsv('client_id,account_id\n\n12a,1\n444,0\n555\n666,8\n')
    expect(r.items).toEqual([{ client_id: '666', account_id: 8 }])
    expect(r.errors.map((e) => e.line)).toEqual([3, 4, 5])
    expect(r.errors[0]?.message).toContain('12a')
    expect(r.errors[1]?.message).toContain('больше нуля')
    expect(r.errors[2]?.message).toContain('две колонки')
  })

  it('keeps the last account for a repeated clientID', () => {
    const r = parseClientIdsCsv('1,10\n1,11\n2,3')
    expect(r.duplicates).toBe(1)
    expect(r.items).toEqual([
      { client_id: '1', account_id: 11 },
      { client_id: '2', account_id: 3 },
    ])
  })

  it('caps the error list and says how many are left', () => {
    const text = Array.from({ length: 30 }, () => 'x,1').join('\n')
    const r = parseClientIdsCsv(text, 5)
    expect(r.errors).toHaveLength(6)
    expect(r.errors[5]).toEqual({ line: 0, message: 'и ещё 25 ошибок' })
  })

  it('rejects too long clientID', () => {
    expect(parseClientIdsCsv('123456789012345678901,1').errors).toHaveLength(1)
  })
})

describe('status', () => {
  it('maps the connector status to a badge', () => {
    expect(statusView({ last_status: null, run_requested: false })).toEqual({ tone: 'idle', label: 'ещё не запускался' })
    expect(statusView({ last_status: 'ok', run_requested: false }).label).toBe('ок')
    expect(statusView({ last_status: 'error', run_requested: false }).tone).toBe('error')
    expect(statusView({ last_status: 'ok', run_requested: true }).tone).toBe('running')
    expect(statusView({ last_status: 'running', run_requested: false }).label).toBe('идёт запуск')
  })

  it('knows when to poll often', () => {
    expect(isRunActive({ last_status: 'ok', run_requested: true })).toBe(true)
    expect(isRunActive({ last_status: 'running' })).toBe(true)
    expect(isRunActive({ last_status: 'error', run_requested: false })).toBe(false)
    expect(isRunActive(undefined)).toBe(false)
  })

  it('names kinds and triggers in plain words', () => {
    expect(connectorKindLabel('billing_http')).toBe('HTTP API биллинга')
    expect(connectorKindLabel('yandex_metrica')).toBe('Яндекс Метрика')
    expect(connectorKindLabel('other')).toBe('other')
    expect(triggerLabel('manual')).toBe('вручную')
    expect(triggerLabel('schedule')).toBe('по расписанию')
  })
})

describe('schedule and time', () => {
  it('labels intervals', () => {
    expect(intervalLabel(60)).toBe('раз в минуту')
    expect(intervalLabel(300)).toBe(`раз в 5${NB}минут`)
    expect(intervalLabel(3600)).toBe('раз в час')
    expect(intervalLabel(21600)).toBe(`раз в 6${NB}часов`)
    expect(intervalLabel(86400)).toBe('раз в сутки')
    expect(intervalLabel(45)).toBe(`раз в 45${NB}секунд`)
  })

  it('adds a non-standard current interval to the options', () => {
    expect(intervalOptions(300)).toEqual([60, 300, 900, 3600, 21600, 86400])
    expect(intervalOptions(120)).toEqual([60, 120, 300, 900, 3600, 21600, 86400])
  })

  it('formats past and future moments', () => {
    const now = Date.parse('2026-10-24T12:00:00Z')
    expect(formatRelative('2026-10-24T11:59:58Z', now)).toBe('только что')
    expect(formatRelative('2026-10-24T11:59:30Z', now)).toBe(`30${NB}с назад`)
    expect(formatRelative('2026-10-24T12:05:00Z', now)).toBe(`через 5${NB}мин`)
    expect(formatRelative('2026-10-24T09:00:00Z', now)).toBe(`3${NB}ч назад`)
    expect(formatRelative('2026-10-22T12:00:00Z', now)).toBe(`2${NB}дня назад`)
    expect(formatRelative(null, now)).toBe('—')
  })
})

describe('texts', () => {
  it('never shows the token itself', () => {
    expect(tokenText('CONNECTOR_TOKEN_BILLING', true)).toEqual({ ok: true, text: 'токен задан (CONNECTOR_TOKEN_BILLING)' })
    expect(tokenText('CONNECTOR_TOKEN_METRICA', false).text).toContain('задайте переменную окружения CONNECTOR_TOKEN_METRICA')
    expect(tokenText(null, false).ok).toBe(true)
  })

  it('summarises run details', () => {
    expect(runDetailsText({ accounts: 2, services: 5, transactions: 1, skipped: 0 })).toBe(
      `2${NB}аккаунта, 5${NB}услуг, 1${NB}транзакция`,
    )
    expect(runDetailsText({ visits: 120, linked: 80, days: 3 })).toBe(`120${NB}визитов, связано с аккаунтами: 80, 3${NB}дня`)
    expect(runDetailsText(null)).toBe('')
  })

  it('builds the site snippet with the counter', () => {
    expect(metricaSnippet(12345)).toBe("ym(12345, 'setUserID', 'ID_АККАУНТА'); ym(12345, 'params', {account_id: 'ID_АККАУНТА'})")
    expect(metricaSnippet(null)).toContain('ym(СЧЁТЧИК')
  })
})

/**
 * Чистая логика раздела «Источники»: подписи коннекторов, статусы, расписание
 * и разбор CSV с соответствиями clientID → аккаунт. Без React, чтобы покрыть тестами.
 */
import { DASH, NBSP, formatInt, plural } from './format'

export type ConnectorKindCode = 'billing_http' | 'yandex_metrica'

export const CONNECTOR_KIND_LABEL: Record<ConnectorKindCode, string> = {
  billing_http: 'HTTP API биллинга',
  yandex_metrica: 'Яндекс Метрика',
}

export function connectorKindLabel(kind: string): string {
  return (CONNECTOR_KIND_LABEL as Record<string, string>)[kind] ?? kind
}

export interface StatusLike {
  last_status?: 'ok' | 'error' | 'running' | null
  run_requested?: boolean
}

export type StatusTone = 'ok' | 'error' | 'running' | 'idle'

export interface StatusView {
  tone: StatusTone
  label: string
}

/** Идёт ли запуск прямо сейчас (или уже нажали «Запустить сейчас», а исполнитель ещё не взял). */
export function isRunActive(status: StatusLike | null | undefined): boolean {
  return Boolean(status?.run_requested) || status?.last_status === 'running'
}

export function statusView(status: StatusLike | null | undefined): StatusView {
  if (status?.last_status === 'running') return { tone: 'running', label: 'идёт запуск' }
  if (status?.run_requested) return { tone: 'running', label: 'запускается' }
  if (status?.last_status === 'ok') return { tone: 'ok', label: 'ок' }
  if (status?.last_status === 'error') return { tone: 'error', label: 'ошибка' }
  return { tone: 'idle', label: 'ещё не запускался' }
}

export function runStatusLabel(status: 'running' | 'ok' | 'error'): string {
  return status === 'running' ? 'идёт' : status === 'ok' ? 'ок' : 'ошибка'
}

export function triggerLabel(trigger: string): string {
  return trigger === 'manual' ? 'вручную' : 'по расписанию'
}

/** Расписание: 60 → «раз в минуту», 300 → «раз в 5 минут», 3600 → «раз в час». */
export function intervalLabel(seconds: number): string {
  if (!Number.isFinite(seconds) || seconds <= 0) return DASH
  const unit = (n: number, forms: readonly [string, string, string], one: string) =>
    n === 1 ? `раз в ${one}` : `раз в ${n}${NBSP}${plural(n, forms)}`
  if (seconds % 86400 === 0) return unit(seconds / 86400, ['сутки', 'суток', 'суток'], 'сутки')
  if (seconds % 3600 === 0) return unit(seconds / 3600, ['час', 'часа', 'часов'], 'час')
  if (seconds % 60 === 0) return unit(seconds / 60, ['минуту', 'минуты', 'минут'], 'минуту')
  return unit(seconds, ['секунду', 'секунды', 'секунд'], 'секунду')
}

export const INTERVAL_PRESETS = [60, 300, 900, 3600, 21600, 86400] as const

/** Варианты расписания: стандартные и текущее значение, если оно нестандартное. */
export function intervalOptions(current: number): number[] {
  const list: number[] = [...INTERVAL_PRESETS]
  if (Number.isFinite(current) && current > 0 && !list.includes(current)) list.push(current)
  return list.sort((a, b) => a - b)
}

export const LOOKBACK_PRESETS = [7, 14, 30, 60, 90] as const

export function lookbackOptions(current: number | null | undefined): number[] {
  const list: number[] = [...LOOKBACK_PRESETS]
  if (typeof current === 'number' && current > 0 && !list.includes(current)) list.push(current)
  return list.sort((a, b) => a - b)
}

/** «только что», «12 с назад», «через 5 мин», «через 2 ч». */
export function formatRelative(iso: string | null | undefined, nowMs: number = Date.now()): string {
  if (!iso) return DASH
  const at = new Date(iso).getTime()
  if (Number.isNaN(at)) return DASH
  const diff = Math.round((at - nowMs) / 1000)
  const abs = Math.abs(diff)
  if (abs < 5) return diff > 0 ? 'вот-вот' : 'только что'
  let text: string
  if (abs < 60) text = `${abs}${NBSP}с`
  else if (abs < 3600) text = `${Math.floor(abs / 60)}${NBSP}мин`
  else if (abs < 86400) text = `${Math.floor(abs / 3600)}${NBSP}ч`
  else {
    const days = Math.floor(abs / 86400)
    text = `${days}${NBSP}${plural(days, ['день', 'дня', 'дней'])}`
  }
  return diff > 0 ? `через ${text}` : `${text} назад`
}

/** Краткая расшифровка details запуска. */
export function runDetailsText(details: Record<string, unknown> | null | undefined): string {
  if (!details) return ''
  const num = (key: string) => (typeof details[key] === 'number' ? (details[key] as number) : null)
  const parts: string[] = []
  const add = (key: string, forms: readonly [string, string, string]) => {
    const n = num(key)
    if (n !== null) parts.push(`${formatInt(n)}${NBSP}${plural(n, forms)}`)
  }
  // биллинг
  add('accounts', ['аккаунт', 'аккаунта', 'аккаунтов'])
  add('services', ['услуга', 'услуги', 'услуг'])
  add('transactions', ['транзакция', 'транзакции', 'транзакций'])
  // Метрика
  add('visits', ['визит', 'визита', 'визитов'])
  const linked = num('linked')
  if (linked !== null) parts.push(`связано с аккаунтами: ${formatInt(linked)}`)
  add('days', ['день', 'дня', 'дней'])
  const skipped = num('skipped')
  if (skipped) parts.push(`пропущено: ${formatInt(skipped)}`)
  return parts.join(', ')
}

/** Как сказать про токен, не показывая его. */
export function tokenText(tokenEnv: string | null | undefined, present: boolean): { ok: boolean; text: string } {
  if (!tokenEnv) return { ok: true, text: 'токен не нужен' }
  if (present) return { ok: true, text: `токен задан (${tokenEnv})` }
  return { ok: false, text: `токен не задан — задайте переменную окружения ${tokenEnv} у ingest` }
}

/** Строка для сайта: связывает визиты Метрики с аккаунтом после входа в личный кабинет. */
export function metricaSnippet(counterId: number | null | undefined, accountParam = 'account_id'): string {
  const counter = counterId ? String(counterId) : 'СЧЁТЧИК'
  return `ym(${counter}, 'setUserID', 'ID_АККАУНТА'); ym(${counter}, 'params', {${accountParam}: 'ID_АККАУНТА'})`
}

// ---------- CSV clientID → аккаунт ----------

export interface ClientIdRow {
  client_id: string
  account_id: number
}

export interface CsvError {
  line: number
  message: string
}

export interface CsvParseResult {
  items: ClientIdRow[]
  errors: CsvError[]
  /** Сколько строк повторили уже встреченный clientID (взяли последнюю). */
  duplicates: number
  header: boolean
}

const MAX_ACCOUNT_ID = Number.MAX_SAFE_INTEGER

function detectDelimiter(line: string): string {
  if (line.includes('\t')) return '\t'
  if (line.includes(';')) return ';'
  return ','
}

function unquote(value: string): string {
  const v = value.trim()
  return v.length >= 2 && v.startsWith('"') && v.endsWith('"') ? v.slice(1, -1).replace(/""/g, '"').trim() : v
}

const CLIENT_KEYS = ['client_id', 'clientid', 'client id', 'ym:s:clientid']
const ACCOUNT_KEYS = ['account_id', 'accountid', 'account id', 'account', 'аккаунт']

/**
 * Разбирает CSV из двух колонок client_id и account_id. Заголовок необязателен (если есть,
 * по нему определяется порядок колонок), разделитель — запятая, точка с запятой или таб.
 * Пустые строки пропускаются. Номера строк в ошибках считаются с единицы, как в редакторе.
 */
export function parseClientIdsCsv(text: string, maxErrors = 20): CsvParseResult {
  const body = text.charCodeAt(0) === 0xfeff ? text.slice(1) : text
  const lines = body.split(/\r\n|\n|\r/)
  const byClient = new Map<string, number>()
  const errors: CsvError[] = []
  let duplicates = 0
  let header = false
  let clientCol = 0
  let accountCol = 1
  let seenData = false
  let errorCount = 0
  const fail = (line: number, message: string) => {
    errorCount += 1
    if (errors.length < maxErrors) errors.push({ line, message })
  }

  lines.forEach((raw, index) => {
    const lineNo = index + 1
    if (!raw.trim()) return
    const cells = raw.split(detectDelimiter(raw)).map(unquote)

    if (!seenData && !header) {
      const lower = cells.map((c) => c.toLowerCase())
      const ci = lower.findIndex((c) => CLIENT_KEYS.includes(c))
      const ai = lower.findIndex((c) => ACCOUNT_KEYS.includes(c))
      if (ci >= 0 || ai >= 0 || cells.every((c) => !/^\d+$/.test(c))) {
        header = true
        if (ci >= 0 && ai >= 0) {
          clientCol = ci
          accountCol = ai
        } else if (ai === 0) {
          clientCol = 1
          accountCol = 0
        }
        return
      }
    }
    seenData = true

    const filled = cells.filter((c) => c !== '')
    if (filled.length < 2 || cells.length < Math.max(clientCol, accountCol) + 1) {
      fail(lineNo, 'нужно две колонки: client_id и account_id')
      return
    }
    const clientId = cells[clientCol] ?? ''
    const accountRaw = cells[accountCol] ?? ''
    if (!/^\d{1,20}$/.test(clientId)) {
      fail(lineNo, `clientID «${clientId.slice(0, 30)}» должен состоять из цифр, до 20 знаков`)
      return
    }
    if (!/^\d+$/.test(accountRaw) || Number(accountRaw) < 1 || Number(accountRaw) > MAX_ACCOUNT_ID) {
      fail(lineNo, `номер аккаунта «${accountRaw.slice(0, 30)}» должен быть целым числом больше нуля`)
      return
    }
    if (byClient.has(clientId)) duplicates += 1
    byClient.set(clientId, Number(accountRaw))
  })

  if (errorCount > errors.length) {
    errors.push({ line: 0, message: `и ещё ${errorCount - errors.length} ${plural(errorCount - errors.length, ['ошибка', 'ошибки', 'ошибок'])}` })
  }
  const items = [...byClient.entries()].map(([client_id, account_id]) => ({ client_id, account_id }))
  return { items, errors, duplicates, header }
}

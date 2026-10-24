import type { ApiToken, ApiTokenRole, ApiTokenStatus } from '@/api/types'
import { formatAgo, formatDate, formatDateTime, NBSP, plural } from './format'

/** Роли токена. Словарь свой: у токенов уже есть «Маркетолог», а у пользователей роль может появиться позже. */
export const TOKEN_ROLE_LABEL: Record<ApiTokenRole, string> = {
  admin: 'Администратор',
  marketer: 'Маркетолог',
  analyst: 'Аналитик',
}

export const TOKEN_ROLE_HINT: Record<ApiTokenRole, string> = {
  analyst: 'Только чтение: клиенты, риски, аналитика.',
  marketer: 'Те же права, что у маркетолога в панели.',
  admin: 'Может менять данные и настройки, как администратор. Выпускать токены не может.',
}

export const TOKEN_ROLE_OPTIONS: { value: ApiTokenRole; label: string }[] = [
  { value: 'analyst', label: TOKEN_ROLE_LABEL.analyst },
  { value: 'marketer', label: TOKEN_ROLE_LABEL.marketer },
  { value: 'admin', label: TOKEN_ROLE_LABEL.admin },
]

export const TOKEN_STATUS_LABEL: Record<ApiTokenStatus, string> = {
  active: 'Действует',
  expired: 'Истёк',
  revoked: 'Отозван',
}

/** Варианты срока в форме: число дней или null — бессрочный. */
export const EXPIRY_OPTIONS = [
  { value: '30', label: '30 дней', days: 30 },
  { value: '90', label: '90 дней', days: 90 },
  { value: '365', label: 'Год', days: 365 },
  { value: 'never', label: 'Без срока', days: null },
] as const

export type ExpiryChoice = (typeof EXPIRY_OPTIONS)[number]['value']

export function expiryDays(choice: ExpiryChoice): number | null {
  return EXPIRY_OPTIONS.find((o) => o.value === choice)?.days ?? null
}

export const TOKEN_NAME_RE = /^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$/

/** null — имя подходит, иначе текст ошибки для формы. */
export function tokenNameError(name: string): string | null {
  const value = name.trim()
  if (!value) return 'Придумайте имя, по нему токен будет видно в журнале.'
  if (value.length > 64) return 'Имя длиннее 64 символов.'
  if (!TOKEN_NAME_RE.test(value)) return 'Только латиница, цифры, точка, дефис и подчёркивание; начинается с буквы или цифры.'
  return null
}

const IPV4_RE = /^(25[0-5]|2[0-4]\d|1\d\d|[1-9]?\d)(\.(25[0-5]|2[0-4]\d|1\d\d|[1-9]?\d)){3}$/

function isIpv6(value: string): boolean {
  if (!/^[0-9a-fA-F:.]+$/.test(value) || !value.includes(':')) return false
  const doubles = value.split('::').length - 1
  if (doubles > 1) return false
  const groups = value.split(':').filter(Boolean)
  return groups.length <= 8 && groups.every((g) => /^[0-9a-fA-F]{1,4}$/.test(g) || IPV4_RE.test(g))
}

/** IP или подсеть: 203.0.113.10, 10.0.0.0/8, 2001:db8::/32. Окончательно проверяет сервер. */
export function isIpOrCidr(value: string): boolean {
  const [address = '', prefix, ...rest] = value.split('/')
  if (rest.length > 0) return false
  const v4 = IPV4_RE.test(address)
  const v6 = !v4 && isIpv6(address)
  if (!v4 && !v6) return false
  if (prefix === undefined) return true
  if (!/^\d{1,3}$/.test(prefix)) return false
  return Number(prefix) <= (v4 ? 32 : 128)
}

/** Список адресов из textarea: по одному в строке, через запятую, точку с запятой или пробел. */
export function parseIpList(text: string): { ips: string[]; invalid: string[] } {
  const ips: string[] = []
  const invalid: string[] = []
  for (const raw of text.split(/[\s,;]+/)) {
    const value = raw.trim()
    if (!value) continue
    if (!isIpOrCidr(value)) {
      if (!invalid.includes(value)) invalid.push(value)
    } else if (!ips.includes(value)) {
      ips.push(value)
    }
  }
  return { ips, invalid }
}

/** Лимит запросов в минуту: пусто — общий лимит (null), иначе целое 1…100 000. */
export function parseRateLimit(text: string): { value: number | null; error: string | null } {
  // \s ловит и неразрывный пробел, если число вставили из буфера как «10 000»
  const raw = text.replace(/\s/g, '')
  if (!raw) return { value: null, error: null }
  if (!/^\d+$/.test(raw)) return { value: null, error: 'Лимит — целое число запросов в минуту.' }
  const value = Number(raw)
  if (value < 1 || value > 100_000) return { value: null, error: 'Лимит от 1 до 100 000 запросов в минуту.' }
  return { value, error: null }
}

/** «5 мин назад · 203.0.113.10» или «ещё не использовался». */
export function formatLastUsed(token: Pick<ApiToken, 'last_used_at' | 'last_used_ip'>, nowMs: number = Date.now()): string {
  if (!token.last_used_at) return 'ещё не использовался'
  const at = new Date(token.last_used_at).getTime()
  if (Number.isNaN(at)) return 'ещё не использовался'
  const ago = formatAgo(Math.min(at, nowMs), nowMs)
  return token.last_used_ip ? `${ago} · ${token.last_used_ip}` : ago
}

/** Срок в списке: «до 22 янв 2027», «бессрочный», «истёк 3 окт», «отозван 24 окт, 14:05». */
export function formatExpiry(
  token: Pick<ApiToken, 'status' | 'expires_at' | 'revoked_at'>,
  now: Date = new Date(),
): string {
  if (token.status === 'revoked') {
    return token.revoked_at ? `отозван ${formatDateTime(token.revoked_at, { now })}` : 'отозван'
  }
  if (!token.expires_at) return token.status === 'expired' ? 'истёк' : 'бессрочный'
  const date = formatDate(token.expires_at, { short: true, now })
  if (token.status === 'expired') return `истёк ${date}`
  const days = Math.ceil((new Date(token.expires_at).getTime() - now.getTime()) / 86_400_000)
  if (days <= 0) return `истекает сегодня`
  if (days <= 14) return `до ${date} (ещё ${days}${NBSP}${plural(days, ['день', 'дня', 'дней'])})`
  return `до ${date}`
}

/** Скоро ли истечёт действующий токен — чтобы подсветить строку заранее. */
export function expiresSoon(token: Pick<ApiToken, 'status' | 'expires_at'>, nowMs: number = Date.now(), days = 14): boolean {
  if (token.status !== 'active' || !token.expires_at) return false
  const left = new Date(token.expires_at).getTime() - nowMs
  return !Number.isNaN(left) && left <= days * 86_400_000
}

export function formatAllowedIps(ips: readonly string[]): string {
  return ips.length ? ips.join(', ') : 'с любого адреса'
}

/** Готовый пример запроса для блока «Как подключить». */
export function curlExample(origin: string, secret?: string | null): string {
  const base = origin.replace(/\/+$/, '')
  return `curl -H "Authorization: Bearer ${secret || '<токен>'}" ${base}/api/v1/customers`
}

// ——— еженедельный отчёт

export const WEEKDAYS = [
  { value: 1, label: 'Понедельник', short: 'пн' },
  { value: 2, label: 'Вторник', short: 'вт' },
  { value: 3, label: 'Среда', short: 'ср' },
  { value: 4, label: 'Четверг', short: 'чт' },
  { value: 5, label: 'Пятница', short: 'пт' },
  { value: 6, label: 'Суббота', short: 'сб' },
  { value: 7, label: 'Воскресенье', short: 'вс' },
] as const

export const SEND_TIME_RE = /^([01]\d|2[0-3]):[0-5]\d$/

const EMAIL_RE = /^[^\s@,;]+@[^\s@,;]+\.[^\s@,;]+$/

/** Получатели отчёта: адреса через запятую, пробел или с новой строки. */
export function parseEmails(text: string): { emails: string[]; invalid: string[] } {
  const emails: string[] = []
  const invalid: string[] = []
  for (const raw of text.split(/[\s,;]+/)) {
    const value = raw.trim()
    if (!value) continue
    if (!EMAIL_RE.test(value)) {
      if (!invalid.includes(value)) invalid.push(value)
      continue
    }
    if (!emails.some((e) => e.toLowerCase() === value.toLowerCase())) emails.push(value)
  }
  return { emails, invalid }
}

/** «по понедельникам в 09:00 (Europe/Moscow)». */
export function describeSchedule(s: { enabled: boolean; weekday: number; send_time: string; timezone: string }): string {
  if (!s.enabled) return 'Рассылка выключена'
  return `Уходит ${EVERY_WEEKDAY[s.weekday] ?? 'раз в неделю'} в ${s.send_time} (${s.timezone})`
}

const EVERY_WEEKDAY: Record<number, string> = {
  1: 'по понедельникам',
  2: 'по вторникам',
  3: 'по средам',
  4: 'по четвергам',
  5: 'по пятницам',
  6: 'по субботам',
  7: 'по воскресеньям',
}

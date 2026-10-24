import type { AuditActorType, AuditEvent, AuditQuery } from '@/api/types'

export const AUDIT_PAGE_SIZE = 50
export const AUDIT_EXPORT_PATH = '/api/v1/audit/export.csv'

export const ACTOR_TYPES = ['user', 'token', 'system', 'anonymous'] as const satisfies readonly AuditActorType[]

export const ACTOR_TYPE_LABEL: Record<AuditActorType, string> = {
  user: 'Сотрудник',
  token: 'API-токен',
  system: 'Система',
  anonymous: 'Без входа',
}

/** Фильтры журнала — то, что живёт в адресной строке. */
export interface AuditFilters {
  q: string
  action: string
  actorType: AuditActorType | ''
  failedOnly: boolean
  dateFrom: string
  dateTo: string
  page: number
}

export const DEFAULT_AUDIT_FILTERS: AuditFilters = {
  q: '',
  action: '',
  actorType: '',
  failedOnly: false,
  dateFrom: '',
  dateTo: '',
  page: 1,
}

const DATE_RE = /^\d{4}-\d{2}-\d{2}$/

function validDate(raw: string | null): string {
  if (!raw || !DATE_RE.test(raw)) return ''
  const date = new Date(`${raw}T00:00:00Z`)
  return !Number.isNaN(date.getTime()) && date.toISOString().startsWith(raw) ? raw : ''
}

/** Читает фильтры из query-строки; мусор молча отбрасывается. */
export function parseAuditFilters(params: URLSearchParams): AuditFilters {
  const actorType = params.get('actor_type') ?? ''
  const page = Number(params.get('page'))
  return {
    q: (params.get('q') ?? '').slice(0, 200),
    action: (params.get('action') ?? '').trim().slice(0, 80),
    actorType: (ACTOR_TYPES as readonly string[]).includes(actorType) ? (actorType as AuditActorType) : '',
    failedOnly: params.get('success') === 'false',
    dateFrom: validDate(params.get('date_from')),
    dateTo: validDate(params.get('date_to')),
    page: Number.isInteger(page) && page > 0 ? page : 1,
  }
}

/** Обратно в URL: только то, что отличается от значений по умолчанию. */
export function serializeAuditFilters(filters: AuditFilters): URLSearchParams {
  const params = new URLSearchParams()
  const q = filters.q.trim()
  if (q) params.set('q', q)
  if (filters.action) params.set('action', filters.action)
  if (filters.actorType) params.set('actor_type', filters.actorType)
  if (filters.failedOnly) params.set('success', 'false')
  if (filters.dateFrom) params.set('date_from', filters.dateFrom)
  if (filters.dateTo) params.set('date_to', filters.dateTo)
  if (filters.page > 1) params.set('page', String(filters.page))
  return params
}

/** Фильтры без страницы — общие для списка и выгрузки CSV. */
function filterQuery(filters: AuditFilters): AuditQuery {
  const query: AuditQuery = {}
  const q = filters.q.trim()
  if (q) query.q = q
  if (filters.action) query.action = filters.action
  if (filters.actorType) query.actor_type = filters.actorType
  if (filters.failedOnly) query.success = false
  // перепутанные даты меняем местами, а не получаем пустой журнал
  let from = filters.dateFrom
  let to = filters.dateTo
  if (from && to && from > to) [from, to] = [to, from]
  if (from) query.date_from = from
  if (to) query.date_to = to
  return query
}

/** Параметры GET /api/v1/audit. */
export function toAuditQuery(filters: AuditFilters, pageSize = AUDIT_PAGE_SIZE): AuditQuery {
  return { ...filterQuery(filters), page: filters.page, page_size: pageSize }
}

/** Ссылка «Скачать CSV» с теми же фильтрами, что сейчас на экране (без пагинации). */
export function auditExportUrl(filters: AuditFilters): string {
  const params = new URLSearchParams()
  for (const [key, value] of Object.entries(filterQuery(filters))) {
    if (value !== undefined && value !== null && value !== '') params.set(key, String(value))
  }
  const qs = params.toString()
  return qs ? `${AUDIT_EXPORT_PATH}?${qs}` : AUDIT_EXPORT_PATH
}

export function hasActiveFilters(filters: AuditFilters): boolean {
  return Boolean(
    filters.q.trim() || filters.action || filters.actorType || filters.failedOnly || filters.dateFrom || filters.dateTo,
  )
}

/** Имя исполнителя без служебной приставки: token:aeza-crm → aeza-crm. */
export function actorName(event: Pick<AuditEvent, 'actor' | 'actor_type'>): string {
  if (event.actor_type === 'token' && event.actor.startsWith('token:')) return event.actor.slice('token:'.length)
  if (event.actor_type === 'anonymous' && !event.actor) return 'без входа'
  return event.actor || '—'
}

/** Бейдж рядом с именем: людей не помечаем, остальных — да. */
export function actorBadge(actorType: AuditActorType): string | null {
  if (actorType === 'token') return 'токен'
  if (actorType === 'system') return 'система'
  if (actorType === 'anonymous') return 'без входа'
  return null
}

const OBJECT_TYPE_LABEL: Record<string, string> = {
  user: 'пользователь',
  token: 'токен',
  customer: 'клиент',
  campaign: 'рассылка',
  template: 'шаблон',
  settings: 'настройки',
  model: 'модель',
  snapshot: 'выгрузка',
  report: 'отчёт',
  session: 'сессия',
}

/** «токен aeza-crm», «клиент 42» или просто тип/идентификатор, если чего-то нет. */
export function objectLabel(event: Pick<AuditEvent, 'object_type' | 'object_id'>): string {
  const type = event.object_type ? (OBJECT_TYPE_LABEL[event.object_type] ?? event.object_type) : ''
  const id = event.object_id ?? ''
  return [type, id].filter(Boolean).join(' ') || '—'
}

export function hasDetails(details: AuditEvent['details'] | null | undefined): boolean {
  return !!details && Object.keys(details).length > 0
}

export function formatDetails(details: AuditEvent['details'] | null | undefined): string {
  return hasDetails(details) ? JSON.stringify(details, null, 2) : ''
}

export function totalPages(total: number, pageSize = AUDIT_PAGE_SIZE): number {
  return Math.max(1, Math.ceil(Math.max(0, total) / pageSize))
}

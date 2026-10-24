import { z } from 'zod'
import type { CustomerSort, RiskLevel, SortOrder } from '@/api/types'

export const SORT_FIELDS = ['id', 'email', 'balance', 'total_replenished', 'risk_score', 'expected_loss'] as const satisfies readonly CustomerSort[]
export const RISK_LEVELS = ['high', 'medium', 'low', 'unknown'] as const satisfies readonly RiskLevel[]
export const PAGE_SIZES = [10, 25, 50, 100] as const

export type ReasonScope = 'main' | 'any'
export type CustomersView = 'table' | 'chart'

export interface CustomersState {
  sort: CustomerSort
  order: SortOrder
  levels: RiskLevel[]
  search: string
  page: number
  pageSize: number
  /** Код причины риска: только клиенты с ней. */
  reason?: string
  /** main — причина главная, по умолчанию (undefined) — любая из причин. */
  reasonScope?: ReasonScope
  /** Диапазон score из гистограммы: от scoreMin включительно до scoreMax (100 — включительно). */
  scoreMin?: number
  scoreMax?: number
  /** Вид списка: таблица (по умолчанию) или график «риск × потеря». */
  view?: CustomersView
}

const REASON_RE = /^[a-z][a-z0-9_]{0,63}$/

function scoreParam(raw: string | null): number | undefined {
  if (raw === null || raw.trim() === '') return undefined
  const value = Number(raw)
  return Number.isFinite(value) && value >= 0 && value <= 100 ? value : undefined
}

export const DEFAULT_CUSTOMERS_STATE: CustomersState = {
  sort: 'risk_score',
  order: 'desc',
  levels: [],
  search: '',
  page: 1,
  pageSize: 25,
}

const sortSchema = z.enum(SORT_FIELDS)
const orderSchema = z.enum(['asc', 'desc'])
const levelSchema = z.enum(RISK_LEVELS)
const positiveInt = z.coerce.number().int().positive()

/** Читает состояние таблицы из query-строки; мусор молча заменяется значениями по умолчанию. */
export function parseCustomersState(params: URLSearchParams): CustomersState {
  const d = DEFAULT_CUSTOMERS_STATE
  const sort = sortSchema.safeParse(params.get('sort'))
  const order = orderSchema.safeParse(params.get('order'))
  const page = positiveInt.safeParse(params.get('page') ?? undefined)
  const size = positiveInt.safeParse(params.get('page_size') ?? undefined)

  const levels: RiskLevel[] = []
  for (const raw of (params.get('level') ?? '').split(',')) {
    const parsed = levelSchema.safeParse(raw.trim())
    if (parsed.success && !levels.includes(parsed.data)) levels.push(parsed.data)
  }
  levels.sort((a, b) => RISK_LEVELS.indexOf(a) - RISK_LEVELS.indexOf(b))

  const state: CustomersState = {
    sort: sort.success ? sort.data : d.sort,
    order: order.success ? order.data : sort.success ? defaultOrderFor(sort.data) : d.order,
    levels,
    search: (params.get('search') ?? '').slice(0, 200),
    page: page.success ? page.data : d.page,
    pageSize: size.success && size.data <= 200 ? size.data : d.pageSize,
  }
  const reason = params.get('reason')?.trim()
  if (reason && REASON_RE.test(reason)) {
    state.reason = reason
    if (params.get('reason_scope') === 'main') state.reasonScope = 'main'
  }
  const scoreMin = scoreParam(params.get('score_min'))
  const scoreMax = scoreParam(params.get('score_max'))
  // перевёрнутый диапазон молча отбрасываем: сервер ответил бы 422
  if (!(scoreMin !== undefined && scoreMax !== undefined && scoreMin >= scoreMax)) {
    if (scoreMin !== undefined) state.scoreMin = scoreMin
    if (scoreMax !== undefined) state.scoreMax = scoreMax
  }
  if (params.get('view') === 'chart') state.view = 'chart'
  return state
}

/** Обратное преобразование: в URL попадают только отличия от значений по умолчанию. */
export function serializeCustomersState(state: CustomersState): URLSearchParams {
  const d = DEFAULT_CUSTOMERS_STATE
  const params = new URLSearchParams()
  if (state.sort !== d.sort || state.order !== d.order) {
    params.set('sort', state.sort)
    params.set('order', state.order)
  }
  if (state.levels.length > 0) params.set('level', state.levels.join(','))
  const search = state.search.trim()
  if (search) params.set('search', search)
  if (state.reason) {
    params.set('reason', state.reason)
    if (state.reasonScope === 'main') params.set('reason_scope', 'main')
  }
  if (state.scoreMin !== undefined) params.set('score_min', String(state.scoreMin))
  if (state.scoreMax !== undefined) params.set('score_max', String(state.scoreMax))
  if (state.page > 1) params.set('page', String(state.page))
  if (state.pageSize !== d.pageSize) params.set('page_size', String(state.pageSize))
  if (state.view === 'chart') params.set('view', 'chart')
  return params
}

/** Без фильтра по причине (кнопка сброса на чипе «Причина: …»). */
export function clearReason(state: CustomersState): CustomersState {
  const next = { ...state, page: 1 }
  delete next.reason
  delete next.reasonScope
  return next
}

/** Без диапазона score (чип «Риск: 30–40»). */
export function clearScoreRange(state: CustomersState): CustomersState {
  const next = { ...state, page: 1 }
  delete next.scoreMin
  delete next.scoreMax
  return next
}

/** Ссылка на список клиентов с причиной. scope=main — у кого она главная. */
export function reasonHref(code: string, scope: ReasonScope = 'any'): string {
  const params = new URLSearchParams({ reason: code })
  if (scope === 'main') params.set('reason_scope', 'main')
  return `/customers?${params.toString()}`
}

/** Ссылка на клиентов в диапазоне score: корзина гистограммы. */
export function scoreRangeHref(from: number, to: number): string {
  return `/customers?${new URLSearchParams({ score_min: String(from), score_max: String(to) }).toString()}`
}

/** Текстовые столбцы логично начинать по возрастанию, числовые — с самых больших. */
export function defaultOrderFor(sort: CustomerSort): SortOrder {
  return sort === 'id' || sort === 'email' ? 'asc' : 'desc'
}

/** Клик по заголовку: новый столбец — порядок по умолчанию, тот же — переворот. */
export function nextSort(state: CustomersState, column: CustomerSort): CustomersState {
  if (state.sort === column) {
    return { ...state, order: state.order === 'asc' ? 'desc' : 'asc', page: 1 }
  }
  return { ...state, sort: column, order: defaultOrderFor(column), page: 1 }
}

export function toggleLevel(state: CustomersState, level: RiskLevel): CustomersState {
  const has = state.levels.includes(level)
  const levels = has ? state.levels.filter((l) => l !== level) : [...state.levels, level]
  levels.sort((a, b) => RISK_LEVELS.indexOf(a) - RISK_LEVELS.indexOf(b))
  return { ...state, levels, page: 1 }
}

/** Параметры запроса к GET /api/v1/customers. */
export function toCustomersQuery(state: CustomersState) {
  return {
    sort: state.sort,
    order: state.order,
    level: state.levels.length ? state.levels.join(',') : undefined,
    search: state.search.trim() || undefined,
    page: state.page,
    page_size: state.pageSize,
    reason: state.reason,
    reason_scope: state.reason ? state.reasonScope : undefined,
    score_min: state.scoreMin,
    score_max: state.scoreMax,
  }
}

/** Безопасный ?next=: только относительный путь внутри приложения. */
export function safeNext(raw: string | null | undefined, fallback = '/'): string {
  if (!raw) return fallback
  if (!raw.startsWith('/') || raw.startsWith('//') || raw.startsWith('/\\')) return fallback
  if (raw.startsWith('/login')) return fallback
  if ([...raw].some((ch) => ch.charCodeAt(0) < 32)) return fallback
  return raw
}

export function loginUrl(next: string): string {
  const target = safeNext(next, '/')
  return target === '/' ? '/login' : `/login?next=${encodeURIComponent(target)}`
}

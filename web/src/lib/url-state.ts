import { z } from 'zod'
import type { CustomerSort, RiskLevel, SortOrder } from '@/api/types'

export const SORT_FIELDS = ['id', 'email', 'balance', 'total_replenished', 'risk_score', 'expected_loss'] as const satisfies readonly CustomerSort[]
export const RISK_LEVELS = ['high', 'medium', 'low', 'unknown'] as const satisfies readonly RiskLevel[]
export const PAGE_SIZES = [10, 25, 50, 100] as const

export interface CustomersState {
  sort: CustomerSort
  order: SortOrder
  levels: RiskLevel[]
  search: string
  page: number
  pageSize: number
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

  return {
    sort: sort.success ? sort.data : d.sort,
    order: order.success ? order.data : sort.success ? defaultOrderFor(sort.data) : d.order,
    levels,
    search: (params.get('search') ?? '').slice(0, 200),
    page: page.success ? page.data : d.page,
    pageSize: size.success && size.data <= 200 ? size.data : d.pageSize,
  }
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
  if (state.page > 1) params.set('page', String(state.page))
  if (state.pageSize !== d.pageSize) params.set('page_size', String(state.pageSize))
  return params
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

import type { ApiErrorBody } from './types'

export class ApiError extends Error {
  readonly status: number
  readonly code: string
  readonly retryAfter: number | null
  readonly requestId: string | null

  constructor(status: number, code: string, message: string, retryAfter: number | null = null, requestId: string | null = null) {
    super(message)
    this.name = 'ApiError'
    this.status = status
    this.code = code
    this.retryAfter = retryAfter
    this.requestId = requestId
  }
}

export function isApiError(error: unknown): error is ApiError {
  return error instanceof ApiError
}

type UnauthorizedHandler = () => void
let onUnauthorized: UnauthorizedHandler | null = null

export function setUnauthorizedHandler(handler: UnauthorizedHandler | null) {
  onUnauthorized = handler
}

export type QueryValue = string | number | boolean | null | undefined

export interface RequestOptions {
  method?: 'GET' | 'POST' | 'PUT' | 'PATCH' | 'DELETE'
  query?: Record<string, QueryValue>
  body?: unknown
  signal?: AbortSignal
  /** Не уводить на /login при 401 (логин, проверка сессии на странице входа). */
  allowUnauthorized?: boolean
}

function buildUrl(path: string, query?: Record<string, QueryValue>): string {
  if (!query) return path
  const params = new URLSearchParams()
  for (const [key, value] of Object.entries(query)) {
    if (value === undefined || value === null || value === '') continue
    params.set(key, String(value))
  }
  const qs = params.toString()
  return qs ? `${path}?${qs}` : path
}

const FALLBACK_MESSAGES: Record<number, string> = {
  400: 'Запрос не принят сервером.',
  401: 'Сессия закончилась, войдите снова.',
  403: 'Недостаточно прав для этого действия.',
  404: 'Ничего не нашлось.',
  413: 'Файл слишком большой.',
  422: 'Данные не прошли проверку.',
  429: 'Слишком много попыток. Подождите немного.',
  502: 'Сервис временно недоступен.',
  503: 'Сервис временно недоступен.',
  504: 'Сервер не ответил вовремя.',
}

async function toApiError(response: Response): Promise<ApiError> {
  let body: Partial<ApiErrorBody> | null
  try {
    body = (await response.json()) as Partial<ApiErrorBody>
  } catch {
    body = null
  }
  const retryHeader = response.headers.get('Retry-After')
  const retryAfter = retryHeader && /^\d+$/.test(retryHeader) ? Number(retryHeader) : null
  const err = body?.error
  const message =
    (typeof err?.message === 'string' && err.message.trim()) ||
    FALLBACK_MESSAGES[response.status] ||
    (response.status >= 500 ? 'Ошибка на сервере. Попробуйте ещё раз.' : 'Не удалось выполнить запрос.')
  return new ApiError(
    response.status,
    typeof err?.code === 'string' ? err.code : `http_${response.status}`,
    message,
    retryAfter,
    typeof err?.request_id === 'string' ? err.request_id : null,
  )
}

export async function request<T>(path: string, options: RequestOptions = {}): Promise<T> {
  const { method = 'GET', query, body, signal, allowUnauthorized = false } = options
  const headers: Record<string, string> = { Accept: 'application/json' }
  if (body !== undefined) headers['Content-Type'] = 'application/json'

  let response: Response
  try {
    response = await fetch(buildUrl(path, query), {
      method,
      headers,
      body: body === undefined ? undefined : JSON.stringify(body),
      credentials: 'same-origin',
      signal,
    })
  } catch (error) {
    if (error instanceof DOMException && error.name === 'AbortError') throw error
    throw new ApiError(0, 'network_error', 'Нет связи с сервером. Проверьте подключение.')
  }

  if (!response.ok) {
    const error = await toApiError(response)
    if (response.status === 401 && !allowUnauthorized) onUnauthorized?.()
    throw error
  }

  if (response.status === 204) return undefined as T
  const text = await response.text()
  if (!text) return undefined as T
  try {
    return JSON.parse(text) as T
  } catch {
    throw new ApiError(response.status, 'bad_response', 'Сервер вернул неожиданный ответ.')
  }
}

export function errorMessage(error: unknown): string {
  if (isApiError(error)) return error.message
  return 'Что-то пошло не так. Попробуйте обновить страницу.'
}

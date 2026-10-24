import { request } from './client'
import type { components } from './schema'

/** Лента оповещений: отдаёт сервис alerts (services/alerts), шлюз ведёт туда /api/v1/alerts. */
export type AlertItem = components['schemas']['AlertItem']
export type AlertFeed = components['schemas']['AlertPage']
export type AlertKind = AlertItem['kind']

export const ALERTS_PAGE_SIZE = 30

export const alertsQueryKey = ['alerts'] as const

export function fetchAlerts(signal?: AbortSignal): Promise<AlertFeed> {
  return request<AlertFeed>('/api/v1/alerts', { query: { page: 1, page_size: ALERTS_PAGE_SIZE }, signal })
}

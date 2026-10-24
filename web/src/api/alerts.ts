import { request } from './client'

/**
 * Лента оповещений отдаёт отдельный сервис alerts (services/alerts), а не api,
 * поэтому типы описаны здесь, а не берутся из openapi.yaml.
 */
export type AlertKind = 'high_risk' | 'loss_jump' | 'total_loss_jump'

export interface AlertItem {
  id: number
  kind: AlertKind
  customer_id: number | null
  email: string | null
  risk_level: string | null
  prev_level: string | null
  /** евроценты */
  expected_loss: number | null
  prev_loss: number | null
  message: string
  created_at: string | null
  /** путь в панели: карточка клиента или список высокого риска */
  link: string
}

export interface AlertFeed {
  items: AlertItem[]
  total: number
  page: number
  page_size: number
  latest_id: number | null
  channels: { feed: boolean; webhook: boolean; telegram: boolean }
}

export const ALERTS_PAGE_SIZE = 30

export const alertsQueryKey = ['alerts'] as const

export function fetchAlerts(signal?: AbortSignal): Promise<AlertFeed> {
  return request<AlertFeed>('/api/v1/alerts', { query: { page: 1, page_size: ALERTS_PAGE_SIZE }, signal })
}

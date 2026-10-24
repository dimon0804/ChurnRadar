import { request } from './client'
import type { components } from './schema'

/** Уведомления: отдаёт сервис alerts (services/alerts), шлюз ведёт туда /api/v1/alerts/*. */
type Schemas = components['schemas']

export type AlertItem = Schemas['AlertItem']
export type AlertFeed = Schemas['AlertPage']
export type AlertKind = AlertItem['kind']
export type AlertEventCode = Schemas['AlertEventCode']
export type AlertChannel = Schemas['AlertChannel']
export type AlertEventPreference = Schemas['AlertEventPreference']
export type AlertPreferencesInput = Schemas['AlertPreferencesInput']
export type AlertPreferencesResponse = Schemas['AlertPreferencesResponse']
export type AlertEventDefinition = Schemas['AlertEventDefinition']
export type AlertSubscriber = Schemas['AlertSubscriber']
export type NotificationItem = Schemas['NotificationItem']
export type NotificationInbox = Schemas['NotificationInbox']

export interface TestResult {
  channel: AlertChannel
  ok: boolean
  reason?: string | null
}

export const ALERTS_PAGE_SIZE = 30

export const alertsQueryKey = ['alerts'] as const
export const inboxQueryKey = ['alerts', 'inbox'] as const
export const prefsQueryKey = ['alerts', 'preferences'] as const
export const subscribersQueryKey = ['alerts', 'subscribers'] as const

export const alertsApi = {
  journal: (signal?: AbortSignal) =>
    request<AlertFeed>('/api/v1/alerts', { query: { page: 1, page_size: ALERTS_PAGE_SIZE }, signal }),
  inbox: (signal?: AbortSignal) =>
    request<NotificationInbox>('/api/v1/alerts/notifications', { query: { limit: ALERTS_PAGE_SIZE }, signal }),
  markRead: () => request<void>('/api/v1/alerts/notifications/read', { method: 'POST' }),
  preferences: (signal?: AbortSignal) => request<AlertPreferencesResponse>('/api/v1/alerts/preferences', { signal }),
  savePreferences: (body: AlertPreferencesInput) =>
    request<AlertPreferencesResponse>('/api/v1/alerts/preferences', { method: 'PUT', body }),
  sendTest: () => request<{ results: TestResult[] }>('/api/v1/alerts/preferences/test', { method: 'POST' }),
  telegramLink: () => request<{ url: string; expires_in_s: number }>('/api/v1/alerts/telegram/link', { method: 'POST' }),
  telegramUnlink: () => request<void>('/api/v1/alerts/telegram/link', { method: 'DELETE' }),
  subscribers: (signal?: AbortSignal) =>
    request<{ items: AlertSubscriber[] }>('/api/v1/alerts/preferences/users', { signal }),
  resetSubscriber: (username: string) =>
    request<void>(`/api/v1/alerts/preferences/users/${encodeURIComponent(username)}`, { method: 'DELETE' }),
}

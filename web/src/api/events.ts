import { z } from 'zod'
import type { ScoresUpdatedEvent, SettingsUpdatedEvent, SourceUpdatedEvent } from './types'

const levelCounts = z
  .object({
    high: z.number().optional(),
    medium: z.number().optional(),
    low: z.number().optional(),
    unknown: z.number().optional(),
  })
  .partial()

const scoresUpdated = z.object({
  model_version: z.number(),
  scored_at: z.string(),
  changed_customer_ids: z.array(z.number()).catch([]),
  full: z.boolean().optional(),
  duration_ms: z.number().optional(),
  summary: z
    .object({
      customers_total: z.number(),
      by_level: levelCounts,
      expected_loss: z.number(),
      exposure: z.number(),
    })
    .catch({ customers_total: 0, by_level: {}, expected_loss: 0, exposure: 0 }),
}) satisfies z.ZodType<ScoresUpdatedEvent>

const settingsUpdated = z.object({
  version: z.number(),
  updated_by: z.string().nullable().catch(null),
}) satisfies z.ZodType<SettingsUpdatedEvent>

const sourceUpdated = z.object({
  kind: z.enum(['snapshot', 'changes']).catch('changes'),
  snapshot_id: z.string().nullable().optional(),
  counts: z
    .object({ accounts: z.number(), services: z.number(), transactions: z.number() })
    .partial()
    .catch({}),
}) satisfies z.ZodType<SourceUpdatedEvent>

export const LIVE_EVENT_TYPES = ['scores.updated', 'settings.updated', 'source.updated'] as const
export type LiveEventType = (typeof LIVE_EVENT_TYPES)[number]

export interface LiveEventMap {
  'scores.updated': ScoresUpdatedEvent
  'settings.updated': SettingsUpdatedEvent
  'source.updated': SourceUpdatedEvent
}

const schemas = {
  'scores.updated': scoresUpdated,
  'settings.updated': settingsUpdated,
  'source.updated': sourceUpdated,
} as const

/**
 * Разбирает data из SSE. Notifier может прислать как сам payload, так и весь конверт
 * NATS ({event_id, type, payload}) — поддерживаем оба варианта.
 */
export function parseLiveEvent<T extends LiveEventType>(type: T, raw: string): LiveEventMap[T] | null {
  let data: unknown
  try {
    data = JSON.parse(raw)
  } catch {
    return null
  }
  if (data && typeof data === 'object' && 'payload' in data && 'event_id' in data) {
    data = (data as { payload: unknown }).payload
  }
  const parsed = schemas[type].safeParse(data)
  return parsed.success ? (parsed.data as LiveEventMap[T]) : null
}

import { useQueryClient } from '@tanstack/react-query'
import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState, type ReactNode } from 'react'
import { api, queryKeys } from '@/api/endpoints'
import { LIVE_EVENT_TYPES, parseLiveEvent, type LiveEventMap, type LiveEventType } from '@/api/events'

export type LiveStatus = 'connecting' | 'online' | 'reconnecting'

export interface LastRecalc {
  modelVersion: number
  scoredAt: string
  receivedAt: number
  durationMs: number | null
}

type Listener<T extends LiveEventType> = (payload: LiveEventMap[T]) => void

interface LiveContextValue {
  status: LiveStatus
  lastRecalc: LastRecalc | null
  /** id клиента -> метка времени последнего изменения (для подсветки строк) */
  changed: ReadonlyMap<number, number>
  subscribe: <T extends LiveEventType>(type: T, listener: Listener<T>) => () => void
}

const LiveContext = createContext<LiveContextValue | null>(null)

const STREAM_URL = '/api/v1/stream'
const HIGHLIGHT_MS = 2600
const BACKOFF_MS = [1000, 2000, 4000, 8000, 15000]

export function LiveProvider({ children }: { children: ReactNode }) {
  const queryClient = useQueryClient()
  const [status, setStatus] = useState<LiveStatus>('connecting')
  const [lastRecalc, setLastRecalc] = useState<LastRecalc | null>(null)
  const [changed, setChanged] = useState<ReadonlyMap<number, number>>(() => new Map())
  const listeners = useRef(new Map<LiveEventType, Set<Listener<LiveEventType>>>())

  const subscribe = useCallback(<T extends LiveEventType>(type: T, listener: Listener<T>) => {
    let set = listeners.current.get(type)
    if (!set) {
      set = new Set()
      listeners.current.set(type, set)
    }
    set.add(listener as unknown as Listener<LiveEventType>)
    return () => {
      set.delete(listener as unknown as Listener<LiveEventType>)
    }
  }, [])

  useEffect(() => {
    let source: EventSource | null = null
    let retryTimer: number | undefined
    let attempt = 0
    let hadError = false
    let disposed = false
    const clearTimers = new Set<number>()

    const emit = <T extends LiveEventType>(type: T, payload: LiveEventMap[T]) => {
      listeners.current.get(type)?.forEach((fn) => fn(payload))
    }

    const invalidateAll = () => {
      void queryClient.invalidateQueries({ predicate: (q) => q.queryKey[0] !== queryKeys.me[0] })
    }

    const handlers: { [K in LiveEventType]: (payload: LiveEventMap[K]) => void } = {
      'scores.updated': (payload) => {
        void queryClient.invalidateQueries({ queryKey: queryKeys.customers })
        void queryClient.invalidateQueries({ queryKey: queryKeys.summary })
        void queryClient.invalidateQueries({ queryKey: queryKeys.customerAll })
        setLastRecalc({
          modelVersion: payload.model_version,
          scoredAt: payload.scored_at,
          receivedAt: Date.now(),
          durationMs: payload.duration_ms ?? null,
        })
        const ids = payload.changed_customer_ids
        if (ids.length > 0 && ids.length <= 500) {
          const stamp = Date.now()
          setChanged((prev) => {
            const next = new Map(prev)
            for (const id of ids) next.set(id, stamp)
            return next
          })
          const timer = window.setTimeout(() => {
            clearTimers.delete(timer)
            setChanged((prev) => {
              const next = new Map(prev)
              for (const [id, at] of prev) if (at <= stamp) next.delete(id)
              return next.size === prev.size ? prev : next
            })
          }, HIGHLIGHT_MS)
          clearTimers.add(timer)
        }
      },
      'settings.updated': () => {
        void queryClient.invalidateQueries({ queryKey: queryKeys.settings })
        void queryClient.invalidateQueries({ queryKey: queryKeys.history })
      },
      'source.updated': () => invalidateAll(),
    }

    const connect = () => {
      if (disposed) return
      source = new EventSource(STREAM_URL, { withCredentials: true })

      source.onopen = () => {
        attempt = 0
        setStatus('online')
        if (hadError) {
          hadError = false
          invalidateAll()
        }
      }

      source.onerror = () => {
        hadError = true
        setStatus('reconnecting')
        if (source && source.readyState === EventSource.CLOSED) {
          source.close()
          source = null
          // Поток закрыт окончательно (например, 401) — проверим сессию: при 401 клиент уведёт на /login.
          void api.me().catch(() => undefined)
          const delay = BACKOFF_MS[Math.min(attempt, BACKOFF_MS.length - 1)] ?? 15000
          attempt += 1
          retryTimer = window.setTimeout(connect, delay)
        }
      }

      for (const type of LIVE_EVENT_TYPES) {
        source.addEventListener(type, (event) => {
          const payload = parseLiveEvent(type, (event as MessageEvent<string>).data)
          if (!payload) return
          ;(handlers[type] as (p: LiveEventMap[typeof type]) => void)(payload)
          emit(type, payload)
        })
      }
    }

    connect()

    return () => {
      disposed = true
      window.clearTimeout(retryTimer)
      clearTimers.forEach((t) => window.clearTimeout(t))
      source?.close()
    }
  }, [queryClient])

  const value = useMemo<LiveContextValue>(
    () => ({ status, lastRecalc, changed, subscribe }),
    [status, lastRecalc, changed, subscribe],
  )

  return <LiveContext.Provider value={value}>{children}</LiveContext.Provider>
}

export function useLive() {
  const ctx = useContext(LiveContext)
  if (!ctx) throw new Error('useLive must be used inside LiveProvider')
  return ctx
}

export function useLiveEvent<T extends LiveEventType>(type: T, listener: Listener<T>) {
  const { subscribe } = useLive()
  const ref = useRef(listener)
  useEffect(() => {
    ref.current = listener
  })
  useEffect(() => subscribe(type, (payload) => ref.current(payload)), [subscribe, type])
}

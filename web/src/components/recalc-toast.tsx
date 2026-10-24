import { useQueryClient } from '@tanstack/react-query'
import { RefreshCw, X } from 'lucide-react'
import { useEffect, useRef, useState } from 'react'
import { queryKeys } from '@/api/endpoints'
import type { ScoresUpdatedEvent, Summary } from '@/api/types'
import { describeRecalc, type RecalcNotice, type RecalcSummary } from '@/lib/recalc'
import { useLiveEvent } from '@/providers/live'

const VISIBLE_MS = 5000

interface Burst {
  before: RecalcSummary | null
  ids: Set<number>
  full: boolean
}

/**
 * Короткое уведомление после пересчёта оценок: какая версия модели, сколько клиентов изменилось
 * и как сдвинулись главные цифры. Если события идут чаще, чем уведомление успевает исчезнуть
 * (например, работает симулятор), они складываются в одно.
 */
export function RecalcToast() {
  const queryClient = useQueryClient()
  const [notice, setNotice] = useState<(RecalcNotice & { key: number }) | null>(null)
  const burst = useRef<Burst | null>(null)
  const lastSummary = useRef<RecalcSummary | null>(null)
  const timer = useRef<number | undefined>(undefined)

  useEffect(() => () => window.clearTimeout(timer.current), [])

  const hide = () => {
    window.clearTimeout(timer.current)
    burst.current = null
    setNotice(null)
  }

  useLiveEvent('scores.updated', (event: ScoresUpdatedEvent) => {
    const cached = queryClient.getQueryData<Summary>(queryKeys.summary)
    const before: RecalcSummary | null = lastSummary.current ?? (cached ? { by_level: cached.by_level, expected_loss: cached.expected_loss } : null)
    if (event.summary.customers_total > 0) lastSummary.current = event.summary

    const current = burst.current ?? { before, ids: new Set<number>(), full: false }
    for (const id of event.changed_customer_ids) current.ids.add(id)
    current.full ||= Boolean(event.full) && event.changed_customer_ids.length === 0

    const next = describeRecalc(
      {
        model_version: event.model_version,
        changed_customer_ids: current.full ? [] : [...current.ids],
        full: current.full,
        summary: event.summary,
      },
      current.before,
    )
    if (!next) return
    burst.current = current
    // ключ меняется только у нового уведомления: обновление текста не перезапускает анимацию появления
    setNotice((prev) => ({ ...next, key: prev?.key ?? Date.now() }))
    window.clearTimeout(timer.current)
    timer.current = window.setTimeout(hide, VISIBLE_MS)
  })

  return (
    <div className="pointer-events-none fixed inset-x-4 bottom-4 z-40 flex justify-end sm:inset-x-auto sm:right-6 sm:bottom-6" role="status" aria-live="polite">
      {notice && (
        <div
          key={notice.key}
          data-testid="recalc-toast"
          className="pointer-events-auto flex w-full max-w-sm items-start gap-3 rounded-xl border border-border bg-popover px-4 py-3 text-popover-foreground shadow-pop animate-pop-in"
        >
          <span className="mt-0.5 flex size-6 shrink-0 items-center justify-center rounded-full bg-risk-low-bg text-risk-low">
            <RefreshCw className="size-3.5" aria-hidden />
          </span>
          <div className="min-w-0 flex-1">
            <p className="text-[13px] leading-5 font-medium">{notice.title}</p>
            {notice.detail && <p className="mt-0.5 text-xs leading-5 text-muted-foreground num">{notice.detail}</p>}
          </div>
          <button
            type="button"
            onClick={hide}
            className="-mt-0.5 -mr-1.5 inline-flex size-6 shrink-0 items-center justify-center rounded text-muted-foreground outline-none hover:bg-accent hover:text-foreground focus-visible:ring-2 focus-visible:ring-ring"
            aria-label="Скрыть уведомление"
          >
            <X className="size-3.5" />
          </button>
        </div>
      )}
    </div>
  )
}

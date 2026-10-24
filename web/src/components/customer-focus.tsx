import { AlarmClock, PiggyBank } from 'lucide-react'
import { Link } from 'react-router'
import type { Recommendation } from '@/api/types'
import { savingText, urgentText } from '@/lib/contact-today'
import { FOCUS_LABEL } from '@/lib/reasons'
import { cn } from '@/lib/utils'

/** Бейдж «Срочно»: срок по риску не дальше двух дней, при любом уровне риска. */
export function UrgentBadge({ item, className }: { item: { urgent?: boolean; deadline_days?: number | null }; className?: string }) {
  const text = urgentText(item)
  if (!text) return null
  return (
    <span
      className={cn('inline-flex items-center gap-1 rounded-md bg-risk-high-bg px-1.5 py-0.5 text-xs font-medium whitespace-nowrap text-risk-high', className)}
      title={text}
      data-testid="urgent-badge"
    >
      <AlarmClock className="size-3.5" aria-hidden />
      Срочно
    </span>
  )
}

/**
 * Главная причина риска и самое выгодное действие — одинаково в очереди на обзоре и в таблице
 * клиентов. Это разные вещи: причина — с наибольшим вкладом в score, действие — с наибольшим
 * денежным эффектом, поэтому они подписаны по-разному и могут относиться к разным причинам.
 */
export function CustomerFocus({
  reason,
  reasonTitle,
  reasonCode,
  reasonLink,
  action,
  compact = false,
  muted = false,
}: {
  reason: string | null | undefined
  reasonTitle?: string
  reasonCode?: string
  /** Ссылка «все клиенты с этой главной причиной» (таблица клиентов). */
  reasonLink?: string
  action: Recommendation | null | undefined
  compact?: boolean
  muted?: boolean
}) {
  const saving = savingText(action)
  return (
    <div className="min-w-0 text-[13px] leading-5" data-testid="customer-focus">
      <p className={cn(compact && 'truncate')} title={reasonTitle ?? reason ?? undefined}>
        <span className="text-subtle-foreground">{FOCUS_LABEL.reason}: </span>
        {reasonLink && reason ? (
          <Link
            to={reasonLink}
            data-reason={reasonCode}
            aria-label={`${reason}: показать всех клиентов с этой главной причиной`}
            className={cn(
              'rounded-sm underline-offset-2 outline-none hover:underline focus-visible:ring-2 focus-visible:ring-ring',
              muted ? 'text-muted-foreground' : 'font-medium text-foreground',
            )}
          >
            {reason}
          </Link>
        ) : (
          <span className={cn(muted ? 'text-muted-foreground' : 'font-medium text-foreground')}>{reason ?? '—'}</span>
        )}
      </p>
      {action && (
        <p className={cn('text-muted-foreground', compact ? 'truncate text-xs' : 'line-clamp-2')} title={action.detail ?? action.title}>
          <span className="text-subtle-foreground">{FOCUS_LABEL.action}: </span>
          {action.title}
          {compact && saving && <span className="num"> · {saving.toLowerCase()}</span>}
        </p>
      )}
      {saving && !compact && (
        <p className="mt-1.5 inline-flex items-center gap-1.5 rounded-md bg-risk-low-bg px-2 py-0.5 text-xs font-medium text-risk-low num">
          <PiggyBank className="size-3.5" aria-hidden />
          {saving}
        </p>
      )}
    </div>
  )
}

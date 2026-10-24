import { keepPreviousData, useQuery } from '@tanstack/react-query'
import { AlertCircle, ArrowRight, Check, FlaskConical, Info, Loader2, Undo2 } from 'lucide-react'
import type { ReactNode } from 'react'
import { errorMessage, isApiError } from '@/api/client'
import { api, queryKeys } from '@/api/endpoints'
import type { ModelSettingsPatch, ModelSettingsPreview, PreviewCustomerChange, RiskLevel } from '@/api/types'
import { RiskBadge } from '@/components/risk'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Skeleton } from '@/components/ui/skeleton'
import { useDebouncedValue } from '@/hooks/use-debounced'
import { formatInt, formatMoney, formatScore, formatShare, NBSP } from '@/lib/format'
import { RISK_LABEL } from '@/lib/labels'
import { isEmptyPatch } from '@/lib/settings'
import { cn } from '@/lib/utils'
import { approximateNote, countDelta, moneyDelta, movesSummary, moveKind, scoreDelta, shareDelta } from '@/lib/what-if'

export const PREVIEW_DEBOUNCE_MS = 300

/**
 * Превью несохранённых настроек. Запрос уходит через 300 мс после последнего движения ползунка;
 * устаревший запрос отменяется: у него меняется ключ, и TanStack Query обрывает fetch через signal.
 */
export function useSettingsPreview(patch: ModelSettingsPatch | null) {
  const debounced = useDebouncedValue(patch, PREVIEW_DEBOUNCE_MS)
  // режим выключили — хвост дебаунса уже не нужен
  const ready = patch !== null && debounced !== null && !isEmptyPatch(debounced)
  const query = useQuery({
    queryKey: queryKeys.settingsPreview(debounced ?? {}),
    queryFn: ({ signal }) => api.previewSettings(debounced ?? {}, signal),
    enabled: ready,
    placeholderData: keepPreviousData,
    staleTime: 15_000,
    gcTime: 30_000,
    retry: false,
  })
  // пока ползунок двигается, показываем прошлый ответ и признак «считаем»
  const settling = patch !== debounced || query.isFetching
  return { ...query, ready, settling }
}

const LEVELS: RiskLevel[] = ['high', 'medium', 'low']

function Delta({ text, good }: { text: string | null; good: boolean }) {
  if (!text) return null
  return <span className={cn('text-xs font-medium num', good ? 'text-risk-low' : 'text-risk-high')}>{text}</span>
}

function Shift({ label, testId, children }: { label: string; testId: string; children: ReactNode }) {
  return (
    <div className="min-w-0 rounded-lg border border-border bg-card px-3 py-2" data-testid={testId}>
      <p className="truncate text-xs text-muted-foreground">{label}</p>
      {children}
    </div>
  )
}

function Totals({ p }: { p: ModelSettingsPreview }) {
  const { before, after } = p
  const lossDelta = moneyDelta(before.expected_loss, after.expected_loss)
  const horizonNote = before.horizon_days !== after.horizon_days ? ` (было за ${before.horizon_days}${NBSP}дн.)` : ''
  return (
    <div className="grid gap-2 sm:grid-cols-3" role="group" aria-label="Что изменится по всем клиентам">
      <Shift label={`Ожидаемая потеря за ${after.horizon_days}${NBSP}дн.`} testId="what-if-expected-loss">
        <p className="mt-0.5 flex flex-wrap items-baseline gap-x-2">
          <span className="text-lg leading-6 font-semibold tracking-[-0.01em] num" data-role="after">
            {formatMoney(after.expected_loss, { cents: false })}
          </span>
          <Delta text={lossDelta} good={after.expected_loss < before.expected_loss} />
        </p>
        <p className="text-xs text-muted-foreground num" data-role="before">
          было {formatMoney(before.expected_loss, { cents: false })}
          {horizonNote}
        </p>
      </Shift>
      <Shift label="Доля выручки под риском" testId="what-if-share">
        <p className="mt-0.5 flex flex-wrap items-baseline gap-x-2">
          <span className="text-lg leading-6 font-semibold tracking-[-0.01em] num">{formatShare(after.revenue_at_risk_share)}</span>
          <Delta text={shareDelta(before.revenue_at_risk_share, after.revenue_at_risk_share)} good={after.revenue_at_risk_share < before.revenue_at_risk_share} />
        </p>
        <p className="text-xs text-muted-foreground num">было {formatShare(before.revenue_at_risk_share)}</p>
      </Shift>
      <Shift label="Клиенты по уровням" testId="what-if-levels">
        <ul className="mt-1 space-y-0.5 text-[13px] num">
          {LEVELS.map((level) => (
            <li key={level} className="flex items-baseline gap-1.5" data-level={level}>
              <span className="w-[4.5rem] text-muted-foreground">{RISK_LABEL[level]}</span>
              <span>{formatInt(before.by_level[level])}</span>
              <ArrowRight className="size-3 self-center text-subtle-foreground" aria-label="станет" />
              <span className="font-medium">{formatInt(after.by_level[level])}</span>
              <Delta text={countDelta(before.by_level[level], after.by_level[level])} good={level === 'high' ? after.by_level[level] < before.by_level[level] : true} />
            </li>
          ))}
        </ul>
      </Shift>
    </div>
  )
}

export function WhatIfBar({
  preview,
  onApply,
  onCancel,
  applying,
}: {
  preview: ReturnType<typeof useSettingsPreview>
  onApply: () => void
  onCancel: () => void
  applying: boolean
}) {
  const p = preview.data
  const superseded = isApiError(preview.error) && preview.error.code === 'preview_superseded'
  const failed = preview.isError && !superseded && !preview.settling

  let status: ReactNode
  if (!preview.ready && !preview.settling) {
    status = 'Двигайте ползунки: здесь будет видно, что изменится. Пока ничего не сохраняется.'
  } else if (preview.settling || superseded) {
    status = (
      <>
        <Loader2 className="size-4 animate-spin" /> Считаем, что изменится…
      </>
    )
  } else if (failed) {
    status = (
      <>
        <AlertCircle className="size-4 shrink-0 text-risk-high" />
        <span className="min-w-0">Не удалось посчитать: {errorMessage(preview.error)}</span>
        <Button variant="link" size="sm" className="h-auto px-1" onClick={() => void preview.refetch()}>
          Повторить
        </Button>
      </>
    )
  } else if (p) {
    status = movesSummary(p)
  }
  const note = p ? approximateNote(p) : null

  return (
    <section aria-label="Режим «Что если»" data-testid="what-if">
      {p ? (
        <div className={cn('transition-opacity', preview.settling && 'opacity-60')}>
          <Totals p={p} />
        </div>
      ) : preview.settling ? (
        <div className="grid gap-2 sm:grid-cols-3">
          {Array.from({ length: 3 }, (_, i) => (
            <Skeleton key={i} className="h-[74px] rounded-lg" />
          ))}
        </div>
      ) : (
        <div className="flex items-center gap-2 rounded-lg border border-dashed border-border px-3 py-4 text-[13px] text-muted-foreground">
          <FlaskConical className="size-4" /> Режим «Что если»: изменения не сохраняются, пока вы не нажмёте «Применить».
        </div>
      )}
      {note && (
        <p className="mt-2 flex items-start gap-1.5 text-xs text-muted-foreground" data-testid="what-if-approximate">
          <Info className="mt-px size-3.5 shrink-0" />
          {note}
        </p>
      )}
      <div className="mt-2 flex flex-wrap items-center justify-between gap-x-4 gap-y-2">
        <p role="status" aria-live="polite" className="flex min-w-0 items-center gap-2 text-[13px] text-muted-foreground">
          {status}
        </p>
        <div className="flex gap-2">
          <Button variant="outline" size="sm" onClick={onCancel} disabled={applying}>
            <Undo2 />
            Отменить
          </Button>
          <Button size="sm" onClick={onApply} disabled={applying || !preview.ready}>
            {applying ? <Loader2 className="animate-spin" /> : <Check />}
            Применить
          </Button>
        </div>
      </div>
    </section>
  )
}

function ChangeRow({ c }: { c: PreviewCustomerChange }) {
  const kind = moveKind(c)
  const loss = moneyDelta(c.expected_loss_before, c.expected_loss_after)
  const score = scoreDelta(c.score_before ?? null, c.score_after ?? null)
  return (
    <li className="py-2.5" data-testid="what-if-change" data-kind={kind}>
      <div className="flex items-baseline justify-between gap-2">
        <p className="min-w-0 truncate text-sm font-medium" title={c.email}>
          {c.email}
        </p>
        <span className="shrink-0 text-xs text-subtle-foreground num">#{c.id}</span>
      </div>
      <div className="mt-1 flex flex-wrap items-center gap-1.5">
        <RiskBadge level={c.level_before} />
        <ArrowRight className="size-3.5 text-subtle-foreground" aria-label="станет" />
        <RiskBadge level={c.level_after} />
      </div>
      <p className="mt-1 text-xs text-muted-foreground num">
        score {formatScore(c.score_before)} → {formatScore(c.score_after)}
        {score && <span className={cn('ml-1 font-medium', score.startsWith('+') ? 'text-risk-high' : 'text-risk-low')}>({score})</span>}
        {' · '}потеря {formatMoney(c.expected_loss_before, { cents: false })} → {formatMoney(c.expected_loss_after, { cents: false })}
        {loss && (
          <span className={cn('ml-1 font-medium', c.expected_loss_after > c.expected_loss_before ? 'text-risk-high' : 'text-risk-low')}>({loss})</span>
        )}
      </p>
    </li>
  )
}

export function WhatIfChanges({ preview }: { preview: ModelSettingsPreview | undefined }) {
  const rest = preview ? preview.changes_total - preview.changes.length : 0
  return (
    <Card data-testid="what-if-changes">
      <CardHeader>
        <CardTitle>Кто сменит уровень</CardTitle>
        <CardDescription>Сначала те, кто попадёт в высокий риск, потом те, кто из него выйдет.</CardDescription>
      </CardHeader>
      <CardContent className="pt-1">
        {!preview ? (
          <p className="py-2 text-[13px] text-muted-foreground">Появится, как только вы что-нибудь поменяете.</p>
        ) : preview.changes.length === 0 ? (
          <p className="py-2 text-[13px] text-muted-foreground">Уровень риска ни у кого не поменяется: изменятся только score и потери.</p>
        ) : (
          <>
            <ul className="max-h-[420px] divide-y divide-border overflow-y-auto pr-1">
              {preview.changes.map((c) => (
                <ChangeRow key={c.id} c={c} />
              ))}
            </ul>
            {rest > 0 && <p className="pt-2 text-xs text-muted-foreground">и ещё {formatInt(rest)}, показаны первые {preview.changes.length}</p>}
          </>
        )}
      </CardContent>
    </Card>
  )
}

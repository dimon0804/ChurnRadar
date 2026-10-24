import { useMutation, useQuery } from '@tanstack/react-query'
import { ArrowRight, GitCompare, History, Loader2, Undo2, X } from 'lucide-react'
import { useState } from 'react'
import { errorMessage } from '@/api/client'
import { api, queryKeys } from '@/api/endpoints'
import type { ModelSettings, RiskLevel, SettingsCompare } from '@/api/types'
import { EmptyState, ErrorState } from '@/components/states'
import { ConfirmDialog } from '@/components/ui/alert-dialog'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Skeleton } from '@/components/ui/skeleton'
import { formatDateTime, formatInt, formatMoney } from '@/lib/format'
import { RISK_LABEL } from '@/lib/labels'
import { comparePair, describeChange, toggleCompare } from '@/lib/settings-changes'
import { cn } from '@/lib/utils'
import { countDelta, moneyDelta, movesSummary } from '@/lib/what-if'

const LEVELS: RiskLevel[] = ['high', 'medium', 'low']

function CompareResult({
  data,
  titles,
  groupTitles,
}: {
  data: SettingsCompare
  titles: ReadonlyMap<string, string>
  groupTitles: ReadonlyMap<string, string>
}) {
  const p = data.impact
  return (
    <div className="space-y-3" data-testid="compare-result">
      <div>
        <p className="text-xs font-medium text-muted-foreground">
          Что поменялось с v{data.from.version} до v{data.to.version}
        </p>
        {data.changes.length === 0 ? (
          <p className="mt-1 text-[13px] text-muted-foreground">Настройки в этих версиях одинаковые.</p>
        ) : (
          <ul className="mt-1 space-y-0.5" data-role="changes">
            {data.changes.map((c, i) => (
              <li key={i} className="text-[13px] leading-5 break-words">
                {describeChange(c, titles, groupTitles)}
              </li>
            ))}
          </ul>
        )}
      </div>
      {p ? (
        <div className="space-y-2 rounded-lg border border-border px-3 py-2.5" data-role="impact">
          <p className="text-xs text-muted-foreground">На сегодняшних данных</p>
          <p className="text-[13px] num">
            Ожидаемая потеря: {formatMoney(p.before.expected_loss, { cents: false })}
            <ArrowRight className="mx-1 inline size-3 text-subtle-foreground" aria-label="стало" />
            <span className="font-medium">{formatMoney(p.after.expected_loss, { cents: false })}</span>
            {moneyDelta(p.before.expected_loss, p.after.expected_loss) && (
              <span className="ml-1.5 text-xs text-muted-foreground">{moneyDelta(p.before.expected_loss, p.after.expected_loss)}</span>
            )}
          </p>
          <ul className="space-y-0.5 text-[13px] num">
            {LEVELS.map((level) => (
              <li key={level} className="flex items-baseline gap-1.5" data-level={level}>
                <span className="w-[4.5rem] text-muted-foreground">{RISK_LABEL[level]}</span>
                <span>{formatInt(p.before.by_level[level])}</span>
                <ArrowRight className="size-3 self-center text-subtle-foreground" aria-label="стало" />
                <span className="font-medium">{formatInt(p.after.by_level[level])}</span>
                <span className="text-xs text-muted-foreground">{countDelta(p.before.by_level[level], p.after.by_level[level])}</span>
              </li>
            ))}
          </ul>
          <p className="text-[13px]" data-role="moves">
            {movesSummary(p)}
          </p>
        </div>
      ) : (
        <p className="rounded-lg border border-dashed border-border px-3 py-2.5 text-[13px] text-muted-foreground" data-role="no-impact">
          Сервис оценки сейчас недоступен, поэтому показаны только различия в настройках, без эффекта на клиентов.
        </p>
      )}
    </div>
  )
}

/** Журнал версий: что менялось, сравнение двух версий и откат (откат — только администратору). */
export function SettingsHistoryCard({
  titles,
  groupTitles,
  currentVersion,
  isAdmin,
  disabled,
  onRolledBack,
}: {
  titles: ReadonlyMap<string, string>
  groupTitles: ReadonlyMap<string, string>
  currentVersion: number
  isAdmin: boolean
  disabled?: boolean
  onRolledBack: (data: ModelSettings) => void
}) {
  const history = useQuery({ queryKey: queryKeys.history, queryFn: ({ signal }) => api.settingsHistory(30, signal) })
  const items = history.data?.items ?? []
  const [selected, setSelected] = useState<number[]>([])
  const pair = comparePair(selected)
  const compare = useQuery({
    queryKey: queryKeys.settingsCompare(pair?.from ?? 0, pair?.to ?? 0),
    queryFn: ({ signal }) => api.compareSettings(pair!.from, pair!.to, signal),
    enabled: pair !== null,
    retry: false,
  })
  const rollback = useMutation({
    mutationFn: api.rollbackSettings,
    onSuccess: (data) => {
      setSelected([])
      onRolledBack(data)
    },
  })

  return (
    <Card data-testid="settings-history">
      <CardHeader>
        <CardTitle>История изменений</CardTitle>
        <CardDescription>Кто и что менял в модели. Отметьте две версии, чтобы сравнить их</CardDescription>
      </CardHeader>
      <CardContent className="pt-2">
        {history.isPending ? (
          <div className="space-y-3 pt-2">
            {Array.from({ length: 4 }, (_, i) => (
              <Skeleton key={i} className="h-10" />
            ))}
          </div>
        ) : history.isError ? (
          <ErrorState error={history.error} onRetry={() => void history.refetch()} className="py-6" />
        ) : items.length === 0 ? (
          <EmptyState icon={<History />} title="Изменений пока не было" className="py-6" />
        ) : (
          <>
            {rollback.isError && (
              <p className="mb-2 rounded-md bg-risk-high-bg px-3 py-2 text-[13px] text-risk-high" role="alert">
                Не удалось откатить: {errorMessage(rollback.error)}
              </p>
            )}
            <ol className="max-h-[420px] overflow-y-auto pr-1 pl-1">
              {items.map((h) => {
                const checked = selected.includes(h.version)
                return (
                  <li
                    key={h.version}
                    data-version={h.version}
                    className="relative border-l border-border py-2 pl-4 before:absolute before:top-3.5 before:-left-[3.5px] before:size-1.5 before:rounded-full before:bg-subtle-foreground"
                  >
                    <div className="flex items-start justify-between gap-2">
                      <p className="flex min-w-0 flex-wrap items-baseline gap-x-2 text-xs text-muted-foreground num">
                        <span className="font-medium text-foreground">v{h.version}</span>
                        {h.version === currentVersion && <span className="text-risk-low">действует</span>}
                        <span>{formatDateTime(h.changed_at)}</span>
                        <span className="truncate">· {h.changed_by ?? 'система'}</span>
                      </p>
                      <label className="flex shrink-0 cursor-pointer items-center gap-1.5 text-xs text-muted-foreground">
                        <input
                          type="checkbox"
                          className="size-3.5 accent-primary"
                          checked={checked}
                          onChange={() => setSelected((s) => toggleCompare(s, h.version))}
                          aria-label={`Сравнить версию ${h.version}`}
                        />
                        Сравнить
                      </label>
                    </div>
                    <ul className="mt-1 space-y-0.5">
                      {(h.changes ?? []).slice(0, 6).map((c, i) => (
                        <li key={i} className="text-[13px] leading-5 break-words">
                          {describeChange(c, titles, groupTitles)}
                        </li>
                      ))}
                      {(h.changes?.length ?? 0) > 6 && <li className="text-xs text-muted-foreground">и ещё {(h.changes?.length ?? 0) - 6}</li>}
                      {(h.changes?.length ?? 0) === 0 && <li className="text-[13px] text-muted-foreground">Без изменений значений</li>}
                    </ul>
                    {isAdmin && h.version !== currentVersion && (
                      <ConfirmDialog
                        title={`Откатить модель к версии ${h.version}?`}
                        description="Все коэффициенты, пороги, горизонт и группы станут такими, как в этой версии. Журнал не стирается: откат запишется новой версией, и его тоже можно будет отменить."
                        confirmLabel="Откатить"
                        onConfirm={() => rollback.mutate(h.version)}
                        trigger={
                          <Button
                            variant="link"
                            size="sm"
                            className="mt-1 h-auto px-0 text-xs"
                            disabled={disabled || rollback.isPending}
                            data-testid={`history-rollback-${h.version}`}
                          >
                            {rollback.isPending && rollback.variables === h.version ? <Loader2 className="animate-spin" /> : <Undo2 />}
                            Откатить к этой версии
                          </Button>
                        }
                      />
                    )}
                  </li>
                )
              })}
            </ol>
            <div className={cn('mt-3 border-t border-border pt-3', !pair && selected.length === 0 && 'hidden')} data-testid="history-compare">
              <div className="mb-2 flex items-center justify-between gap-2">
                <p className="flex items-center gap-1.5 text-[13px] font-medium">
                  <GitCompare className="size-3.5" aria-hidden />
                  {pair ? `Сравнение v${pair.from} и v${pair.to}` : 'Отметьте ещё одну версию'}
                </p>
                <Button variant="ghost" size="sm" className="h-7 px-2" onClick={() => setSelected([])} aria-label="Сбросить сравнение">
                  <X />
                </Button>
              </div>
              {pair &&
                (compare.isPending ? (
                  <Skeleton className="h-24" />
                ) : compare.isError ? (
                  <ErrorState error={compare.error} onRetry={() => void compare.refetch()} className="py-4" />
                ) : compare.data ? (
                  <CompareResult data={compare.data} titles={titles} groupTitles={groupTitles} />
                ) : null)}
            </div>
          </>
        )}
      </CardContent>
    </Card>
  )
}

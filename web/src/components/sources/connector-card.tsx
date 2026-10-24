import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  AlertTriangle,
  BarChart3,
  ChevronDown,
  Database,
  History,
  KeyRound,
  Loader2,
  Play,
  RotateCcw,
} from 'lucide-react'
import { useEffect, useId, useRef, useState } from 'react'
import { errorMessage } from '@/api/client'
import {
  connectorRunsQueryKey,
  connectorsApi,
  connectorsQueryKey,
  metricaSummaryQueryKey,
  type Connector,
  type ConnectorList,
  type ConnectorPatch,
  type ConnectorRun,
} from '@/api/connectors'
import { ConfirmDialog } from '@/components/ui/alert-dialog'
import { Button } from '@/components/ui/button'
import { Card } from '@/components/ui/card'
import { Select } from '@/components/ui/select'
import { Skeleton } from '@/components/ui/skeleton'
import { Switch } from '@/components/ui/switch'
import { useNow } from '@/hooks/use-now'
import { DASH, formatDateTime, formatDuration, formatInt } from '@/lib/format'
import {
  connectorKindLabel,
  formatRelative,
  intervalLabel,
  intervalOptions,
  isRunActive,
  runDetailsText,
  runStatusLabel,
  statusView,
  tokenText,
  triggerLabel,
  type StatusTone,
} from '@/lib/sources'
import { cn } from '@/lib/utils'
import { MetricaPanel } from './metrica-panel'

const TONE_CLASS: Record<StatusTone, string> = {
  ok: 'bg-risk-low-bg text-risk-low',
  error: 'bg-risk-high-bg text-risk-high',
  running: 'bg-chart-forecast/10 text-chart-forecast',
  idle: 'bg-risk-unknown-bg text-risk-unknown',
}

function StatusBadge({ tone, label, testId }: { tone: StatusTone; label: string; testId?: string }) {
  return (
    <span
      data-testid={testId}
      data-tone={tone}
      className={cn('inline-flex shrink-0 items-center gap-1 rounded-full px-2 py-0.5 text-xs font-medium', TONE_CLASS[tone])}
    >
      {tone === 'running' && <Loader2 className="size-3 animate-spin" aria-hidden />}
      {label}
    </span>
  )
}

function Fact({ label, value, title }: { label: string; value: string; title?: string }) {
  return (
    <div className="min-w-0">
      <dt className="text-xs text-muted-foreground">{label}</dt>
      <dd className="mt-0.5 truncate text-sm font-medium num" title={title}>
        {value}
      </dd>
    </div>
  )
}

function RunRow({ run }: { run: ConnectorRun }) {
  const tone: StatusTone = run.status === 'running' ? 'running' : run.status
  const details = runDetailsText(run.details as Record<string, unknown> | undefined)
  return (
    <li className="rounded-lg border border-border px-3 py-2.5" data-testid="connector-run" data-status={run.status}>
      <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
        <span className="text-sm font-medium num">{formatDateTime(run.started_at)}</span>
        <span className="text-xs text-muted-foreground">{triggerLabel(run.trigger)}</span>
        <StatusBadge tone={tone} label={runStatusLabel(run.status)} />
        <span className="ml-auto text-xs text-muted-foreground num">
          <span data-testid="connector-run-records">{formatInt(run.records)}</span> зап. · {formatDuration(run.duration_ms)}
        </span>
      </div>
      {details && <p className="mt-1 text-xs text-muted-foreground num">{details}</p>}
      {run.error && <p className="mt-1 text-xs break-words text-risk-high">{run.error}</p>}
    </li>
  )
}

function RunsHistory({ connector, active }: { connector: Connector; active: boolean }) {
  const [open, setOpen] = useState(false)
  const panelId = useId()
  const runs = useQuery({
    queryKey: connectorRunsQueryKey(connector.id),
    queryFn: ({ signal }) => connectorsApi.runs(connector.id, signal),
    enabled: open,
    refetchInterval: open && active ? 2000 : false,
  })

  return (
    <div className="border-t border-border">
      <button
        type="button"
        aria-expanded={open}
        aria-controls={panelId}
        onClick={() => setOpen((v) => !v)}
        className="flex w-full items-center gap-2 px-4 py-3 text-left text-[13px] font-medium outline-none hover:bg-accent/40 focus-visible:ring-2 focus-visible:ring-ring sm:px-5"
      >
        <History className="size-4 text-muted-foreground" aria-hidden />
        История запусков
        <ChevronDown className={cn('ml-auto size-4 text-muted-foreground transition-transform', open && 'rotate-180')} aria-hidden />
      </button>
      {open && (
        <div id={panelId} className="px-4 pb-4 sm:px-5">
          {runs.isPending ? (
            <Skeleton className="h-16 rounded-lg" />
          ) : runs.isError ? (
            <p className="text-[13px] text-risk-high">{errorMessage(runs.error)}</p>
          ) : runs.data.items.length === 0 ? (
            <p className="text-[13px] text-muted-foreground">Запусков пока не было.</p>
          ) : (
            <ul className="space-y-2" aria-label={`История запусков: ${connector.title}`}>
              {runs.data.items.map((r) => (
                <RunRow key={r.id} run={r} />
              ))}
            </ul>
          )}
        </div>
      )}
    </div>
  )
}

export function ConnectorCard({ connector: c, schedulerEnabled }: { connector: Connector; schedulerEnabled: boolean }) {
  const queryClient = useQueryClient()
  const now = useNow(5000)
  const active = isRunActive(c.status)
  const view = statusView(c.status)
  const token = tokenText(c.token_env, c.token_present)
  const kindLabel = connectorKindLabel(c.kind)

  const store = (next: Connector) =>
    queryClient.setQueryData<ConnectorList>(connectorsQueryKey, (list) =>
      list ? { ...list, items: list.items.map((x) => (x.id === next.id ? next : x)) } : list,
    )

  // запуск закончился: подтягиваем историю и сводку Метрики
  const wasActive = useRef(active)
  useEffect(() => {
    if (wasActive.current && !active) {
      void queryClient.invalidateQueries({ queryKey: connectorRunsQueryKey(c.id) })
      if (c.kind === 'yandex_metrica') void queryClient.invalidateQueries({ queryKey: metricaSummaryQueryKey })
    }
    wasActive.current = active
  }, [active, c.id, c.kind, queryClient])

  const patch = useMutation({
    mutationFn: (body: ConnectorPatch) => connectorsApi.patch(c.id, body),
    onMutate: (body) => {
      const prev = queryClient.getQueryData<ConnectorList>(connectorsQueryKey)
      store({ ...c, ...(body.enabled !== undefined ? { enabled: body.enabled } : {}), ...(body.interval_s ? { interval_s: body.interval_s } : {}) })
      return { prev }
    },
    onError: (_e, _b, ctx) => ctx?.prev && queryClient.setQueryData(connectorsQueryKey, ctx.prev),
    onSuccess: store,
  })
  const run = useMutation({
    mutationFn: () => connectorsApi.run(c.id),
    onSuccess: (next) => {
      store(next)
      void queryClient.invalidateQueries({ queryKey: connectorRunsQueryKey(c.id) })
    },
  })
  const reset = useMutation({ mutationFn: () => connectorsApi.resetCursor(c.id), onSuccess: store })
  const error = patch.error ?? run.error ?? reset.error

  const s = c.status
  const nextRun = !c.enabled
    ? 'по расписанию выключен'
    : !schedulerEnabled
      ? 'планировщик выключен'
      : s.next_run_at
        ? formatRelative(s.next_run_at, now)
        : DASH

  return (
    <Card data-testid={`connector-${c.id}`} data-status={s.last_status ?? 'never'} className="overflow-hidden">
      <div className="space-y-4 p-4 sm:p-5">
        <div className="flex items-start gap-3">
          <div className="flex size-9 shrink-0 items-center justify-center rounded-lg bg-muted text-muted-foreground">
            {c.kind === 'yandex_metrica' ? <BarChart3 className="size-4" /> : <Database className="size-4" />}
          </div>
          <div className="min-w-0 flex-1">
            <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
              <h3 className="truncate text-sm font-semibold">{c.title}</h3>
              <StatusBadge tone={view.tone} label={view.label} testId="connector-status" />
            </div>
            <p className="mt-0.5 text-xs text-muted-foreground">
              {kindLabel}
              {c.managed && ' · задан в окружении'}
            </p>
          </div>
          <Switch
            checked={c.enabled}
            disabled={patch.isPending}
            onCheckedChange={(enabled) => patch.mutate({ enabled })}
            aria-label={`Запускать по расписанию: ${c.title}`}
          />
        </div>

        <dl className="grid grid-cols-2 gap-x-4 gap-y-3 sm:grid-cols-4">
          <Fact label="Последний запуск" value={formatRelative(s.last_run_at, now)} title={formatDateTime(s.last_run_at)} />
          <Fact label="Длительность" value={formatDuration(s.last_duration_ms)} />
          <div className="min-w-0">
            <dt className="text-xs text-muted-foreground">Записей</dt>
            <dd className="mt-0.5 text-sm font-medium num" data-testid="connector-records">
              {formatInt(s.last_records)}
            </dd>
          </div>
          <Fact label="Следующий" value={nextRun} title={formatDateTime(s.next_run_at)} />
        </dl>

        {s.last_error && s.last_status === 'error' && (
          <div role="alert" className="flex gap-2 rounded-lg bg-risk-high-bg px-3 py-2 text-[13px] text-risk-high">
            <AlertTriangle className="mt-0.5 size-4 shrink-0" aria-hidden />
            <p className="min-w-0 break-words">
              {s.last_error}
              {s.consecutive_failures > 1 && <span className="opacity-80"> · подряд неудач: {s.consecutive_failures}</span>}
            </p>
          </div>
        )}

        <div className="space-y-1.5 text-[13px]">
          <p className="flex min-w-0 items-start gap-2">
            <span className="w-14 shrink-0 text-muted-foreground">Адрес</span>
            <span className="min-w-0 truncate num" title={c.base_url}>
              {c.base_url || DASH}
            </span>
            {c.managed && <span className="ml-auto hidden shrink-0 text-xs text-subtle-foreground sm:inline">задано в окружении</span>}
          </p>
          <p className="flex min-w-0 items-start gap-2">
            <span className="w-14 shrink-0 text-muted-foreground">Токен</span>
            <span className={cn('flex min-w-0 items-start gap-1.5 break-words', !token.ok && 'text-risk-medium')}>
              <KeyRound className="mt-0.5 size-3.5 shrink-0" aria-hidden />
              {token.text}
            </span>
          </p>
        </div>

        <div className="flex flex-wrap items-center gap-2">
          <Select
            ariaLabel={`Расписание: ${c.title}`}
            value={String(c.interval_s)}
            onValueChange={(v) => patch.mutate({ interval_s: Number(v) })}
            options={intervalOptions(c.interval_s).map((sec) => ({ value: String(sec), label: intervalLabel(sec) }))}
            className="w-full sm:w-44"
          />
          <Button
            size="sm"
            onClick={() => run.mutate()}
            disabled={active || run.isPending}
            aria-label={`Запустить сейчас: ${c.title}`}
            className="flex-1 sm:flex-none"
          >
            {active || run.isPending ? <Loader2 className="animate-spin" /> : <Play />}
            {active ? 'Идёт запуск…' : 'Запустить сейчас'}
          </Button>
          <ConfirmDialog
            title="Забрать всё заново?"
            description={
              c.kind === 'yandex_metrica'
                ? 'Следующий запуск выгрузит визиты за всю глубину заново, а не только новые дни. Это займёт больше времени, данные не задвоятся.'
                : 'Следующий запуск заберёт из биллинга все аккаунты, услуги и транзакции, а не только изменения. Данные не задвоятся.'
            }
            confirmLabel="Забрать заново"
            onConfirm={() => reset.mutate()}
            trigger={
              <Button variant="outline" size="sm" disabled={reset.isPending} aria-label={`Забрать всё заново: ${c.title}`} className="flex-1 sm:flex-none">
                <RotateCcw />
                Забрать всё заново
              </Button>
            }
          />
        </div>
        {reset.isSuccess && !active && <p className="text-xs text-muted-foreground">Курсор сброшен: следующий запуск заберёт всё целиком.</p>}
        {error && <p className="text-[13px] text-risk-high">{errorMessage(error)}</p>}

        {c.kind === 'yandex_metrica' && <MetricaPanel connector={c} onSaved={store} />}
      </div>
      <RunsHistory connector={c} active={active} />
    </Card>
  )
}

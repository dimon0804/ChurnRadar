import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { CheckCircle2, FileJson, Loader2, UploadCloud, X, Zap } from 'lucide-react'
import { useEffect, useRef, useState, type DragEvent } from 'react'
import { z } from 'zod'
import { errorMessage } from '@/api/client'
import { api, queryKeys } from '@/api/endpoints'
import type { SnapshotAccepted, SourceSnapshot } from '@/api/types'
import { PageHeader } from '@/components/page-header'
import { ErrorState } from '@/components/states'
import { ConfirmDialog } from '@/components/ui/alert-dialog'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Input } from '@/components/ui/input'
import { Select } from '@/components/ui/select'
import { Skeleton } from '@/components/ui/skeleton'
import { Switch } from '@/components/ui/switch'
import { countLabel, formatDecimal, formatDuration, formatInt } from '@/lib/format'
import { cn } from '@/lib/utils'
import { useLive, useLiveEvent } from '@/providers/live'

const MAX_FILE_BYTES = 50 * 1024 * 1024

const snapshotShape = z.object({
  reference_time: z.string().nullable().optional(),
  accounts: z.array(z.object({ id: z.number(), email: z.string() }).passthrough()),
  services: z.array(z.object({ id: z.number(), ownerId: z.number() }).passthrough()),
  transactions: z.array(z.object({ id: z.number(), serviceId: z.number() }).passthrough()),
})

interface Parsed {
  name: string
  size: number
  body: SourceSnapshot
}

function formatBytes(n: number): string {
  if (n < 1024) return `${n} Б`
  if (n < 1024 * 1024) return `${formatDecimal(n / 1024, 0)} КБ`
  return `${formatDecimal(n / 1024 / 1024, 1)} МБ`
}

function Counts({ accounts, services, transactions }: { accounts: number; services: number; transactions: number }) {
  return (
    <dl className="grid grid-cols-3 gap-2.5">
      {(
        [
          ['Аккаунты', accounts],
          ['Услуги', services],
          ['Транзакции', transactions],
        ] as const
      ).map(([label, value]) => (
        <div key={label} className="rounded-lg border border-border bg-card px-3.5 py-2.5">
          <dt className="text-xs text-muted-foreground">{label}</dt>
          <dd className="mt-0.5 text-lg font-semibold num">{formatInt(value)}</dd>
        </div>
      ))}
    </dl>
  )
}

type Phase = { kind: 'idle' } | { kind: 'recalc'; accepted: SnapshotAccepted } | { kind: 'done'; accepted: SnapshotAccepted; durationMs: number | null }

function SnapshotCard() {
  const inputRef = useRef<HTMLInputElement>(null)
  const [dragging, setDragging] = useState(false)
  const [file, setFile] = useState<Parsed | null>(null)
  const [fileError, setFileError] = useState<string | null>(null)
  const [phase, setPhase] = useState<Phase>({ kind: 'idle' })
  const { lastRecalc } = useLive()
  const lastRecalcRef = useRef(lastRecalc)
  useEffect(() => {
    lastRecalcRef.current = lastRecalc
  }, [lastRecalc])
  const startedAt = useRef(0)

  const upload = useMutation({
    mutationFn: (body: SourceSnapshot) => {
      startedAt.current = Date.now()
      return api.putSnapshot(body)
    },
    onSuccess: (accepted) => {
      // пересчёт мог закончиться раньше, чем пришёл ответ на PUT
      const recalc = lastRecalcRef.current
      if (recalc && recalc.receivedAt >= startedAt.current) {
        setPhase({ kind: 'done', accepted, durationMs: recalc.durationMs })
      } else {
        setPhase({ kind: 'recalc', accepted })
      }
      setFile(null)
    },
  })

  useLiveEvent('scores.updated', (e) => {
    setPhase((p) => (p.kind === 'recalc' ? { kind: 'done', accepted: p.accepted, durationMs: e.duration_ms ?? null } : p))
  })

  const readFile = async (f: File) => {
    setFileError(null)
    setPhase({ kind: 'idle' })
    upload.reset()
    if (!f.name.toLowerCase().endsWith('.json') && f.type !== 'application/json') {
      setFileError('Нужен файл в формате JSON, как data.json из комплекта.')
      return
    }
    if (f.size > MAX_FILE_BYTES) {
      setFileError(`Файл больше ${formatBytes(MAX_FILE_BYTES)}. Разбейте данные или загрузите их потоком.`)
      return
    }
    let json: unknown
    try {
      json = JSON.parse(await f.text())
    } catch {
      setFileError('Файл не читается как JSON. Проверьте, что он не обрезан.')
      return
    }
    const parsed = snapshotShape.safeParse(json)
    if (!parsed.success) {
      setFileError('В файле должны быть массивы accounts, services и transactions в формате data.json.')
      return
    }
    setFile({ name: f.name, size: f.size, body: json as SourceSnapshot })
  }

  const onDrop = (e: DragEvent) => {
    e.preventDefault()
    setDragging(false)
    const f = e.dataTransfer.files?.[0]
    if (f) void readFile(f)
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle>Загрузка снимка данных</CardTitle>
        <CardDescription>
          JSON с аккаунтами, услугами и транзакциями в формате data.json. Текущие данные заменятся целиком, оценки
          пересчитаются сами.
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-4">
        <div
          role="button"
          tabIndex={0}
          aria-label="Выбрать JSON-файл со снимком данных"
          onClick={() => inputRef.current?.click()}
          onKeyDown={(e) => {
            if (e.key === 'Enter' || e.key === ' ') {
              e.preventDefault()
              inputRef.current?.click()
            }
          }}
          onDragOver={(e) => {
            e.preventDefault()
            setDragging(true)
          }}
          onDragLeave={() => setDragging(false)}
          onDrop={onDrop}
          className={cn(
            'flex cursor-pointer flex-col items-center justify-center rounded-xl border-2 border-dashed px-6 py-10 text-center transition-colors outline-none focus-visible:ring-2 focus-visible:ring-ring',
            dragging ? 'border-ring bg-ring/5' : 'border-border hover:border-input hover:bg-accent/40',
          )}
        >
          <UploadCloud className={cn('size-7 transition-colors', dragging ? 'text-ring' : 'text-subtle-foreground')} />
          <p className="mt-3 text-sm font-medium">{dragging ? 'Отпустите файл' : 'Перетащите JSON сюда или нажмите, чтобы выбрать'}</p>
          <p className="mt-1 text-xs text-muted-foreground">до {formatBytes(MAX_FILE_BYTES)}</p>
          <input
            ref={inputRef}
            type="file"
            accept=".json,application/json"
            className="hidden"
            onChange={(e) => {
              const f = e.target.files?.[0]
              if (f) void readFile(f)
              e.target.value = ''
            }}
          />
        </div>

        {fileError && (
          <p role="alert" className="rounded-lg bg-risk-high-bg px-3.5 py-2.5 text-[13px] text-risk-high">
            {fileError}
          </p>
        )}

        {file && (
          <div className="space-y-3 rounded-xl border border-border bg-muted/30 p-4 animate-fade-in">
            <div className="flex items-center gap-3">
              <FileJson className="size-5 shrink-0 text-muted-foreground" />
              <div className="min-w-0 flex-1">
                <p className="truncate text-sm font-medium" title={file.name}>
                  {file.name}
                </p>
                <p className="text-xs text-muted-foreground num">{formatBytes(file.size)}</p>
              </div>
              <Button variant="ghost" size="icon-sm" aria-label="Убрать файл" onClick={() => setFile(null)}>
                <X />
              </Button>
            </div>
            <Counts accounts={file.body.accounts.length} services={file.body.services.length} transactions={file.body.transactions.length} />
            <div className="flex flex-wrap items-center justify-end gap-3">
              {upload.isError && <p className="mr-auto text-[13px] text-risk-high">{errorMessage(upload.error)}</p>}
              <ConfirmDialog
                title="Заменить данные?"
                description={`Текущие аккаунты, услуги и транзакции будут заменены содержимым файла «${file.name}». Отменить это нельзя.`}
                confirmLabel="Заменить и пересчитать"
                onConfirm={() => upload.mutate(file.body)}
                trigger={
                  <Button disabled={upload.isPending}>
                    {upload.isPending ? <Loader2 className="animate-spin" /> : <UploadCloud />}
                    {upload.isPending ? 'Загружаем…' : 'Загрузить'}
                  </Button>
                }
              />
            </div>
          </div>
        )}

        {phase.kind !== 'idle' && (
          <div className="space-y-3 rounded-xl border border-border p-4 animate-fade-in" role="status" aria-live="polite">
            <p className="flex items-center gap-2 text-sm font-medium">
              {phase.kind === 'recalc' ? (
                <>
                  <Loader2 className="size-4 animate-spin text-muted-foreground" />
                  Снимок принят, идёт пересчёт…
                </>
              ) : (
                <>
                  <CheckCircle2 className="size-4 text-risk-low" />
                  Готово: оценки пересчитаны{phase.durationMs !== null ? ` за ${formatDuration(phase.durationMs)}` : ''}
                </>
              )}
            </p>
            <Counts {...phase.accepted.counts} />
            <p className="truncate text-xs text-subtle-foreground num" title={phase.accepted.snapshot_id}>
              Снимок {phase.accepted.snapshot_id}
            </p>
          </div>
        )}
      </CardContent>
    </Card>
  )
}

const INTERVALS = [500, 1000, 2000, 5000, 10000, 30000]

function intervalLabel(ms: number) {
  return ms < 1000 ? `${ms} мс` : `${formatDecimal(ms / 1000, 1)} с`
}

function SimulatorCard() {
  const queryClient = useQueryClient()
  const { status } = useLive()
  const sim = useQuery({
    queryKey: queryKeys.simulator,
    queryFn: ({ signal }) => api.simulator(signal),
    refetchInterval: (q) => (q.state.data?.running ? 3000 : false),
  })
  const [accounts, setAccounts] = useState('1000')

  const command = useMutation({
    mutationFn: api.putSimulator,
    onMutate: async (cmd) => {
      await queryClient.cancelQueries({ queryKey: queryKeys.simulator })
      const prev = queryClient.getQueryData(queryKeys.simulator)
      if (sim.data) queryClient.setQueryData(queryKeys.simulator, { ...sim.data, ...('running' in cmd ? { running: cmd.running } : {}), ...(cmd.interval_ms ? { interval_ms: cmd.interval_ms } : {}) })
      return { prev }
    },
    onError: (_e, _c, ctx) => ctx?.prev && queryClient.setQueryData(queryKeys.simulator, ctx.prev),
    onSuccess: (data) => queryClient.setQueryData(queryKeys.simulator, data),
  })

  const s = sim.data
  const intervals = s && !INTERVALS.includes(s.interval_ms) ? [...INTERVALS, s.interval_ms].sort((a, b) => a - b) : INTERVALS
  const accountsNum = Number(accounts)
  const accountsValid = Number.isInteger(accountsNum) && accountsNum >= 1 && accountsNum <= 200000

  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          <Zap className="size-4 text-chart-forecast" />
          Живой поток изменений
        </CardTitle>
        <CardDescription>Демонстрационный источник: меняет балансы, продления и статусы услуг, а панель обновляется на глазах.</CardDescription>
      </CardHeader>
      <CardContent className="space-y-5">
        {sim.isPending ? (
          <Skeleton className="h-24 rounded-lg" />
        ) : sim.isError || !s ? (
          <ErrorState error={sim.error} onRetry={() => void sim.refetch()} className="py-6" />
        ) : (
          <>
            <div className="flex items-center justify-between gap-4 rounded-lg border border-border px-4 py-3">
              <div>
                <p className="text-sm font-medium">{s.running ? 'Поток включён' : 'Поток выключен'}</p>
                <p className="mt-0.5 text-xs text-muted-foreground num">
                  {s.running ? `событие каждые ${intervalLabel(s.interval_ms)}` : 'данные не меняются'} · отправлено {formatInt(s.events_emitted)}
                </p>
              </div>
              <Switch
                checked={s.running}
                disabled={command.isPending}
                onCheckedChange={(running) => command.mutate({ running })}
                aria-label="Живой поток изменений"
              />
            </div>
            <label className="flex items-center justify-between gap-4">
              <span className="text-sm">Как часто</span>
              <Select
                ariaLabel="Интервал между событиями"
                value={String(s.interval_ms)}
                onValueChange={(v) => command.mutate({ interval_ms: Number(v) })}
                options={intervals.map((ms) => ({ value: String(ms), label: intervalLabel(ms) }))}
                className="w-28"
              />
            </label>
            {command.isError && <p className="text-[13px] text-risk-high">{errorMessage(command.error)}</p>}
            {s.running && status !== 'online' && (
              <p className="text-[13px] text-risk-medium">Нет живого соединения: изменения появятся после переподключения.</p>
            )}
          </>
        )}

        <div className="border-t border-border pt-5">
          <p className="text-sm font-medium">Сгенерировать расширенный набор</p>
          <p className="mt-1 text-[13px] leading-5 text-muted-foreground">
            Заменит текущие данные синтетическими клиентами. Удобно, чтобы показать работу на большом объёме.
          </p>
          <div className="mt-3 flex items-center gap-2">
            <Input
              inputMode="numeric"
              value={accounts}
              onChange={(e) => setAccounts(e.target.value.replace(/[^\d]/g, ''))}
              aria-label="Сколько аккаунтов сгенерировать"
              aria-invalid={!accountsValid || undefined}
              className="h-8 w-28 text-right num"
            />
            <span className="text-[13px] text-muted-foreground">аккаунтов</span>
            <ConfirmDialog
              title="Заменить данные синтетическими?"
              description={`Будет создано ${countLabel(accountsNum, ['аккаунт', 'аккаунта', 'аккаунтов'])} со своими услугами и транзакциями. Текущие данные заменятся.`}
              confirmLabel="Сгенерировать"
              onConfirm={() => command.mutate({ generate: { accounts: accountsNum } })}
              trigger={
                <Button variant="outline" size="sm" className="ml-auto" disabled={!accountsValid || command.isPending}>
                  Сгенерировать
                </Button>
              }
            />
          </div>
          {!accountsValid && <p className="mt-1.5 text-xs text-destructive">От 1 до 200 000 аккаунтов</p>}
        </div>
      </CardContent>
    </Card>
  )
}

export default function DataPage() {
  return (
    <>
      <PageHeader title="Данные" description="Откуда панель берёт клиентов: загрузка снимка и демонстрационный поток изменений." />
      <div className="grid items-start gap-4 lg:grid-cols-[minmax(0,1fr)_400px]">
        <SnapshotCard />
        <SimulatorCard />
      </div>
    </>
  )
}

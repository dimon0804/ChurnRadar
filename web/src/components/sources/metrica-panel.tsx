import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Check, Copy, FileSpreadsheet, Loader2, Upload, X } from 'lucide-react'
import { useId, useRef, useState } from 'react'
import { errorMessage } from '@/api/client'
import { connectorsApi, metricaSummaryQueryKey, type Connector, type ConnectorOptions } from '@/api/connectors'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Select } from '@/components/ui/select'
import { Skeleton } from '@/components/ui/skeleton'
import { useNow } from '@/hooks/use-now'
import { countLabel, formatDateTime, formatInt } from '@/lib/format'
import { formatRelative, lookbackOptions, metricaSnippet, parseClientIdsCsv, type CsvParseResult } from '@/lib/sources'

const MAX_CSV_BYTES = 20 * 1024 * 1024

function SummaryTile({ label, value, title }: { label: string; value: string; title?: string }) {
  return (
    <div className="min-w-0 rounded-lg border border-border bg-card px-3 py-2">
      <dt className="truncate text-xs text-muted-foreground">{label}</dt>
      <dd className="mt-0.5 truncate text-sm font-semibold num" title={title}>
        {value}
      </dd>
    </div>
  )
}

function CounterField({ connector: c, onSaved }: { connector: Connector; onSaved: (next: Connector) => void }) {
  const current = c.options.counter_id
  const [value, setValue] = useState(current ? String(current) : '')
  const id = useId()
  const save = useMutation({
    mutationFn: (counter: number) => connectorsApi.patch(c.id, { options: { ...c.options, counter_id: counter } }),
    onSuccess: onSaved,
  })
  const num = Number(value)
  const valid = /^\d{1,12}$/.test(value) && num >= 1
  const dirty = valid && num !== current

  if (c.managed) {
    return (
      <div className="min-w-0">
        <p className="text-xs text-muted-foreground">Номер счётчика</p>
        <p className="mt-0.5 text-sm font-medium num">
          {current ?? '—'} <span className="ml-1 text-xs font-normal text-subtle-foreground">задано в окружении</span>
        </p>
      </div>
    )
  }
  return (
    <div className="min-w-0">
      <label htmlFor={id} className="text-xs text-muted-foreground">
        Номер счётчика
      </label>
      <div className="mt-1 flex gap-2">
        <Input
          id={id}
          inputMode="numeric"
          value={value}
          onChange={(e) => setValue(e.target.value.replace(/[^\d]/g, ''))}
          aria-invalid={(value !== '' && !valid) || undefined}
          className="h-8 w-36 num"
        />
        <Button size="sm" variant="outline" disabled={!dirty || save.isPending} onClick={() => save.mutate(num)}>
          {save.isPending ? <Loader2 className="animate-spin" /> : <Check />}
          Сохранить
        </Button>
      </div>
      {save.isError && <p className="mt-1 text-xs text-risk-high">{errorMessage(save.error)}</p>}
    </div>
  )
}

function ClientIdsUpload() {
  const queryClient = useQueryClient()
  const inputRef = useRef<HTMLInputElement>(null)
  const [file, setFile] = useState<{ name: string; parsed: CsvParseResult } | null>(null)
  const [fileError, setFileError] = useState<string | null>(null)
  const [replace, setReplace] = useState(false)
  const replaceId = useId()

  const upload = useMutation({
    mutationFn: () => connectorsApi.uploadClientIds({ items: file?.parsed.items ?? [], replace }),
    onSuccess: () => {
      setFile(null)
      void queryClient.invalidateQueries({ queryKey: metricaSummaryQueryKey })
    },
  })

  const readFile = async (f: File) => {
    setFileError(null)
    upload.reset()
    if (f.size > MAX_CSV_BYTES) {
      setFileError('Файл больше 20 МБ. Разбейте его на части и загрузите по очереди.')
      return
    }
    const parsed = parseClientIdsCsv(await f.text())
    if (parsed.items.length === 0 && parsed.errors.length === 0) {
      setFileError('В файле нет ни одной строки с данными.')
      return
    }
    setFile({ name: f.name, parsed })
  }

  const p = file?.parsed
  const canSend = p && p.items.length > 0 && p.errors.length === 0

  return (
    <div className="space-y-3">
      <div>
        <p className="text-sm font-medium">Соответствия clientID → аккаунт</p>
        <p className="mt-0.5 text-[13px] leading-5 text-muted-foreground">
          Запасной путь, если сайт не передаёт номер аккаунта. CSV из двух колонок client_id и account_id, заголовок
          можно не писать, разделитель — запятая, точка с запятой или таб.
        </p>
      </div>
      <div className="flex flex-wrap items-center gap-3">
        <Button variant="outline" size="sm" onClick={() => inputRef.current?.click()} aria-label="Выбрать CSV с соответствиями clientID">
          <FileSpreadsheet />
          Выбрать CSV
        </Button>
        <label htmlFor={replaceId} className="flex cursor-pointer items-center gap-2 text-[13px]">
          <input
            id={replaceId}
            type="checkbox"
            checked={replace}
            onChange={(e) => setReplace(e.target.checked)}
            className="size-4 rounded border-input accent-primary"
          />
          заменить всю таблицу
        </label>
        <input
          ref={inputRef}
          type="file"
          accept=".csv,.tsv,.txt,text/csv,text/plain"
          className="hidden"
          data-testid="client-ids-input"
          onChange={(e) => {
            const f = e.target.files?.[0]
            if (f) void readFile(f)
            e.target.value = ''
          }}
        />
      </div>

      {fileError && (
        <p role="alert" className="rounded-lg bg-risk-high-bg px-3 py-2 text-[13px] text-risk-high">
          {fileError}
        </p>
      )}

      {file && p && (
        <div className="space-y-2.5 rounded-lg border border-border bg-muted/30 p-3 animate-fade-in">
          <div className="flex items-center gap-2">
            <p className="min-w-0 flex-1 truncate text-sm font-medium" title={file.name}>
              {file.name}
            </p>
            <Button variant="ghost" size="icon-sm" aria-label="Убрать файл" onClick={() => setFile(null)}>
              <X />
            </Button>
          </div>
          <p className="text-[13px] text-muted-foreground num">
            Прочитано: {countLabel(p.items.length, ['соответствие', 'соответствия', 'соответствий'])}
            {p.header && ', заголовок пропущен'}
            {p.duplicates > 0 && `, повторов clientID: ${formatInt(p.duplicates)} (взяли последний)`}
          </p>
          {p.errors.length > 0 && (
            <div role="alert" className="rounded-md bg-risk-high-bg px-3 py-2 text-[13px] text-risk-high">
              <p className="font-medium">Исправьте файл и выберите его снова:</p>
              <ul className="mt-1 space-y-0.5">
                {p.errors.map((e, i) => (
                  <li key={i} className="break-words">
                    {e.line > 0 ? `строка ${e.line}: ` : ''}
                    {e.message}
                  </li>
                ))}
              </ul>
            </div>
          )}
          <div className="flex flex-wrap items-center justify-end gap-2">
            {upload.isError && <p className="mr-auto text-[13px] text-risk-high">{errorMessage(upload.error)}</p>}
            <Button size="sm" disabled={!canSend || upload.isPending} onClick={() => upload.mutate()}>
              {upload.isPending ? <Loader2 className="animate-spin" /> : <Upload />}
              {replace ? 'Заменить таблицу' : 'Загрузить'}
            </Button>
          </div>
        </div>
      )}

      {upload.isSuccess && (
        <p role="status" className="text-[13px] text-risk-low">
          Записано {formatInt(upload.data.accepted)}, всего в таблице {formatInt(upload.data.total)}. Визиты свяжутся с
          клиентами при следующем запуске коннектора.
        </p>
      )}
    </div>
  )
}

function Snippet({ counterId, accountParam }: { counterId: number | undefined; accountParam: string | undefined }) {
  const code = metricaSnippet(counterId, accountParam || 'account_id')
  const [copied, setCopied] = useState(false)
  return (
    <div>
      <p className="text-sm font-medium">Как связать сайт</p>
      <p className="mt-0.5 text-[13px] leading-5 text-muted-foreground">
        Добавьте в личный кабинет строку, которая срабатывает после входа, и подставьте номер аккаунта клиента:
      </p>
      <div className="mt-2 flex items-start gap-2 rounded-lg border border-border bg-muted/40 p-2.5">
        <code className="min-w-0 flex-1 text-xs leading-5 break-all">{code}</code>
        <Button
          variant="ghost"
          size="icon-sm"
          aria-label="Скопировать строку для сайта"
          onClick={() => {
            void navigator.clipboard?.writeText(code).then(() => {
              setCopied(true)
              window.setTimeout(() => setCopied(false), 1500)
            })
          }}
        >
          {copied ? <Check /> : <Copy />}
        </Button>
      </div>
    </div>
  )
}

export function MetricaPanel({ connector: c, onSaved }: { connector: Connector; onSaved: (next: Connector) => void }) {
  const now = useNow(30000)
  const summary = useQuery({
    queryKey: metricaSummaryQueryKey,
    queryFn: ({ signal }) => connectorsApi.metrica(signal),
  })
  const lookback = useMutation({
    mutationFn: (days: number) => connectorsApi.patch(c.id, { options: { ...c.options, lookback_days: days } as ConnectorOptions }),
    onSuccess: onSaved,
  })
  const o = c.options
  const s = summary.data

  return (
    <div className="space-y-5 rounded-xl border border-border bg-muted/20 p-3.5 sm:p-4" data-testid="metrica-panel">
      <div className="flex flex-wrap items-end gap-x-6 gap-y-3">
        <CounterField key={o.counter_id ?? 'none'} connector={c} onSaved={onSaved} />
        <div>
          <p className="text-xs text-muted-foreground">Глубина первой выгрузки</p>
          <Select
            ariaLabel="Глубина первой выгрузки Метрики"
            value={String(o.lookback_days ?? 30)}
            onValueChange={(v) => lookback.mutate(Number(v))}
            options={lookbackOptions(o.lookback_days).map((d) => ({ value: String(d), label: countLabel(d, ['день', 'дня', 'дней']) }))}
            className="mt-1 w-28"
          />
        </div>
      </div>
      {lookback.isError && <p className="text-xs text-risk-high">{errorMessage(lookback.error)}</p>}

      {summary.isPending ? (
        <Skeleton className="h-14 rounded-lg" />
      ) : summary.isError || !s ? (
        <p className="text-[13px] text-risk-high">{errorMessage(summary.error)}</p>
      ) : (
        <dl className="grid grid-cols-2 gap-2 sm:grid-cols-4" aria-label="Что известно из Метрики">
          <SummaryTile label="Клиентов с визитами" value={formatInt(s.accounts_with_activity)} />
          <SummaryTile label="Соответствий clientID" value={formatInt(s.client_ids)} title={s.client_ids_updated_at ? `обновлено ${formatDateTime(s.client_ids_updated_at)}` : undefined} />
          <SummaryTile label="Последний визит" value={formatRelative(s.last_visit_at, now)} title={formatDateTime(s.last_visit_at)} />
          <SummaryTile label="Пересчитано" value={formatRelative(s.computed_at, now)} title={formatDateTime(s.computed_at)} />
        </dl>
      )}

      <Snippet counterId={o.counter_id} accountParam={o.account_param} />
      <ClientIdsUpload />
    </div>
  )
}

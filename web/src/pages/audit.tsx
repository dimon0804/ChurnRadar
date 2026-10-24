import { keepPreviousData, useQuery } from '@tanstack/react-query'
import { ChevronDown, ChevronLeft, ChevronRight, Download, History, Search, X } from 'lucide-react'
import { Fragment, useEffect, useId, useMemo, useRef, useState } from 'react'
import { useSearchParams } from 'react-router'
import { api, queryKeys } from '@/api/endpoints'
import type { AuditEvent } from '@/api/types'
import { PageHeader } from '@/components/page-header'
import { EmptyState, ErrorState } from '@/components/states'
import { Button } from '@/components/ui/button'
import { Card } from '@/components/ui/card'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Select } from '@/components/ui/select'
import { Skeleton } from '@/components/ui/skeleton'
import { Switch } from '@/components/ui/switch'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table'
import {
  ACTOR_TYPE_LABEL,
  ACTOR_TYPES,
  actorBadge,
  actorName,
  AUDIT_PAGE_SIZE,
  auditExportUrl,
  DEFAULT_AUDIT_FILTERS,
  formatDetails,
  hasActiveFilters,
  objectLabel,
  parseAuditFilters,
  serializeAuditFilters,
  toAuditQuery,
  totalPages,
  type AuditFilters,
} from '@/lib/audit'
import { formatDateTime, formatInt } from '@/lib/format'
import { cn } from '@/lib/utils'

const ALL = '__all'
const SERVICE_LABEL: Record<AuditEvent['service'], string> = {
  auth: 'вход и доступы',
  api: 'панель и API',
  ingest: 'загрузка данных',
  alerts: 'уведомления',
}

function exactTime(iso: string): string {
  const date = new Date(iso)
  return Number.isNaN(date.getTime()) ? iso : date.toLocaleString('ru-RU')
}

function ResultBadge({ success }: { success: boolean }) {
  return (
    <span
      className={cn(
        'inline-flex h-6 items-center rounded-full px-2 text-xs font-medium whitespace-nowrap',
        success ? 'bg-risk-low-bg text-risk-low' : 'bg-risk-high-bg text-risk-high',
      )}
    >
      {success ? 'Успешно' : 'Отказ'}
    </span>
  )
}

function EventDetails({ event }: { event: AuditEvent }) {
  const json = formatDetails(event.details)
  return (
    <div className="grid gap-4 px-5 py-4 text-[13px] md:grid-cols-[minmax(0,280px)_1fr]">
      <dl className="grid grid-cols-[auto_1fr] gap-x-3 gap-y-1.5">
        <dt className="text-muted-foreground">Когда</dt>
        <dd className="num">{exactTime(event.at)}</dd>
        <dt className="text-muted-foreground">Код действия</dt>
        <dd className="font-mono text-xs break-all">{event.action}</dd>
        <dt className="text-muted-foreground">Сервис</dt>
        <dd>{SERVICE_LABEL[event.service] ?? event.service}</dd>
        {event.actor_role && (
          <>
            <dt className="text-muted-foreground">Роль</dt>
            <dd>{event.actor_role}</dd>
          </>
        )}
        <dt className="text-muted-foreground">request_id</dt>
        <dd className="font-mono text-xs break-all select-all">{event.request_id ?? '—'}</dd>
      </dl>
      <div className="min-w-0">
        <p className="mb-1.5 text-muted-foreground">Подробности</p>
        {json ? (
          <pre className="max-h-64 overflow-auto rounded-md border border-border bg-card px-3 py-2 font-mono text-xs leading-5">{json}</pre>
        ) : (
          <p className="text-muted-foreground">Ничего дополнительно не записано.</p>
        )}
      </div>
    </div>
  )
}

export default function AuditPage() {
  const [searchParams, setSearchParams] = useSearchParams()
  const qs = searchParams.toString()
  const filters = useMemo(() => parseAuditFilters(new URLSearchParams(qs)), [qs])
  const ids = { from: useId(), to: useId(), failed: useId() }
  const [expanded, setExpanded] = useState<number | null>(null)

  const update = (patch: Partial<AuditFilters>, replace = false) => {
    // любой новый фильтр возвращает на первую страницу
    const next = { ...filters, page: 1, ...patch }
    setSearchParams(serializeAuditFilters(next), { replace, preventScrollReset: true })
    setExpanded(null)
  }

  // поиск: черновик в поле, в URL — с задержкой, чтобы не дёргать сервер на каждую букву
  const [draft, setDraft] = useState(filters.q)
  const [syncedQ, setSyncedQ] = useState(filters.q)
  if (filters.q !== syncedQ) {
    // адрес поменялся снаружи (назад/вперёд, сброс фильтров) — подтягиваем поле
    setSyncedQ(filters.q)
    if (filters.q !== draft.trim()) setDraft(filters.q)
  }
  const timer = useRef<number | undefined>(undefined)
  useEffect(() => () => window.clearTimeout(timer.current), [])
  const onSearch = (value: string) => {
    setDraft(value)
    window.clearTimeout(timer.current)
    timer.current = window.setTimeout(() => {
      // берём свежий адрес: за эти доли секунды могли поменять другой фильтр
      setSearchParams(
        (prev) => {
          const current = parseAuditFilters(prev)
          const q = value.trim()
          return q === current.q ? prev : serializeAuditFilters({ ...current, q, page: 1 })
        },
        { replace: true, preventScrollReset: true },
      )
    }, 350)
  }

  const query = toAuditQuery(filters, AUDIT_PAGE_SIZE)
  const audit = useQuery({
    queryKey: queryKeys.auditPage(query),
    queryFn: ({ signal }) => api.audit(query, signal),
    placeholderData: keepPreviousData,
  })

  const items = audit.data?.items ?? []
  const total = audit.data?.total ?? 0
  const pages = totalPages(total, AUDIT_PAGE_SIZE)
  const actionOptions = [
    { value: ALL, label: 'Все действия' },
    ...(audit.data?.actions ?? []).map((a) => ({ value: a.code, label: a.title })),
  ]
  // действие из ссылки может не попасть в список, если таких записей пока нет
  if (filters.action && !actionOptions.some((o) => o.value === filters.action)) {
    actionOptions.push({ value: filters.action, label: filters.action })
  }
  const filtered = hasActiveFilters(filters)

  return (
    <>
      <PageHeader
        title="Журнал действий"
        description="Кто, что и когда менял: входы, пользователи, токены, модель, данные, письма и отчёты. Неудачные входы и отказы в доступе тоже здесь."
        actions={
          <Button asChild variant="outline">
            <a
              href={auditExportUrl(filters)}
              download
              data-testid="audit-csv"
              title={filtered ? 'Выгрузить записи с текущими фильтрами' : 'Выгрузить весь журнал'}
            >
              <Download />
              Скачать CSV
            </a>
          </Button>
        }
      />

      <Card>
        <div className="flex flex-wrap items-end gap-3 border-b border-border px-5 py-3">
          <div className="relative w-full sm:w-64">
            <Search className="pointer-events-none absolute top-1/2 left-2.5 size-4 -translate-y-1/2 text-muted-foreground" />
            <Input
              type="search"
              value={draft}
              onChange={(e) => onSearch(e.target.value)}
              placeholder="Кто, объект, IP, request_id"
              aria-label="Поиск по журналу"
              className="h-8 pl-8"
            />
          </div>
          <Select
            value={filters.action || ALL}
            onValueChange={(v) => update({ action: v === ALL ? '' : v })}
            options={actionOptions}
            ariaLabel="Действие"
            className="w-full sm:w-56"
          />
          <Select
            value={filters.actorType || ALL}
            onValueChange={(v) => update({ actorType: v === ALL ? '' : (v as AuditFilters['actorType']) })}
            options={[{ value: ALL, label: 'Все исполнители' }, ...ACTOR_TYPES.map((t) => ({ value: t, label: ACTOR_TYPE_LABEL[t] }))]}
            ariaLabel="Кто"
            className="w-full sm:w-44"
          />
          <div className="flex items-center gap-2">
            <Label htmlFor={ids.from} className="text-xs text-muted-foreground">
              с
            </Label>
            <Input
              id={ids.from}
              type="date"
              value={filters.dateFrom}
              max={filters.dateTo || undefined}
              onChange={(e) => update({ dateFrom: e.target.value })}
              className="h-8 w-36 text-[13px]"
            />
            <Label htmlFor={ids.to} className="text-xs text-muted-foreground">
              по
            </Label>
            <Input
              id={ids.to}
              type="date"
              value={filters.dateTo}
              min={filters.dateFrom || undefined}
              onChange={(e) => update({ dateTo: e.target.value })}
              className="h-8 w-36 text-[13px]"
            />
          </div>
          <div className="flex h-8 items-center gap-2">
            <Switch id={ids.failed} checked={filters.failedOnly} onCheckedChange={(v) => update({ failedOnly: v })} />
            <Label htmlFor={ids.failed}>Только отказы</Label>
          </div>
          {filtered && (
            <Button variant="ghost" size="sm" onClick={() => update(DEFAULT_AUDIT_FILTERS)}>
              <X />
              Сбросить
            </Button>
          )}
          <p className="ml-auto self-center text-xs text-muted-foreground num" aria-live="polite">
            {audit.data ? `Записей: ${formatInt(total)}` : ''}
          </p>
        </div>

        {audit.isPending ? (
          <div className="space-y-2 p-5">
            {Array.from({ length: 6 }, (_, i) => (
              <Skeleton key={i} className="h-9" />
            ))}
          </div>
        ) : audit.isError ? (
          <ErrorState error={audit.error} onRetry={() => void audit.refetch()} />
        ) : items.length === 0 ? (
          <EmptyState
            icon={<History />}
            title={filtered ? 'Под фильтры ничего не попало' : 'Журнал пока пуст'}
            description={filtered ? 'Попробуйте расширить даты или убрать часть фильтров.' : 'Записи появятся после первых действий в панели.'}
          />
        ) : (
          <div className={cn('transition-opacity', audit.isPlaceholderData && 'opacity-60')}>
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Время</TableHead>
                  <TableHead>Кто</TableHead>
                  <TableHead>Действие</TableHead>
                  <TableHead>Объект</TableHead>
                  <TableHead>IP</TableHead>
                  <TableHead>Результат</TableHead>
                  <TableHead className="w-10">
                    <span className="sr-only">Подробности</span>
                  </TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {items.map((e) => {
                  const open = expanded === e.id
                  const badge = actorBadge(e.actor_type)
                  return (
                    <Fragment key={e.id}>
                      <TableRow
                        data-testid="audit-row"
                        className={cn('cursor-pointer hover:bg-accent/40', open && 'border-b-0 bg-accent/40')}
                        onClick={() => setExpanded(open ? null : e.id)}
                      >
                        <TableCell className="text-[13px] whitespace-nowrap text-muted-foreground num" title={exactTime(e.at)}>
                          {formatDateTime(e.at)}
                        </TableCell>
                        <TableCell className="text-[13px]">
                          <span className="inline-flex items-center gap-1.5 whitespace-nowrap">
                            <span className="font-medium">{actorName(e)}</span>
                            {badge && (
                              <span className="rounded bg-muted px-1.5 py-px text-[11px] font-medium text-muted-foreground">{badge}</span>
                            )}
                          </span>
                        </TableCell>
                        <TableCell className="text-[13px]">{e.action_title || e.action}</TableCell>
                        <TableCell className="max-w-56 truncate text-[13px] text-muted-foreground" title={objectLabel(e)}>
                          {objectLabel(e)}
                        </TableCell>
                        <TableCell className="font-mono text-xs whitespace-nowrap text-muted-foreground">{e.ip ?? '—'}</TableCell>
                        <TableCell>
                          <ResultBadge success={e.success} />
                        </TableCell>
                        <TableCell>
                          <Button
                            variant="ghost"
                            size="icon-sm"
                            aria-expanded={open}
                            aria-label={open ? 'Скрыть подробности' : 'Показать подробности'}
                            onClick={(ev) => {
                              ev.stopPropagation()
                              setExpanded(open ? null : e.id)
                            }}
                          >
                            <ChevronDown className={cn('transition-transform', open && 'rotate-180')} />
                          </Button>
                        </TableCell>
                      </TableRow>
                      {open && (
                        <TableRow className="bg-accent/40">
                          <TableCell colSpan={7} className="p-0 first:pl-0 last:pr-0">
                            <EventDetails event={e} />
                          </TableCell>
                        </TableRow>
                      )}
                    </Fragment>
                  )
                })}
              </TableBody>
            </Table>

            {pages > 1 && (
              <div className="flex flex-wrap items-center justify-between gap-3 border-t border-border px-5 py-3 text-[13px] text-muted-foreground">
                <span className="num">
                  {formatInt((filters.page - 1) * AUDIT_PAGE_SIZE + 1)}–{formatInt(Math.min(total, filters.page * AUDIT_PAGE_SIZE))} из{' '}
                  {formatInt(total)}
                </span>
                <div className="flex items-center gap-1">
                  <Button
                    variant="outline"
                    size="icon-sm"
                    aria-label="Предыдущая страница"
                    disabled={filters.page <= 1}
                    onClick={() => update({ page: filters.page - 1 })}
                  >
                    <ChevronLeft />
                  </Button>
                  <span className="min-w-20 text-center num">
                    {formatInt(filters.page)} из {formatInt(pages)}
                  </span>
                  <Button
                    variant="outline"
                    size="icon-sm"
                    aria-label="Следующая страница"
                    disabled={filters.page >= pages}
                    onClick={() => update({ page: filters.page + 1 })}
                  >
                    <ChevronRight />
                  </Button>
                </div>
              </div>
            )}
          </div>
        )}
      </Card>
    </>
  )
}

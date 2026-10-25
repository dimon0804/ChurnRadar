import { keepPreviousData, useQuery } from '@tanstack/react-query'
import { createColumnHelper, flexRender, getCoreRowModel, useReactTable, type SortingState } from '@tanstack/react-table'
import { ArrowDown, ArrowUp, ChevronLeft, ChevronRight, ChevronsUpDown, Download, Mails, ScatterChart as ScatterIcon, Search, Table2, Users, X } from 'lucide-react'
import { useEffect, useMemo, useRef, useState, type KeyboardEvent, type MouseEvent } from 'react'
import { Link, Outlet, useNavigate, useSearchParams } from 'react-router'
import { api, queryKeys } from '@/api/endpoints'
import type { CustomerPage, CustomerSort, CustomerSummary, RiskLevel } from '@/api/types'
import { CampaignDialog } from '@/components/campaign-dialog'
import { ReasonPanel } from '@/components/charts/reason-panel'
import { RiskScatter } from '@/components/charts/risk-scatter'
import { PageHeader, RecalcNote } from '@/components/page-header'
import { RiskBadge, RiskGlyph, RISK_TEXT, ScoreBar } from '@/components/risk'
import { EmptyState, ErrorState } from '@/components/states'
import { Button } from '@/components/ui/button'
import { Card } from '@/components/ui/card'
import { Input } from '@/components/ui/input'
import { Select } from '@/components/ui/select'
import { Skeleton } from '@/components/ui/skeleton'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table'
import { useDebouncedValue } from '@/hooks/use-debounced'
import { countLabel, formatInt, formatMoney } from '@/lib/format'
import { RISK_LABEL } from '@/lib/labels'
import { FEATURE_TITLE, isChurned, reasonAction, reasonTooltip, shortReasonTitle } from '@/lib/reasons'
import { diffRows, scoreTrend } from '@/lib/recalc'
import {
  clearReason,
  clearScoreRange,
  DEFAULT_CUSTOMERS_STATE,
  nextSort,
  PAGE_SIZES,
  parseCustomersState,
  RISK_LEVELS,
  serializeCustomersState,
  toCustomersQuery,
  toggleLevel,
  type CustomersState,
} from '@/lib/url-state'
import { exportUrlFromState } from '@/lib/contact-today'
import { cn } from '@/lib/utils'
import { useAuth } from '@/providers/auth'
import { useLive } from '@/providers/live'

const SORTABLE = new Set<string>(['id', 'email', 'balance', 'total_replenished', 'risk_score', 'expected_loss'])

const col = createColumnHelper<CustomerSummary>()

/** Что передаём ячейкам через meta таблицы. */
interface CellContext {
  titles?: ReadonlyMap<string, string>
  trend?: ReadonlyMap<number, 'up' | 'down'>
  /** Отмеченные галочкой — для рассылки «Написать выбранным». */
  selected?: ReadonlySet<number>
  onToggle?: (ids: number[], on: boolean) => void
  /** Ссылка «клиенты с этой главной причиной» с сохранением остальных фильтров. */
  reasonLink?: (code: string) => string
}

const columns = [
  col.display({
    id: 'select',
    meta: { className: 'w-[40px] pr-0' },
    header: (h) => {
      const meta = h.table.options.meta as CellContext | undefined
      const ids = h.table.getRowModel().rows.map((r) => r.original.id)
      const all = ids.length > 0 && ids.every((id) => meta?.selected?.has(id))
      return (
        <input
          type="checkbox"
          className="size-4 cursor-pointer accent-primary align-middle"
          aria-label="Выбрать всех на странице"
          disabled={ids.length === 0}
          checked={all}
          onChange={(e) => meta?.onToggle?.(ids, e.target.checked)}
        />
      )
    },
    cell: (c) => {
      const meta = c.table.options.meta as CellContext | undefined
      const id = c.row.original.id
      return (
        <input
          type="checkbox"
          className="size-4 cursor-pointer accent-primary align-middle"
          aria-label={`Выбрать клиента ${c.row.original.email || id}`}
          checked={meta?.selected?.has(id) ?? false}
          onChange={(e) => meta?.onToggle?.([id], e.target.checked)}
        />
      )
    },
  }),
  col.accessor('id', {
    header: 'ID',
    // на телефоне прячем второстепенные колонки, чтобы риск был виден без прокрутки
    meta: { className: 'w-[76px] max-md:hidden' },
    cell: (c) => <span className="text-muted-foreground num">{c.getValue()}</span>,
  }),
  col.accessor('email', {
    header: 'Email',
    meta: { className: 'min-w-[150px] max-w-[240px]' },
    cell: (c) => (
      <span className="block truncate font-medium" title={c.getValue()}>
        {c.getValue() || '—'}
      </span>
    ),
  }),
  col.accessor('balance', {
    header: 'Баланс',
    meta: { align: 'right', className: 'max-md:hidden' },
    cell: (c) => <span className="num">{formatMoney(c.getValue())}</span>,
  }),
  col.accessor('total_replenished', {
    header: 'Пополнено всего',
    meta: { align: 'right', className: 'max-md:hidden' },
    cell: (c) => <span className="num">{formatMoney(c.getValue())}</span>,
  }),
  col.accessor('risk_score', {
    header: 'Риск',
    meta: { className: 'w-[140px] whitespace-nowrap' },
    cell: (c) => (
      <ScoreBar
        score={c.getValue()}
        level={c.row.original.risk_level}
        trend={(c.table.options.meta as CellContext | undefined)?.trend?.get(c.row.original.id) ?? null}
      />
    ),
  }),
  col.accessor('risk_level', {
    header: 'Уровень',
    enableSorting: false,
    meta: { className: 'w-[124px] whitespace-nowrap' },
    cell: (c) => <RiskBadge level={c.getValue()} churned={isChurned(c.row.original)} />,
  }),
  col.display({
    id: 'reason',
    header: 'Главная причина и что сделать',
    meta: { className: 'w-full min-w-[160px] max-w-0' },
    cell: (c) => {
      const reason = c.row.original.reasons?.[0]
      const unknown = c.row.original.risk_level === 'unknown'
      if (!reason) return <span className="text-subtle-foreground">—</span>
      const meta = c.table.options.meta as CellContext | undefined
      const titles = meta?.titles
      const action = reasonAction(reason)
      const label = shortReasonTitle(reason, titles)
      return (
        <div className="min-w-0" title={reasonTooltip(reason)}>
          {meta?.reasonLink ? (
            <Link
              to={meta.reasonLink(reason.code)}
              data-reason={reason.code}
              aria-label={`${label}: показать всех клиентов с этой главной причиной`}
              className={cn(
                'block max-w-full truncate rounded-sm underline-offset-2 outline-none hover:underline focus-visible:ring-2 focus-visible:ring-ring',
                unknown ? 'text-muted-foreground' : 'font-medium',
              )}
            >
              {label}
            </Link>
          ) : (
            <p className={cn('truncate', unknown ? 'text-muted-foreground' : 'font-medium')}>{label}</p>
          )}
          {action && (
            <p className="truncate text-xs leading-5 text-muted-foreground">
              <span className="text-subtle-foreground" aria-hidden>
                →{' '}
              </span>
              <span className="sr-only">Что сделать: </span>
              {action}
            </p>
          )}
        </div>
      )
    },
  }),
  col.accessor('expected_loss', {
    header: 'Ожидаемая потеря',
    meta: { align: 'right' },
    cell: (c) => {
      const v = c.getValue()
      return <span className={cn('num', v > 0 ? 'font-medium' : 'text-muted-foreground')}>{formatMoney(v)}</span>
    },
  }),
]

interface ColumnMeta {
  className?: string
  align?: 'right'
}

const ARIA_SORT = { asc: 'ascending', desc: 'descending' } as const

interface FlashState {
  data: CustomerPage | null
  signatures: ReadonlyMap<number, string>
  scores: ReadonlyMap<number, number | null>
  ids: ReadonlySet<number>
  trend: ReadonlyMap<number, 'up' | 'down'>
  stamp: number
}

const EMPTY_FLASH: FlashState = { data: null, signatures: new Map(), scores: new Map(), ids: new Set(), trend: new Map(), stamp: 0 }

const rowSignature = (c: CustomerSummary) =>
  [c.risk_level, c.risk_score === null ? '' : c.risk_score.toFixed(1), c.expected_loss, c.balance, c.total_replenished, c.reasons?.[0]?.code ?? ''].join('|')

function nextFlash(prev: FlashState, data: CustomerPage, live: ReadonlyMap<number, number>): FlashState {
  const { changed, next } = diffRows(prev.signatures, data.items, rowSignature)
  const ids = new Set(changed)
  // клиенты из события scores.updated, если они на этой странице и это не первая загрузка
  if (prev.data) for (const item of data.items) if (live.has(item.id) && prev.signatures.has(item.id)) ids.add(item.id)
  const trend = new Map<number, 'up' | 'down'>()
  const scores = new Map<number, number | null>()
  for (const item of data.items) {
    scores.set(item.id, item.risk_score)
    if (!ids.has(item.id)) continue
    const t = scoreTrend(prev.scores.get(item.id), item.risk_score)
    if (t) trend.set(item.id, t)
  }
  return { data, signatures: next, scores, ids, trend, stamp: ids.size > 0 ? prev.stamp + 1 : prev.stamp }
}

function SortButton({
  label,
  active,
  order,
  align,
  onClick,
}: {
  label: string
  active: boolean
  order: 'asc' | 'desc'
  align?: 'right'
  onClick: () => void
}) {
  const Icon = !active ? ChevronsUpDown : order === 'asc' ? ArrowUp : ArrowDown
  const nextLabel = active ? (order === 'asc' ? 'по убыванию' : 'по возрастанию') : ''
  return (
    <button
      type="button"
      onClick={onClick}
      className={cn(
        '-mx-1.5 inline-flex h-7 items-center gap-1 rounded px-1.5 font-medium outline-none hover:bg-accent hover:text-foreground focus-visible:ring-2 focus-visible:ring-ring',
        active && 'text-foreground',
        align === 'right' && 'flex-row-reverse',
      )}
      title={active ? `Сортировать ${nextLabel}` : 'Сортировать'}
    >
      {label}
      <Icon className={cn('size-3.5 shrink-0', active ? 'opacity-100' : 'opacity-40')} aria-hidden />
    </button>
  )
}

function LevelChips({
  selected,
  counts,
  onToggle,
  onClear,
}: {
  selected: RiskLevel[]
  counts?: Partial<Record<RiskLevel, number>>
  onToggle: (level: RiskLevel) => void
  onClear: () => void
}) {
  const chip = 'inline-flex h-8 items-center gap-1.5 rounded-full border px-3 text-[13px] font-medium transition-colors outline-none focus-visible:ring-2 focus-visible:ring-ring'
  return (
    <div className="flex flex-wrap items-center gap-1.5" role="group" aria-label="Фильтр по уровню риска">
      <button
        type="button"
        aria-pressed={selected.length === 0}
        onClick={onClear}
        className={cn(chip, selected.length === 0 ? 'border-brand bg-brand-soft text-foreground' : 'border-border bg-card text-muted-foreground hover:text-foreground')}
      >
        Все
      </button>
      {RISK_LEVELS.map((l) => {
        const on = selected.includes(l)
        const count = counts?.[l]
        return (
          <button
            key={l}
            type="button"
            aria-pressed={on}
            onClick={() => onToggle(l)}
            className={cn(
              chip,
              on ? 'border-foreground/70 bg-accent text-foreground' : 'border-border bg-card text-muted-foreground hover:text-foreground',
            )}
          >
            <RiskGlyph level={l} className={RISK_TEXT[l]} />
            {RISK_LABEL[l]}
            {typeof count === 'number' && <span className="text-xs text-subtle-foreground num">{formatInt(count)}</span>}
          </button>
        )
      })}
    </div>
  )
}

/** Чип активного фильтра с кнопкой сброса: «Причина: Ждёт оплаты ×». */
function FilterChip({ label, value, onClear, testId }: { label: string; value: string; onClear: () => void; testId: string }) {
  return (
    <span
      className="inline-flex h-8 max-w-full items-center gap-1 rounded-full border border-foreground/70 bg-accent pr-1 pl-3 text-[13px]"
      data-testid={testId}
    >
      <span className="text-muted-foreground">{label}:</span>
      <span className="min-w-0 truncate font-medium">{value}</span>
      <button
        type="button"
        onClick={onClear}
        className="inline-flex size-6 shrink-0 items-center justify-center rounded-full text-muted-foreground outline-none hover:bg-background hover:text-foreground focus-visible:ring-2 focus-visible:ring-ring"
        aria-label={`Сбросить фильтр «${label}: ${value}»`}
      >
        <X className="size-3.5" />
      </button>
    </span>
  )
}

function ViewSwitch({ view, onChange }: { view: 'table' | 'chart'; onChange: (v: 'table' | 'chart') => void }) {
  return (
    <div className="inline-flex shrink-0 rounded-md border border-border p-0.5" role="group" aria-label="Вид списка">
      {(
        [
          ['table', 'Таблица', Table2],
          ['chart', 'График', ScatterIcon],
        ] as const
      ).map(([key, text, Icon]) => (
        <button
          key={key}
          type="button"
          aria-pressed={view === key}
          onClick={() => onChange(key)}
          data-testid={`customers-view-${key}`}
          className={cn(
            'inline-flex h-8 items-center gap-1.5 rounded px-2.5 text-[13px] font-medium transition-colors outline-none focus-visible:ring-2 focus-visible:ring-ring',
            view === key ? 'bg-accent text-foreground' : 'text-muted-foreground hover:text-foreground',
          )}
        >
          <Icon className="size-3.5" aria-hidden />
          <span className="max-sm:sr-only">{text}</span>
        </button>
      ))}
    </div>
  )
}

function SkeletonRows({ count }: { count: number }) {
  return (
    <>
      {Array.from({ length: count }, (_, i) => (
        <TableRow key={i}>
          {columns.map((c, j) => (
            <TableCell key={j} className={(c.meta as ColumnMeta | undefined)?.className}>
              <Skeleton className={cn('h-4', j === 0 ? 'w-4' : j === 2 ? 'w-40' : j === 7 ? 'w-48' : 'w-16')} />
            </TableCell>
          ))}
        </TableRow>
      ))}
    </>
  )
}

export default function CustomersPage() {
  const [searchParams, setSearchParams] = useSearchParams()
  const navigate = useNavigate()
  const { isAdmin, canAct } = useAuth()
  const { changed } = useLive()
  const qs = searchParams.toString()
  const state = useMemo(() => parseCustomersState(new URLSearchParams(qs)), [qs])

  const update = (next: CustomersState, replace = false) => {
    setSearchParams(serializeCustomersState(next), { replace, preventScrollReset: true })
  }

  // поиск: локальный черновик + дебаунс в URL
  const [draft, setDraft] = useState(state.search)
  const debounced = useDebouncedValue(draft, 300)
  const pushed = useRef(state.search)
  useEffect(() => {
    if (state.search !== pushed.current) {
      pushed.current = state.search
      setDraft(state.search)
    }
  }, [state.search])
  useEffect(() => {
    const value = debounced.trim()
    if (value === state.search.trim()) return
    pushed.current = value
    update({ ...state, search: value, page: 1 }, true)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [debounced])

  const query = toCustomersQuery(state)
  const customers = useQuery({
    queryKey: queryKeys.customerList(query),
    queryFn: ({ signal }) => api.customers(query, signal),
    placeholderData: keepPreviousData,
  })
  const summary = useQuery({ queryKey: queryKeys.summary, queryFn: ({ signal }) => api.summary(signal) })

  const data = customers.data
  const items = useMemo(() => data?.items ?? [], [data])
  const titles = useMemo(
    () => new Map((summary.data?.top_reasons ?? []).map((r) => [r.code, r.title] as const)),
    [summary.data?.top_reasons],
  )

  // После пересчёта подсвечиваем строки, у которых поменялись цифры, и показываем, куда сдвинулся риск.
  // Считаем прямо при новой загрузке страницы таблицы, сравнивая с прошлой: так подсветка совпадает
  // с тем, что видно на экране, и работает и для частичного, и для полного пересчёта (full=true).
  const [flash, setFlash] = useState<FlashState>(EMPTY_FLASH)
  if (data && data !== flash.data && !customers.isPlaceholderData) {
    setFlash(nextFlash(flash, data, changed))
  }
  const total = data?.total ?? 0
  const totalPages = Math.max(1, Math.ceil(total / state.pageSize))

  // страница за пределами (например, данные уменьшились) — на последнюю
  useEffect(() => {
    if (data && state.page > totalPages && total > 0) update({ ...state, page: totalPages }, true)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [data, totalPages])

  const sorting: SortingState = [{ id: state.sort, desc: state.order === 'desc' }]
  const [selected, setSelected] = useState<ReadonlySet<number>>(() => new Set())
  const [writing, setWriting] = useState(false)
  const toggleSelected = (ids: number[], on: boolean) => {
    setSelected((prev) => {
      const next = new Set(prev)
      for (const id of ids) {
        if (on) next.add(id)
        else next.delete(id)
      }
      return next
    })
  }
  // eslint-disable-next-line react-hooks/incompatible-library
  const table = useReactTable({
    data: items,
    columns,
    // галочки нужны только для рассылки, а писать клиентам аналитику нельзя
    state: { sorting, columnVisibility: { select: canAct } },
    manualSorting: true,
    manualPagination: true,
    enableSortingRemoval: false,
    getCoreRowModel: getCoreRowModel(),
    getRowId: (row) => String(row.id),
    meta: {
      titles,
      trend: flash.trend,
      selected,
      onToggle: toggleSelected,
      reasonLink: (code: string) => `/customers?${serializeCustomersState({ ...state, reason: code, reasonScope: 'main', page: 1 }).toString()}`,
    } satisfies CellContext,
  })

  const filtered =
    state.levels.length > 0 || state.search.trim() !== '' || Boolean(state.reason) || state.scoreMin !== undefined || state.scoreMax !== undefined
  const view = state.view ?? 'table'
  const reasonTitle = state.reason ? (titles.get(state.reason) ?? FEATURE_TITLE[state.reason] ?? state.reason) : ''
  const scoreText =
    state.scoreMin !== undefined || state.scoreMax !== undefined ? `${state.scoreMin ?? 0}–${state.scoreMax ?? 100}` : ''
  const detailSuffix = qs ? `?${qs}` : ''

  const openRow = (id: number) => navigate(`/customers/${id}${detailSuffix}`)
  const onRowClick = (event: MouseEvent, id: number) => {
    if ((event.target as HTMLElement).closest('a,button,input,label')) return
    if (window.getSelection()?.toString()) return
    openRow(id)
  }
  const onRowKey = (event: KeyboardEvent, id: number) => {
    if (event.key === 'Enter' && event.target === event.currentTarget) openRow(id)
  }

  const from = total === 0 ? 0 : (state.page - 1) * state.pageSize + 1
  const to = Math.min(total, state.page * state.pageSize)

  return (
    <>
      <PageHeader
        title="Клиенты"
        description="Рейтинг клиентов по риску ухода. Нажмите на строку, чтобы увидеть причины и деньги."
        meta={data && <RecalcNote scoredAt={data.scored_at} modelVersion={data.model_version} />}
      />

      <div className="mb-4 flex flex-wrap items-center justify-between gap-3">
        <LevelChips
          selected={state.levels}
          counts={summary.data?.by_level}
          onToggle={(l) => update(toggleLevel(state, l))}
          onClear={() => update({ ...state, levels: [], page: 1 })}
        />
        <div className="flex w-full flex-wrap items-center gap-2 sm:w-auto sm:flex-nowrap">
          <div className="relative w-full sm:w-72">
            <Search className="pointer-events-none absolute top-1/2 left-3 size-4 -translate-y-1/2 text-subtle-foreground" />
            <Input
              type="search"
              value={draft}
              onChange={(e) => setDraft(e.target.value)}
              placeholder="Email или ID клиента"
              aria-label="Поиск по email или ID"
              className="pr-8 pl-9 [&::-webkit-search-cancel-button]:hidden"
              maxLength={200}
            />
            {draft && (
              <button
                type="button"
                onClick={() => setDraft('')}
                className="absolute top-1/2 right-1.5 inline-flex size-6 -translate-y-1/2 items-center justify-center rounded text-muted-foreground hover:bg-accent hover:text-foreground"
                aria-label="Очистить поиск"
              >
                <X className="size-3.5" />
              </button>
            )}
          </div>
          <ViewSwitch view={view} onChange={(v) => update({ ...state, view: v === 'chart' ? 'chart' : undefined }, true)} />
          <Button asChild variant="outline" className="shrink-0">
            <a
              href={exportUrlFromState(state)}
              download
              title={filtered ? 'Выгрузить клиентов с текущими фильтрами и сортировкой' : 'Выгрузить всех клиентов в текущей сортировке'}
              data-testid="customers-csv"
            >
              <Download />
              Выгрузить CSV
            </a>
          </Button>
        </div>
      </div>

      {(state.reason || scoreText) && (
        <div className="mb-3 flex flex-wrap items-center gap-2" aria-label="Активные фильтры">
          {state.reason && (
            <FilterChip
              testId="chip-reason"
              label={state.reasonScope === 'main' ? 'Главная причина' : 'Причина'}
              value={reasonTitle}
              onClear={() => update(clearReason(state))}
            />
          )}
          {scoreText && <FilterChip testId="chip-score" label="Риск" value={scoreText} onClear={() => update(clearScoreRange(state))} />}
        </div>
      )}
      {state.reason && (
        <ReasonPanel
          code={state.reason}
          scope={state.reasonScope ?? 'any'}
          onScopeChange={(scope) => update({ ...state, reasonScope: scope === 'main' ? 'main' : undefined, page: 1 })}
          detailSuffix={detailSuffix}
        />
      )}

      {canAct && selected.size > 0 && (
        <div
          className="mb-3 flex flex-wrap items-center gap-3 rounded-lg border border-border bg-card px-4 py-2.5 text-sm shadow-card"
          data-testid="selection-bar"
        >
          <span className="font-medium num">Выбрано: {countLabel(selected.size, ['клиент', 'клиента', 'клиентов'])}</span>
          <Button size="sm" onClick={() => setWriting(true)}>
            <Mails />
            Написать выбранным
          </Button>
          <Button variant="ghost" size="sm" onClick={() => setSelected(new Set())}>
            Снять выбор
          </Button>
        </div>
      )}
      <CampaignDialog
        target={{ customer_ids: [...selected] }}
        title={`Написать выбранным · ${selected.size}`}
        open={writing}
        onOpenChange={setWriting}
        onStarted={() => setSelected(new Set())}
      />

      <Card className="relative overflow-hidden">
        {customers.isFetching && !customers.isPending && (
          <div className="absolute inset-x-0 top-0 z-10 h-0.5 overflow-hidden" aria-hidden>
            <div className="h-full w-1/3 animate-[loading-bar_1s_ease-in-out_infinite] bg-ring/70" />
          </div>
        )}
        {view === 'chart' ? (
          <RiskScatter state={state} detailSuffix={detailSuffix} />
        ) : customers.isError && !data ? (
          <ErrorState error={customers.error} onRetry={() => void customers.refetch()} />
        ) : (
          <>
            <Table aria-label="Клиенты под риском" aria-busy={customers.isFetching} className="table-auto">
              <TableHeader className="bg-muted/40">
                {table.getHeaderGroups().map((group) => (
                  <TableRow key={group.id} className="hover:bg-transparent">
                    {group.headers.map((header) => {
                      const meta = (header.column.columnDef.meta ?? {}) as ColumnMeta
                      const id = header.column.id
                      const sortable = SORTABLE.has(id) && header.column.getCanSort()
                      const active = state.sort === id
                      const label = String(header.column.columnDef.header ?? '')
                      return (
                        <TableHead
                          key={header.id}
                          className={cn(meta.className, meta.align === 'right' && 'text-right')}
                          aria-sort={sortable ? (active ? ARIA_SORT[state.order] : 'none') : undefined}
                          scope="col"
                        >
                          {sortable ? (
                            <SortButton
                              label={label}
                              active={active}
                              order={state.order}
                              align={meta.align}
                              onClick={() => update(nextSort(state, id as CustomerSort))}
                            />
                          ) : (
                            flexRender(header.column.columnDef.header, header.getContext())
                          )}
                        </TableHead>
                      )
                    })}
                  </TableRow>
                ))}
              </TableHeader>
              <TableBody className={cn('transition-opacity', customers.isPlaceholderData && 'opacity-60')}>
                {customers.isPending ? (
                  <SkeletonRows count={Math.min(state.pageSize, 10)} />
                ) : (
                  table.getRowModel().rows.map((row) => {
                    const id = row.original.id
                    const stamp = flash.ids.has(id) ? flash.stamp : 0
                    return (
                      <TableRow
                        key={`${id}-${stamp}`}
                        tabIndex={0}
                        onClick={(e) => onRowClick(e, id)}
                        onKeyDown={(e) => onRowKey(e, id)}
                        aria-label={`Клиент ${row.original.email || id}`}
                        className={cn(
                          'cursor-pointer outline-none hover:bg-accent/60 focus-visible:bg-accent focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-inset',
                          stamp && 'animate-row-flash',
                        )}
                      >
                        {row.getVisibleCells().map((cell) => {
                          const meta = (cell.column.columnDef.meta ?? {}) as ColumnMeta
                          return (
                            <TableCell
                              key={cell.id}
                              className={cn(meta.className, meta.align === 'right' && 'text-right whitespace-nowrap')}
                            >
                              {cell.column.id === 'email' ? (
                                <Link
                                  to={`/customers/${id}${detailSuffix}`}
                                  className="block max-w-full truncate rounded-sm font-medium outline-none hover:underline focus-visible:ring-2 focus-visible:ring-ring"
                                  title={row.original.email}
                                  tabIndex={-1}
                                >
                                  {row.original.email || '—'}
                                </Link>
                              ) : (
                                flexRender(cell.column.columnDef.cell, cell.getContext())
                              )}
                            </TableCell>
                          )
                        })}
                      </TableRow>
                    )
                  })
                )}
              </TableBody>
            </Table>

            {!customers.isPending && items.length === 0 && (
              filtered ? (
                <EmptyState
                  title="Никого не нашли"
                  description="Под эти условия не подходит ни один клиент. Попробуйте изменить поиск или уровни риска."
                  action={
                    <Button
                      variant="outline"
                      size="sm"
                      onClick={() => {
                        pushed.current = ''
                        setDraft('')
                        update({ ...DEFAULT_CUSTOMERS_STATE, sort: state.sort, order: state.order, pageSize: state.pageSize })
                      }}
                    >
                      Сбросить фильтры
                    </Button>
                  }
                />
              ) : (
                <EmptyState
                  icon={<Users />}
                  title="Клиентов пока нет"
                  description="Как только появятся данные об аккаунтах и услугах, здесь будет рейтинг риска."
                  action={
                    isAdmin ? (
                      <Button asChild variant="outline" size="sm">
                        <Link to="/data">Загрузить данные</Link>
                      </Button>
                    ) : undefined
                  }
                />
              )
            )}

            {total > 0 && (
              <div className="flex flex-wrap items-center justify-between gap-3 border-t border-border px-5 py-3 text-[13px] text-muted-foreground">
                <span className="num">
                  {formatInt(from)}–{formatInt(to)} из {formatInt(total)}
                </span>
                <div className="flex items-center gap-4">
                  <label className="flex items-center gap-2">
                    <span>Строк на странице</span>
                    <Select
                      ariaLabel="Строк на странице"
                      value={String(state.pageSize)}
                      onValueChange={(v) => update({ ...state, pageSize: Number(v), page: 1 })}
                      options={PAGE_SIZES.map((n) => ({ value: String(n), label: String(n) }))}
                      className="w-16"
                    />
                  </label>
                  <div className="flex items-center gap-1">
                    <Button
                      variant="outline"
                      size="icon-sm"
                      aria-label="Предыдущая страница"
                      disabled={state.page <= 1}
                      onClick={() => update({ ...state, page: state.page - 1 })}
                    >
                      <ChevronLeft />
                    </Button>
                    <span className="min-w-20 text-center num" aria-live="polite">
                      {formatInt(state.page)} из {formatInt(totalPages)}
                    </span>
                    <Button
                      variant="outline"
                      size="icon-sm"
                      aria-label="Следующая страница"
                      disabled={state.page >= totalPages}
                      onClick={() => update({ ...state, page: state.page + 1 })}
                    >
                      <ChevronRight />
                    </Button>
                  </div>
                </div>
              </div>
            )}
          </>
        )}
      </Card>

      <Outlet />
    </>
  )
}

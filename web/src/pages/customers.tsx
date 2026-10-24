import { keepPreviousData, useQuery } from '@tanstack/react-query'
import { createColumnHelper, flexRender, getCoreRowModel, useReactTable, type SortingState } from '@tanstack/react-table'
import { ArrowDown, ArrowUp, ChevronLeft, ChevronRight, ChevronsUpDown, Search, Users, X } from 'lucide-react'
import { useEffect, useMemo, useRef, useState, type KeyboardEvent, type MouseEvent } from 'react'
import { Link, Outlet, useNavigate, useSearchParams } from 'react-router'
import { api, queryKeys } from '@/api/endpoints'
import type { CustomerSort, CustomerSummary, RiskLevel } from '@/api/types'
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
import { formatInt, formatMoney } from '@/lib/format'
import { RISK_LABEL } from '@/lib/labels'
import {
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
import { cn } from '@/lib/utils'
import { useAuth } from '@/providers/auth'
import { useLive } from '@/providers/live'

const SORTABLE = new Set<string>(['id', 'email', 'balance', 'total_replenished', 'risk_score', 'expected_loss'])

const col = createColumnHelper<CustomerSummary>()

const columns = [
  col.accessor('id', {
    header: 'ID',
    meta: { className: 'w-[76px]' },
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
    meta: { align: 'right' },
    cell: (c) => <span className="num">{formatMoney(c.getValue())}</span>,
  }),
  col.accessor('total_replenished', {
    header: 'Пополнено всего',
    meta: { align: 'right' },
    cell: (c) => <span className="num">{formatMoney(c.getValue())}</span>,
  }),
  col.accessor('risk_score', {
    header: 'Риск',
    meta: { className: 'w-[128px] whitespace-nowrap' },
    cell: (c) => <ScoreBar score={c.getValue()} level={c.row.original.risk_level} />,
  }),
  col.accessor('risk_level', {
    header: 'Уровень',
    enableSorting: false,
    meta: { className: 'w-[124px] whitespace-nowrap' },
    cell: (c) => <RiskBadge level={c.getValue()} />,
  }),
  col.display({
    id: 'reason',
    header: 'Главная причина',
    meta: { className: 'w-full min-w-[160px] max-w-0' },
    cell: (c) => {
      const reason = c.row.original.reasons?.[0]
      const unknown = c.row.original.risk_level === 'unknown'
      if (!reason) return <span className="text-subtle-foreground">—</span>
      return (
        <span className={cn('block truncate', unknown ? 'text-muted-foreground' : '')} title={reason.title}>
          {reason.title}
        </span>
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
        className={cn(chip, selected.length === 0 ? 'border-primary bg-primary text-primary-foreground' : 'border-border bg-card text-muted-foreground hover:text-foreground')}
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

function SkeletonRows({ count }: { count: number }) {
  return (
    <>
      {Array.from({ length: count }, (_, i) => (
        <TableRow key={i}>
          {columns.map((_, j) => (
            <TableCell key={j}>
              <Skeleton className={cn('h-4', j === 1 ? 'w-40' : j === 6 ? 'w-48' : 'w-16')} />
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
  const { isAdmin } = useAuth()
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
  const total = data?.total ?? 0
  const totalPages = Math.max(1, Math.ceil(total / state.pageSize))

  // страница за пределами (например, данные уменьшились) — на последнюю
  useEffect(() => {
    if (data && state.page > totalPages && total > 0) update({ ...state, page: totalPages }, true)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [data, totalPages])

  const sorting: SortingState = [{ id: state.sort, desc: state.order === 'desc' }]
  // eslint-disable-next-line react-hooks/incompatible-library
  const table = useReactTable({
    data: items,
    columns,
    state: { sorting },
    manualSorting: true,
    manualPagination: true,
    enableSortingRemoval: false,
    getCoreRowModel: getCoreRowModel(),
    getRowId: (row) => String(row.id),
  })

  const filtered = state.levels.length > 0 || state.search.trim() !== ''
  const detailSuffix = qs ? `?${qs}` : ''

  const openRow = (id: number) => navigate(`/customers/${id}${detailSuffix}`)
  const onRowClick = (event: MouseEvent, id: number) => {
    if ((event.target as HTMLElement).closest('a,button')) return
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
      </div>

      <Card className="relative overflow-hidden">
        {customers.isFetching && !customers.isPending && (
          <div className="absolute inset-x-0 top-0 z-10 h-0.5 overflow-hidden" aria-hidden>
            <div className="h-full w-1/3 animate-[loading-bar_1s_ease-in-out_infinite] bg-ring/70" />
          </div>
        )}
        {customers.isError && !data ? (
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
                    const stamp = changed.get(id)
                    return (
                      <TableRow
                        key={`${id}-${stamp ?? 0}`}
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

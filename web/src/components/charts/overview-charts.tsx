import { useQuery } from '@tanstack/react-query'
import { ArrowRight, History } from 'lucide-react'
import { useMemo } from 'react'
import { Link, useNavigate } from 'react-router'
import {
  Bar,
  BarChart,
  CartesianGrid,
  Cell,
  Line,
  LineChart,
  Pie,
  PieChart,
  ResponsiveContainer,
  Tooltip as RTooltip,
  XAxis,
  YAxis,
} from 'recharts'
import { api, queryKeys } from '@/api/endpoints'
import type { RiskDistribution, RiskLevel, Summary } from '@/api/types'
import { AXIS_TICK, CHART_VARS, ChartCard, ChartTip, LegendItem } from '@/components/charts/chart-card'
import { EmptyState } from '@/components/states'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Skeleton } from '@/components/ui/skeleton'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table'
import { useCssVars } from '@/hooks/use-css-vars'
import {
  histogramAria,
  histogramRows,
  historyAria,
  historyDelta,
  historyRows,
  lossByReason,
  revenueByLevel,
  slicesAria,
  timeTick,
  type HistogramRow,
  type HistoryRow,
  type LevelSlice,
} from '@/lib/charts'
import { countLabel, formatCompact, formatDateTime, formatInt, formatMoney, formatMoneyCompact, formatShare } from '@/lib/format'
import { RISK_LABEL, RISK_LABEL_LONG } from '@/lib/labels'
import { reasonHref, scoreRangeHref } from '@/lib/url-state'
import { cn } from '@/lib/utils'

const money = (v: number | null | undefined) => formatMoney(v, { cents: false })

export function useLevelColors() {
  const c = useCssVars(CHART_VARS)
  const level: Record<RiskLevel, string> = {
    high: c['risk-high-bar'],
    medium: c['risk-medium-bar'],
    low: c['risk-low-bar'],
    unknown: c['risk-unknown-bar'],
  }
  return { c, level }
}

/** Индекс столбца/сектора из события Recharts: в разных версиях он приходит по-разному. */
function payloadOf<T>(data: unknown): T | undefined {
  const d = data as { payload?: T } | undefined
  return (d?.payload ?? (data as T | undefined)) || undefined
}

// --- гистограмма score ---------------------------------------------------------------------------

export function ScoreHistogram({ d, className }: { d: RiskDistribution | undefined; className?: string }) {
  const navigate = useNavigate()
  const { c, level } = useLevelColors()
  const rows = useMemo(() => histogramRows(d), [d])
  const total = rows.reduce((s, r) => s + r.total, 0)
  const open = (r: HistogramRow | undefined) => {
    if (r && r.total > 0) navigate(scoreRangeHref(r.from, r.to))
  }

  return (
    <ChartCard
      testId="chart-score-histogram"
      className={className}
      title="Распределение risk-score"
      description="Сколько клиентов в каждом диапазоне риска. Нажмите на столбец — откроются эти клиенты."
      ariaLabel={histogramAria(rows)}
      height={300}
      legend={
        <>
          {(['high', 'medium', 'low'] as const).map((l) => (
            <LegendItem key={l} color={level[l]}>
              {RISK_LABEL[l]}
            </LegendItem>
          ))}
        </>
      }
      chart={
        total === 0 ? (
          <EmptyState title="Клиентов с оценкой пока нет" className="py-6" />
        ) : (
          <ResponsiveContainer width="100%" height="100%">
            <BarChart data={rows} margin={{ top: 8, right: 4, bottom: 0, left: 0 }} barCategoryGap="12%">
              <CartesianGrid vertical={false} stroke={c['chart-grid']} />
              <XAxis
                dataKey="label"
                tick={{ ...AXIS_TICK, fill: c['chart-axis'] }}
                tickLine={false}
                axisLine={{ stroke: c.border }}
                interval="preserveStartEnd"
                minTickGap={4}
              />
              <YAxis
                allowDecimals={false}
                tickFormatter={(v: number) => formatCompact(v)}
                tick={{ ...AXIS_TICK, fill: c['chart-axis'] }}
                tickLine={false}
                axisLine={false}
                width={44}
              />
              <RTooltip
                cursor={{ fill: c['chart-axis'], fillOpacity: 0.08 }}
                isAnimationActive={false}
                content={({ active, payload }) => {
                  const r = active ? (payload?.[0]?.payload as HistogramRow | undefined) : undefined
                  if (!r) return null
                  return (
                    <ChartTip
                      title={`Score ${r.label}`}
                      lines={[
                        ['Клиентов', formatInt(r.total)],
                        ...(['high', 'medium', 'low'] as const).filter((l) => r[l] > 0).map((l) => [RISK_LABEL[l], formatInt(r[l])] as [string, string]),
                        ['Ожидаемая потеря', money(r.loss)],
                      ]}
                      hint={r.total > 0 ? 'Нажмите, чтобы открыть клиентов' : undefined}
                    />
                  )
                }}
              />
              {(['low', 'medium', 'high'] as const).map((l, i) => (
                <Bar
                  key={l}
                  dataKey={l}
                  name={RISK_LABEL[l]}
                  stackId="score"
                  fill={level[l]}
                  radius={i === 2 ? [3, 3, 0, 0] : 0}
                  isAnimationActive={false}
                  cursor="pointer"
                  onClick={(data: unknown) => open(payloadOf<HistogramRow>(data))}
                />
              ))}
            </BarChart>
          </ResponsiveContainer>
        )
      }
      table={
        <Table>
          <caption className="sr-only">Клиенты по диапазонам risk-score</caption>
          <TableHeader className="sticky top-0 bg-card">
            <TableRow>
              <TableHead>Score</TableHead>
              <TableHead className="text-right">Клиентов</TableHead>
              <TableHead className="text-right">Высокий</TableHead>
              <TableHead className="text-right">Средний</TableHead>
              <TableHead className="text-right">Низкий</TableHead>
              <TableHead className="text-right">Потеря</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {rows.map((r) => (
              <TableRow key={r.label}>
                <TableCell>
                  {r.total > 0 ? (
                    <Link to={scoreRangeHref(r.from, r.to)} className="font-medium text-link underline-offset-2 hover:underline num">
                      {r.label}
                    </Link>
                  ) : (
                    <span className="num text-muted-foreground">{r.label}</span>
                  )}
                </TableCell>
                <TableCell className="text-right num">{formatInt(r.total)}</TableCell>
                <TableCell className="text-right num">{formatInt(r.high)}</TableCell>
                <TableCell className="text-right num">{formatInt(r.medium)}</TableCell>
                <TableCell className="text-right num">{formatInt(r.low)}</TableCell>
                <TableCell className="text-right num">{money(r.loss)}</TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      }
    />
  )
}

// --- деньги по причинам --------------------------------------------------------------------------

/** Горизонтальные полосы ожидаемой потери по причинам. Каждая строка — ссылка на клиентов с причиной. */
export function LossByReason({ s, limit = 8, testId = 'chart-loss-by-reason', title = 'Деньги по причинам' }: { s: Summary | undefined; limit?: number; testId?: string; title?: string }) {
  const rows = useMemo(() => lossByReason(s?.top_reasons, limit), [s?.top_reasons, limit])
  const total = rows.reduce((sum, r) => sum + r.loss, 0)
  return (
    <Card className="flex min-w-0 flex-col" data-testid={testId}>
      <CardHeader>
        <CardTitle>{title}</CardTitle>
        <CardDescription>Сколько ожидаемой потери приходится на каждую причину. Нажмите на причину — откроются её клиенты.</CardDescription>
      </CardHeader>
      <CardContent className="flex-1">
        {rows.length === 0 ? (
          <EmptyState title="Причин риска не видно" className="py-6" />
        ) : (
          <ol className="space-y-1" aria-label={`${title}: ${rows.map((r) => `${r.title} ${money(r.loss)}`).join(', ')}`}>
            {rows.map((r) => (
              <li key={r.code}>
                <Link
                  to={reasonHref(r.code)}
                  className="group -mx-2 block rounded-md px-2 py-1.5 outline-none hover:bg-accent focus-visible:ring-2 focus-visible:ring-ring"
                  data-reason={r.code}
                  title={`${r.title}: ${countLabel(r.customers, ['клиент', 'клиента', 'клиентов'])}, главная у ${formatInt(r.primary)}`}
                >
                  <span className="flex items-baseline justify-between gap-3 text-sm">
                    <span className="min-w-0 truncate">{r.title}</span>
                    <span className="shrink-0 font-medium num">
                      {money(r.loss)}
                      <span className="ml-1.5 text-xs font-normal text-muted-foreground">{total > 0 ? formatShare(r.loss / total, 0) : ''}</span>
                    </span>
                  </span>
                  <span className="mt-1 flex items-center gap-2">
                    <span className="h-2 flex-1 overflow-hidden rounded-full bg-muted">
                      <span
                        className="block h-full rounded-full bg-chart-forecast transition-[width] duration-500"
                        style={{ width: `${Math.max(r.share * 100, r.loss > 0 ? 2 : 0)}%` }}
                      />
                    </span>
                    <ArrowRight className="size-3.5 shrink-0 text-subtle-foreground opacity-0 transition-opacity group-hover:opacity-100 group-focus-visible:opacity-100" aria-hidden />
                  </span>
                  <span className="mt-0.5 block text-xs text-muted-foreground num">{countLabel(r.customers, ['клиент', 'клиента', 'клиентов'])}</span>
                </Link>
              </li>
            ))}
          </ol>
        )}
      </CardContent>
    </Card>
  )
}

// --- донат -------------------------------------------------------------------------------------

export function DonutChart<T extends { label: string; value: number; share: number }>({
  slices,
  colors,
  center,
  centerHint,
  onSelect,
  format = money,
}: {
  slices: T[]
  colors: string[]
  center: string
  centerHint: string
  onSelect?: (slice: T) => void
  format?: (v: number) => string
}) {
  const { c } = useLevelColors()
  return (
    <div className="relative h-full w-full">
      <ResponsiveContainer width="100%" height="100%">
        <PieChart>
          <Pie
            data={slices}
            dataKey="value"
            nameKey="label"
            innerRadius="58%"
            outerRadius="88%"
            paddingAngle={slices.length > 1 ? 1.5 : 0}
            stroke={c.card}
            strokeWidth={2}
            isAnimationActive={false}
            cursor={onSelect ? 'pointer' : undefined}
            onClick={(data: unknown) => {
              const s = payloadOf<T>(data)
              if (s && onSelect) onSelect(s)
            }}
          >
            {slices.map((s, i) => (
              <Cell key={s.label} fill={colors[i % colors.length]} />
            ))}
          </Pie>
          <RTooltip
            isAnimationActive={false}
            content={({ active, payload }) => {
              const s = active ? (payload?.[0]?.payload as T | undefined) : undefined
              if (!s) return null
              return <ChartTip title={s.label} lines={[['Сумма', format(s.value)], ['Доля', formatShare(s.share, 1)]]} />
            }}
          />
        </PieChart>
      </ResponsiveContainer>
      <div className="pointer-events-none absolute inset-0 flex flex-col items-center justify-center text-center">
        <span className="text-lg font-semibold num">{center}</span>
        <span className="text-xs text-muted-foreground">{centerHint}</span>
      </div>
    </div>
  )
}

export function RevenueByLevel({ d }: { d: RiskDistribution | undefined }) {
  const navigate = useNavigate()
  const { level } = useLevelColors()
  const slices = useMemo(() => revenueByLevel(d), [d])
  const total = slices.reduce((s, x) => s + x.value, 0)
  return (
    <ChartCard
      testId="chart-revenue-by-level"
      title="Выручка по уровням риска"
      description="Какая часть месячной выручки у клиентов каждого уровня"
      ariaLabel={slicesAria('Месячная выручка по уровням риска', slices)}
      height={220}
      legend={slices.map((s) => (
        <LegendItem key={s.level} color={level[s.level]}>
          {RISK_LABEL[s.level]} <span className="num">{formatShare(s.share, 0)}</span>
        </LegendItem>
      ))}
      chart={
        slices.length === 0 ? (
          <EmptyState title="Выручки пока нет" className="py-6" />
        ) : (
          <DonutChart<LevelSlice>
            slices={slices}
            colors={slices.map((s) => level[s.level])}
            center={formatMoneyCompact(total)}
            centerHint="в месяц"
            onSelect={(s) => navigate(`/customers?level=${s.level}`)}
          />
        )
      }
      table={
        <Table>
          <caption className="sr-only">Месячная выручка по уровням риска</caption>
          <TableHeader className="sticky top-0 bg-card">
            <TableRow>
              <TableHead>Уровень</TableHead>
              <TableHead className="text-right">Клиентов</TableHead>
              <TableHead className="text-right">В месяц</TableHead>
              <TableHead className="text-right">Доля</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {slices.map((s) => (
              <TableRow key={s.level}>
                <TableCell>
                  <Link to={`/customers?level=${s.level}`} className="font-medium text-link underline-offset-2 hover:underline">
                    {RISK_LABEL_LONG[s.level]}
                  </Link>
                </TableCell>
                <TableCell className="text-right num">{formatInt(s.customers)}</TableCell>
                <TableCell className="text-right num">{money(s.value)}</TableCell>
                <TableCell className="text-right num">{formatShare(s.share, 1)}</TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      }
    />
  )
}

// --- динамика метрик -----------------------------------------------------------------------------

export function MetricsHistory({ className }: { className?: string }) {
  const history = useQuery({ queryKey: queryKeys.summaryHistory, queryFn: ({ signal }) => api.summaryHistory(signal) })
  const { c } = useLevelColors()
  const rows = useMemo(() => historyRows(history.data?.items), [history.data])
  const span = rows.length > 1 ? rows[rows.length - 1]!.t - rows[0]!.t : 0
  const delta = historyDelta(rows)

  if (history.isPending) return <Skeleton className={cn('h-[340px] rounded-xl', className)} />
  if (history.isError) return null

  return (
    <ChartCard
      testId="chart-metrics-history"
      className={className}
      title="Как менялись цифры"
      description={
        delta
          ? `С ${formatDateTime(rows[0]!.at)}: ожидаемая потеря ${delta.loss === 0 ? 'не изменилась' : `${delta.loss > 0 ? 'выросла' : 'снизилась'} на ${money(Math.abs(delta.loss))}`}, клиентов высокого риска ${delta.high === 0 ? 'столько же' : `${delta.high > 0 ? 'больше' : 'меньше'} на ${formatInt(Math.abs(delta.high))}`}`
          : 'Точка добавляется после каждого пересчёта. Включите симулятор на странице «Данные» — линия оживёт.'
      }
      ariaLabel={historyAria(rows)}
      legend={
        <>
          <LegendItem color={c['chart-forecast']} shape="line">
            Ожидаемая потеря
          </LegendItem>
          <LegendItem color={c['risk-high-bar']} shape="line">
            Клиенты высокого риска <span className="text-subtle-foreground">(правая шкала)</span>
          </LegendItem>
        </>
      }
      chart={
        rows.length < 2 ? (
          <EmptyState
            icon={<History />}
            title="История только начинается"
            description="Нужно хотя бы два пересчёта. Каждый пересчёт добавляет точку."
            className="py-6"
          />
        ) : (
          <ResponsiveContainer width="100%" height="100%">
            <LineChart data={rows} margin={{ top: 8, right: 4, bottom: 0, left: 0 }}>
              <CartesianGrid vertical={false} stroke={c['chart-grid']} />
              <XAxis
                dataKey="t"
                type="number"
                scale="time"
                domain={['dataMin', 'dataMax']}
                tickFormatter={(t: number) => timeTick(t, span)}
                tick={{ ...AXIS_TICK, fill: c['chart-axis'] }}
                tickLine={false}
                axisLine={{ stroke: c.border }}
                minTickGap={36}
              />
              <YAxis
                yAxisId="loss"
                tickFormatter={(v: number) => formatMoneyCompact(v)}
                tick={{ ...AXIS_TICK, fill: c['chart-axis'] }}
                tickLine={false}
                axisLine={false}
                width={64}
                domain={['auto', 'auto']}
              />
              <YAxis
                yAxisId="high"
                orientation="right"
                allowDecimals={false}
                tickFormatter={(v: number) => formatCompact(v)}
                tick={{ ...AXIS_TICK, fill: c['risk-high-bar'] }}
                tickLine={false}
                axisLine={false}
                width={36}
                domain={['auto', 'auto']}
              />
              <RTooltip
                isAnimationActive={false}
                content={({ active, payload }) => {
                  const r = active ? (payload?.[0]?.payload as HistoryRow | undefined) : undefined
                  if (!r) return null
                  return (
                    <ChartTip
                      title={formatDateTime(r.at)}
                      lines={[
                        ['Ожидаемая потеря', money(r.loss)],
                        ['Высокий риск', formatInt(r.high)],
                        ['Под риском', formatInt(r.atRisk)],
                        ['Клиентов', formatInt(r.customers)],
                      ]}
                      hint={`${r.full ? 'Полный пересчёт' : 'Частичный пересчёт'} · модель v${r.version}`}
                    />
                  )
                }}
              />
              <Line yAxisId="loss" type="monotone" dataKey="loss" stroke={c['chart-forecast']} strokeWidth={2} dot={false} isAnimationActive={false} />
              <Line yAxisId="high" type="stepAfter" dataKey="high" stroke={c['risk-high-bar']} strokeWidth={2} dot={false} isAnimationActive={false} />
            </LineChart>
          </ResponsiveContainer>
        )
      }
      table={
        <Table>
          <caption className="sr-only">История ключевых метрик</caption>
          <TableHeader className="sticky top-0 bg-card">
            <TableRow>
              <TableHead>Когда</TableHead>
              <TableHead className="text-right">Потеря</TableHead>
              <TableHead className="text-right">Высокий риск</TableHead>
              <TableHead className="text-right">Под риском</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {[...rows].reverse().map((r) => (
              <TableRow key={`${r.at}-${r.version}`}>
                <TableCell className="num">{formatDateTime(r.at)}</TableCell>
                <TableCell className="text-right num">{money(r.loss)}</TableCell>
                <TableCell className="text-right num">{formatInt(r.high)}</TableCell>
                <TableCell className="text-right num">{formatInt(r.atRisk)}</TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      }
    />
  )
}

/** Блок графиков обзора: гистограмма, деньги по причинам, выручка по уровням, динамика. */
export function OverviewCharts({ s }: { s: Summary }) {
  const distribution = useQuery({ queryKey: queryKeys.distribution, queryFn: ({ signal }) => api.distribution(signal) })
  return (
    <div className="space-y-4" data-testid="overview-charts">
      <div className="grid items-start gap-4 lg:grid-cols-3">
        {distribution.isPending ? (
          <Skeleton className="h-[340px] rounded-xl lg:col-span-2" />
        ) : (
          <ScoreHistogram d={distribution.data} className="lg:col-span-2" />
        )}
        <LossByReason s={s} limit={6} />
      </div>
      <div className="grid gap-4 lg:grid-cols-3">
        {distribution.isPending ? <Skeleton className="h-[300px] rounded-xl" /> : <RevenueByLevel d={distribution.data} />}
        <MetricsHistory className="lg:col-span-2" />
      </div>
    </div>
  )
}

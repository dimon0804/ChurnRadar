import { useQuery } from '@tanstack/react-query'
import { useMemo, useState, type ReactNode } from 'react'
import {
  Bar,
  BarChart,
  CartesianGrid,
  Cell,
  ComposedChart,
  Line,
  LineChart,
  ReferenceLine,
  ResponsiveContainer,
  Tooltip as RTooltip,
  XAxis,
  YAxis,
} from 'recharts'
import { api, queryKeys } from '@/api/endpoints'
import type { CustomerCharts, CustomerDetail, RiskLevel } from '@/api/types'
import { AXIS_TICK, ChartTip, LegendItem, ViewToggle } from '@/components/charts/chart-card'
import { useLevelColors } from '@/components/charts/overview-charts'
import { riskLevelOf } from '@/components/risk'
import { Skeleton } from '@/components/ui/skeleton'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table'
import {
  paymentRows,
  paymentsAria,
  runwayAria,
  runwayRows,
  waterfallAria,
  waterfallRows,
  type PaymentRow,
  type RunwayRow,
  type WaterfallRow,
} from '@/lib/charts'
import { formatDate, formatDateTime, formatDecimal, formatInt, formatMoney, formatMoneyCompact, formatPoints, formatScore } from '@/lib/format'
import { cn } from '@/lib/utils'

const money = (v: number | null | undefined) => formatMoney(v)

/** Раздел карточки с графиком: заголовок, «График / Таблица», подпись. */
function ChartSection({
  title,
  aside,
  ariaLabel,
  chart,
  table,
  note,
  height = 220,
  testId,
}: {
  title: string
  aside?: ReactNode
  ariaLabel: string
  chart: ReactNode
  table: ReactNode
  note?: ReactNode
  height?: number
  testId?: string
}) {
  const [view, setView] = useState<'chart' | 'table'>('chart')
  return (
    <section className="px-6 py-5" data-testid={testId}>
      <div className="mb-3 flex flex-wrap items-center justify-between gap-x-3 gap-y-2">
        <h3 className="text-[13px] font-semibold tracking-wide text-muted-foreground uppercase">{title}</h3>
        <div className="flex items-center gap-3">
          {aside}
          <ViewToggle value={view} onChange={setView} label={`Вид: ${title}`} />
        </div>
      </div>
      {view === 'chart' ? (
        <div className="w-full min-w-0" style={{ height }} role="img" aria-label={ariaLabel} data-chart>
          {chart}
        </div>
      ) : (
        <div className="overflow-auto rounded-lg border border-border bg-card" style={{ maxHeight: Math.max(height, 240) }}>
          {table}
        </div>
      )}
      {note && <p className="mt-2 text-xs leading-5 text-muted-foreground">{note}</p>}
    </section>
  )
}

// --- водопад -------------------------------------------------------------------------------------

export function ScoreWaterfall({ c }: { c: CustomerDetail }) {
  const { c: colors, level } = useLevelColors()
  const rows = useMemo(() => waterfallRows(c.features, c.risk_score), [c.features, c.risk_score])
  const lvl = riskLevelOf(c.risk_level)
  if (rows.length === 0) return null
  const steps = rows.filter((r) => r.kind === 'step')
  const height = Math.max(160, rows.length * 34 + 30)
  return (
    <ChartSection
      testId="chart-score-waterfall"
      title="Как складывается score"
      aside={<span className="hidden text-xs text-subtle-foreground sm:inline">вклад причин, пунктов</span>}
      ariaLabel={waterfallAria(rows)}
      height={height}
      chart={
        <ResponsiveContainer width="100%" height="100%">
          <BarChart data={rows} layout="vertical" margin={{ top: 0, right: 40, bottom: 0, left: 0 }} barCategoryGap="22%">
            <CartesianGrid horizontal={false} stroke={colors['chart-grid']} />
            <XAxis type="number" domain={[0, 100]} ticks={[0, 25, 50, 75, 100]} tick={{ ...AXIS_TICK, fill: colors['chart-axis'] }} tickLine={false} axisLine={false} />
            <YAxis
              type="category"
              dataKey="title"
              width={130}
              tick={{ ...AXIS_TICK, fill: colors['chart-axis'] }}
              tickLine={false}
              axisLine={false}
              tickFormatter={(t: string) => (t.length > 20 ? `${t.slice(0, 19)}…` : t)}
            />
            <RTooltip
              cursor={{ fill: colors['chart-axis'], fillOpacity: 0.06 }}
              isAnimationActive={false}
              content={({ active, payload }) => {
                const r = active ? (payload?.[0]?.payload as WaterfallRow | undefined) : undefined
                if (!r) return null
                return r.kind === 'total' ? (
                  <ChartTip title="Итого" lines={[['Risk-score', formatScore(r.value)]]} />
                ) : (
                  <ChartTip title={r.title} lines={[['Вклад', `+${formatPoints(r.value)}`], ['Нарастающим итогом', formatDecimal(r.end, 1)]]} />
                )
              }}
            />
            <Bar dataKey="base" stackId="w" fill="transparent" isAnimationActive={false} />
            <Bar
              dataKey="value"
              stackId="w"
              radius={[0, 3, 3, 0]}
              isAnimationActive={false}
              label={{ position: 'right', fill: colors.foreground, fontSize: 12, formatter: (v: unknown) => formatDecimal(typeof v === 'number' ? v : null, 1) }}
            >
              {rows.map((r) => (
                <Cell key={r.code} fill={r.kind === 'total' ? level[lvl] : colors['chart-forecast']} fillOpacity={r.kind === 'total' ? 1 : 0.85} />
              ))}
            </Bar>
          </BarChart>
        </ResponsiveContainer>
      }
      table={
        <Table>
          <caption className="sr-only">Вклад причин в risk-score</caption>
          <TableHeader>
            <TableRow>
              <TableHead>Причина</TableHead>
              <TableHead className="text-right">Вклад</TableHead>
              <TableHead className="text-right">Итого</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {rows.map((r) => (
              <TableRow key={r.code}>
                <TableCell className={cn(r.kind === 'total' && 'font-semibold')}>{r.title}</TableCell>
                <TableCell className="text-right num">{r.kind === 'total' ? '' : `+${formatPoints(r.value)}`}</TableCell>
                <TableCell className="text-right num">{formatDecimal(r.end, 1)}</TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      }
      note={steps.length > 0 ? 'Каждая причина добавляет свою долю риска; вместе они дают итоговый score из 100.' : undefined}
    />
  )
}

// --- на сколько хватит баланса -------------------------------------------------------------------

function Runway({ data, level }: { data: CustomerCharts; level: RiskLevel }) {
  const { c, level: levelColors } = useLevelColors()
  const rows = useMemo(() => runwayRows(data.runway), [data.runway])
  const runway = data.runway
  const outDay = runway.runs_out_at ? runway.runs_out_at.slice(0, 10) : null
  const renewalDays = rows.filter((r) => r.renewals > 0)
  const many = renewalDays.length > 40
  return (
    <ChartSection
      testId="chart-runway"
      title="На сколько хватит баланса"
      ariaLabel={runwayAria(runway, (iso) => formatDate(iso))}
      chart={
        <ResponsiveContainer width="100%" height="100%">
          <ComposedChart data={rows} margin={{ top: 16, right: 8, bottom: 0, left: 0 }}>
            <CartesianGrid vertical={false} stroke={c['chart-grid']} />
            <XAxis
              dataKey="date"
              tickFormatter={(d: string) => formatDate(d, { short: true })}
              tick={{ ...AXIS_TICK, fill: c['chart-axis'] }}
              tickLine={false}
              axisLine={{ stroke: c.border }}
              minTickGap={28}
            />
            <YAxis
              tickFormatter={(v: number) => formatMoneyCompact(v)}
              tick={{ ...AXIS_TICK, fill: c['chart-axis'] }}
              tickLine={false}
              axisLine={false}
              width={56}
              domain={[0, 'auto']}
            />
            <RTooltip
              isAnimationActive={false}
              content={({ active, payload }) => {
                const r = active ? (payload?.[0]?.payload as RunwayRow | undefined) : undefined
                if (!r) return null
                const lines: [string, string][] = [['Баланс на конец дня', money(r.balance)]]
                if (r.paid > 0) lines.push(['Списано за продления', money(r.paid)])
                if (r.unpaid > 0) lines.push(['Не хватит на продления', money(r.unpaid)])
                return <ChartTip title={formatDate(r.date)} lines={lines} hint={r.renewals > 0 ? `Продлений в этот день: ${formatInt(r.renewals)}` : undefined} />
              }}
            />
            {!many &&
              renewalDays.map((r) => (
                <ReferenceLine
                  key={r.date}
                  x={r.date}
                  stroke={r.unpaid > 0 ? levelColors.high : c['chart-axis']}
                  strokeOpacity={r.unpaid > 0 ? 0.8 : 0.35}
                  strokeDasharray="2 3"
                />
              ))}
            {outDay && (
              <ReferenceLine
                x={outDay}
                stroke={levelColors.high}
                strokeWidth={2}
                label={{ value: 'деньги кончатся', position: 'insideTopRight', fill: levelColors.high, fontSize: 12, dy: -14 }}
              />
            )}
            <Bar dataKey="paid" fill={c['chart-axis']} fillOpacity={0.35} maxBarSize={8} isAnimationActive={false} />
            <Line type="stepAfter" dataKey="balance" stroke={levelColors[level === 'unknown' ? 'low' : level]} strokeWidth={2} dot={false} isAnimationActive={false} />
          </ComposedChart>
        </ResponsiveContainer>
      }
      table={
        <Table>
          <caption className="sr-only">Баланс по дням и продления</caption>
          <TableHeader className="sticky top-0 bg-card">
            <TableRow>
              <TableHead>Дата</TableHead>
              <TableHead className="text-right">Продлений</TableHead>
              <TableHead className="text-right">Списано</TableHead>
              <TableHead className="text-right">Не хватит</TableHead>
              <TableHead className="text-right">Баланс</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {(renewalDays.length > 0 ? renewalDays : rows.slice(0, 1)).map((r) => (
              <TableRow key={r.date}>
                <TableCell className="num">{formatDate(r.date)}</TableCell>
                <TableCell className="text-right num">{formatInt(r.renewals)}</TableCell>
                <TableCell className="text-right num">{money(r.paid)}</TableCell>
                <TableCell className={cn('text-right num', r.unpaid > 0 && 'text-risk-high')}>{money(r.unpaid)}</TableCell>
                <TableCell className="text-right num">{money(r.balance)}</TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      }
      note={
        <>
          <LegendItem color={levelColors[level === 'unknown' ? 'low' : level]} shape="line">
            баланс и разблокированные бонусы
          </LegendItem>
          <span className="mx-2 text-subtle-foreground">·</span>
          {runway.renewals === 0
            ? 'Автопродлений нет: с баланса по графику ничего не спишется.'
            : runway.runs_out_at
              ? `Пунктир — дни автопродлений. С ${formatDate(runway.runs_out_at)} денег на продления не хватит.`
              : `Пунктир — дни автопродлений. Денег хватит на все продления за ${runway.days} дн.`}
        </>
      }
    />
  )
}

// --- платежи по месяцам ----------------------------------------------------------------------------

function Payments({ data }: { data: CustomerCharts }) {
  const { c, level } = useLevelColors()
  const rows = useMemo(() => paymentRows(data.payments), [data.payments])
  const hasFailed = rows.some((r) => r.failed > 0)
  return (
    <ChartSection
      testId="chart-payments"
      title="Платежи по месяцам"
      ariaLabel={paymentsAria(rows)}
      height={200}
      chart={
        <ResponsiveContainer width="100%" height="100%">
          <ComposedChart data={rows} margin={{ top: 8, right: 4, bottom: 0, left: 0 }}>
            <CartesianGrid vertical={false} stroke={c['chart-grid']} />
            <XAxis dataKey="label" tick={{ ...AXIS_TICK, fill: c['chart-axis'] }} tickLine={false} axisLine={{ stroke: c.border }} interval="preserveStartEnd" minTickGap={8} />
            <YAxis yAxisId="paid" tickFormatter={(v: number) => formatMoneyCompact(v)} tick={{ ...AXIS_TICK, fill: c['chart-axis'] }} tickLine={false} axisLine={false} width={56} />
            {hasFailed && <YAxis yAxisId="failed" orientation="right" allowDecimals={false} hide />}
            <RTooltip
              cursor={{ fill: c['chart-axis'], fillOpacity: 0.08 }}
              isAnimationActive={false}
              content={({ active, payload }) => {
                const r = active ? (payload?.[0]?.payload as PaymentRow | undefined) : undefined
                if (!r) return null
                return (
                  <ChartTip
                    title={r.label}
                    lines={[
                      ['Проведено', money(r.paid)],
                      ['Платежей', formatInt(r.payments)],
                      ['Не прошло', formatInt(r.failed)],
                    ]}
                  />
                )
              }}
            />
            <Bar yAxisId="paid" dataKey="paid" fill={c['chart-fact']} radius={[3, 3, 0, 0]} maxBarSize={28} isAnimationActive={false} />
            {hasFailed && (
              <Line yAxisId="failed" type="monotone" dataKey="failed" stroke={level.high} strokeWidth={0} dot={{ r: 3.5, fill: level.high, strokeWidth: 0 }} isAnimationActive={false} />
            )}
          </ComposedChart>
        </ResponsiveContainer>
      }
      table={
        <Table>
          <caption className="sr-only">Платежи по месяцам</caption>
          <TableHeader className="sticky top-0 bg-card">
            <TableRow>
              <TableHead>Месяц</TableHead>
              <TableHead className="text-right">Проведено</TableHead>
              <TableHead className="text-right">Платежей</TableHead>
              <TableHead className="text-right">Не прошло</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {rows.map((r) => (
              <TableRow key={r.month}>
                <TableCell>{r.label}</TableCell>
                <TableCell className="text-right num">{money(r.paid)}</TableCell>
                <TableCell className="text-right num">{formatInt(r.payments)}</TableCell>
                <TableCell className={cn('text-right num', r.failed > 0 && 'text-risk-high')}>{formatInt(r.failed)}</TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      }
      note={
        <>
          <LegendItem color={c['chart-fact']}>проведённые платежи</LegendItem>
          {hasFailed && (
            <>
              <span className="mx-2 text-subtle-foreground">·</span>
              <LegendItem color={level.high} shape="dot">
                месяцы с непрошедшими платежами
              </LegendItem>
            </>
          )}
        </>
      }
    />
  )
}

// --- score в истории -------------------------------------------------------------------------------

function ScoreHistory({ data }: { data: CustomerCharts }) {
  const { c, level } = useLevelColors()
  const rows = data.score_history.map((p) => ({ ...p, t: Date.parse(p.at) }))
  if (rows.length < 2) return null
  return (
    <ChartSection
      testId="chart-score-history"
      title="Score в истории"
      ariaLabel={`Score клиента в моменты писем и сейчас: ${rows.map((r) => `${formatDate(r.at)} — ${formatScore(r.risk_score)}`).join(', ')}. Таблица — по кнопке «Таблица».`}
      height={180}
      chart={
        <ResponsiveContainer width="100%" height="100%">
          <LineChart data={rows} margin={{ top: 8, right: 8, bottom: 0, left: 0 }}>
            <CartesianGrid vertical={false} stroke={c['chart-grid']} />
            <XAxis dataKey="at" tickFormatter={(d: string) => formatDate(d, { short: true })} tick={{ ...AXIS_TICK, fill: c['chart-axis'] }} tickLine={false} axisLine={{ stroke: c.border }} />
            <YAxis domain={[0, 100]} ticks={[0, 50, 100]} tick={{ ...AXIS_TICK, fill: c['chart-axis'] }} tickLine={false} axisLine={false} width={32} />
            <RTooltip
              isAnimationActive={false}
              content={({ active, payload }) => {
                const r = active ? (payload?.[0]?.payload as (typeof rows)[number] | undefined) : undefined
                if (!r) return null
                return <ChartTip title={formatDateTime(r.at)} lines={[['Score', formatScore(r.risk_score)]]} hint={r.source === 'now' ? 'Сейчас' : 'В момент отправки письма'} />
              }}
            />
            <Line type="monotone" dataKey="risk_score" stroke={level.high} strokeWidth={2} dot={{ r: 3.5 }} isAnimationActive={false} />
          </LineChart>
        </ResponsiveContainer>
      }
      table={
        <Table>
          <caption className="sr-only">Score клиента в истории</caption>
          <TableHeader>
            <TableRow>
              <TableHead>Когда</TableHead>
              <TableHead>Что было</TableHead>
              <TableHead className="text-right">Score</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {rows.map((r) => (
              <TableRow key={`${r.at}-${r.source}`}>
                <TableCell className="num">{formatDateTime(r.at)}</TableCell>
                <TableCell>{r.source === 'now' ? 'сейчас' : 'отправили письмо'}</TableCell>
                <TableCell className="text-right num">{formatScore(r.risk_score)}</TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      }
      note="Отдельной истории оценок по клиенту нет: точки — score в моменты отправки писем и текущий."
    />
  )
}

/** Графики карточки по данным /customers/{id}/charts: баланс, платежи, score в истории. */
export function CustomerChartsBlock({ c }: { c: CustomerDetail }) {
  const charts = useQuery({ queryKey: queryKeys.customerCharts(c.id), queryFn: ({ signal }) => api.customerCharts(c.id, signal) })
  const level = riskLevelOf(c.risk_level)
  if (charts.isPending) {
    return (
      <div className="space-y-3 px-6 py-5" aria-busy="true" aria-label="Загружаем графики клиента">
        <Skeleton className="h-4 w-48" />
        <Skeleton className="h-48 w-full" />
      </div>
    )
  }
  if (charts.isError || !charts.data) return null
  const d = charts.data
  const hasLive = (c.services ?? []).some((s) => s.status === 'active' || s.status === 'suspended' || s.status === 'prolong_wait')
  return (
    <>
      {hasLive && <Runway data={d} level={level} />}
      <Payments data={d} />
      <ScoreHistory data={d} />
    </>
  )
}

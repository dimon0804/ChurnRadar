import { useQuery } from '@tanstack/react-query'
import { ArrowRight } from 'lucide-react'
import { useMemo } from 'react'
import { Link, useNavigate } from 'react-router'
import { Bar, BarChart, CartesianGrid, Cell, ResponsiveContainer, Tooltip as RTooltip, XAxis, YAxis } from 'recharts'
import { api, queryKeys } from '@/api/endpoints'
import type { Marketing } from '@/api/types'
import { AXIS_TICK, ChartCard, ChartTip, LegendItem } from '@/components/charts/chart-card'
import { DonutChart, LossByReason, useLevelColors } from '@/components/charts/overview-charts'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table'
import { funnelSteps, segmentRows, slicesAria, termSlices, type SegmentRow, type TermSlice } from '@/lib/charts'
import { formatInt, formatMoney, formatMoneyCompact, formatShare } from '@/lib/format'
import { RISK_LABEL_LONG } from '@/lib/labels'

const money = (v: number | null | undefined) => formatMoney(v, { cents: false })

function MrrBySegment({ m }: { m: Marketing }) {
  const navigate = useNavigate()
  const { c, level } = useLevelColors()
  const rows = useMemo(() => segmentRows(m), [m])
  const loss = rows.reduce((s, r) => s + r.loss, 0)
  return (
    <ChartCard
      testId="chart-mrr-segments"
      title="MRR по сегментам риска"
      description="Сколько выручки в месяц у каждого уровня и какую часть мы, вероятно, потеряем за месяц"
      ariaLabel={`MRR по сегментам: ${rows.map((r) => `${r.label} ${money(r.mrr)}, из них под угрозой ${money(r.loss)}`).join('; ')}. Всего под угрозой ${money(loss)} в месяц. Таблица — по кнопке «Таблица».`}
      legend={
        <>
          <LegendItem color={c['chart-fact']}>останется</LegendItem>
          <LegendItem color={level.high}>ожидаемая потеря за месяц</LegendItem>
        </>
      }
      chart={
        <ResponsiveContainer width="100%" height="100%">
          <BarChart data={rows} margin={{ top: 8, right: 4, bottom: 0, left: 0 }} barCategoryGap="28%">
            <CartesianGrid vertical={false} stroke={c['chart-grid']} />
            <XAxis dataKey="label" tick={{ ...AXIS_TICK, fill: c['chart-axis'] }} tickLine={false} axisLine={{ stroke: c.border }} />
            <YAxis tickFormatter={(v: number) => formatMoneyCompact(v)} tick={{ ...AXIS_TICK, fill: c['chart-axis'] }} tickLine={false} axisLine={false} width={60} />
            <RTooltip
              cursor={{ fill: c['chart-axis'], fillOpacity: 0.08 }}
              isAnimationActive={false}
              content={({ active, payload }) => {
                const r = active ? (payload?.[0]?.payload as SegmentRow | undefined) : undefined
                if (!r) return null
                return (
                  <ChartTip
                    title={RISK_LABEL_LONG[r.level]}
                    lines={[
                      ['MRR', money(r.mrr)],
                      ['Останется', money(r.kept)],
                      ['Ожидаемая потеря', money(r.loss)],
                      ['Клиентов', formatInt(r.customers)],
                    ]}
                    hint="Нажмите, чтобы открыть клиентов"
                  />
                )
              }}
            />
            <Bar dataKey="kept" stackId="mrr" fill={c['chart-fact']} isAnimationActive={false} cursor="pointer" onClick={(d: unknown) => {
              const r = (d as { payload?: SegmentRow })?.payload
              if (r) navigate(`/customers?level=${r.level}`)
            }} />
            <Bar dataKey="loss" stackId="mrr" fill={level.high} radius={[3, 3, 0, 0]} isAnimationActive={false} cursor="pointer" onClick={(d: unknown) => {
              const r = (d as { payload?: SegmentRow })?.payload
              if (r) navigate(`/customers?level=${r.level}`)
            }} />
          </BarChart>
        </ResponsiveContainer>
      }
      table={
        <Table>
          <caption className="sr-only">MRR по сегментам риска</caption>
          <TableHeader className="sticky top-0 bg-card">
            <TableRow>
              <TableHead>Сегмент</TableHead>
              <TableHead className="text-right">MRR</TableHead>
              <TableHead className="text-right">Останется</TableHead>
              <TableHead className="text-right">Потеря в месяц</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {rows.map((r) => (
              <TableRow key={r.level}>
                <TableCell>
                  <Link to={`/customers?level=${r.level}`} className="font-medium text-link underline-offset-2 hover:underline">
                    {RISK_LABEL_LONG[r.level]}
                  </Link>
                </TableCell>
                <TableCell className="text-right num">{money(r.mrr)}</TableCell>
                <TableCell className="text-right num">{money(r.kept)}</TableCell>
                <TableCell className="text-right num">{money(r.loss)}</TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      }
    />
  )
}

function LtvBySegment({ m }: { m: Marketing }) {
  const { c, level } = useLevelColors()
  const rows = useMemo(() => segmentRows(m).filter((r) => r.level !== 'unknown'), [m])
  return (
    <ChartCard
      testId="chart-ltv-segments"
      title="LTV по сегментам"
      description="Сколько в среднем ещё принесёт один платящий клиент каждого уровня, с учётом маржи"
      ariaLabel={`Средний LTV: ${rows.map((r) => `${r.label} ${money(r.ltvAvg)}`).join(', ')}. Таблица — по кнопке «Таблица».`}
      chart={
        <ResponsiveContainer width="100%" height="100%">
          <BarChart data={rows} margin={{ top: 20, right: 4, bottom: 0, left: 0 }} barCategoryGap="30%">
            <CartesianGrid vertical={false} stroke={c['chart-grid']} />
            <XAxis dataKey="label" tick={{ ...AXIS_TICK, fill: c['chart-axis'] }} tickLine={false} axisLine={{ stroke: c.border }} />
            <YAxis tickFormatter={(v: number) => formatMoneyCompact(v)} tick={{ ...AXIS_TICK, fill: c['chart-axis'] }} tickLine={false} axisLine={false} width={60} />
            <RTooltip
              cursor={{ fill: c['chart-axis'], fillOpacity: 0.08 }}
              isAnimationActive={false}
              content={({ active, payload }) => {
                const r = active ? (payload?.[0]?.payload as SegmentRow | undefined) : undefined
                if (!r) return null
                return <ChartTip title={RISK_LABEL_LONG[r.level]} lines={[['LTV на клиента', money(r.ltvAvg)], ['LTV сегмента', money(r.ltv)]]} />
              }}
            />
            <Bar
              dataKey="ltvAvg"
              radius={[4, 4, 0, 0]}
              isAnimationActive={false}
              label={{ position: 'top', fill: c.foreground, fontSize: 12, formatter: (v: unknown) => formatMoneyCompact(typeof v === 'number' ? v : null) }}
            >
              {rows.map((r) => (
                <Cell key={r.level} fill={level[r.level]} />
              ))}
            </Bar>
          </BarChart>
        </ResponsiveContainer>
      }
      table={
        <Table>
          <caption className="sr-only">LTV по сегментам</caption>
          <TableHeader className="sticky top-0 bg-card">
            <TableRow>
              <TableHead>Сегмент</TableHead>
              <TableHead className="text-right">LTV на клиента</TableHead>
              <TableHead className="text-right">LTV сегмента</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {rows.map((r) => (
              <TableRow key={r.level}>
                <TableCell>{RISK_LABEL_LONG[r.level]}</TableCell>
                <TableCell className="text-right num">{money(r.ltvAvg)}</TableCell>
                <TableCell className="text-right num">{money(r.ltv)}</TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      }
    />
  )
}

function TermsDonut({ m }: { m: Marketing }) {
  const { c, level } = useLevelColors()
  const slices = useMemo(() => termSlices(m), [m])
  const colors = [c['chart-fact'], c['chart-forecast'], c['chart-risk'], c['chart-rule-b'], level.medium, c['muted-foreground']]
  const total = slices.reduce((s, x) => s + x.value, 0)
  return (
    <ChartCard
      testId="chart-payment-terms"
      title="Выручка по периодам оплаты"
      description="Короткие периоды — риск: от почасовой услуги отказаться проще, чем от годовой"
      ariaLabel={slicesAria('MRR по периодам оплаты', slices)}
      height={220}
      legend={slices.map((s, i) => (
        <LegendItem key={s.term} color={colors[i % colors.length]!}>
          {s.label} <span className="num">{formatShare(s.share, 0)}</span>
        </LegendItem>
      ))}
      chart={<DonutChart<TermSlice> slices={slices} colors={colors} center={formatMoneyCompact(total)} centerHint="MRR" />}
      table={
        <Table>
          <caption className="sr-only">MRR по периодам оплаты</caption>
          <TableHeader className="sticky top-0 bg-card">
            <TableRow>
              <TableHead>Период</TableHead>
              <TableHead className="text-right">Услуг</TableHead>
              <TableHead className="text-right">MRR</TableHead>
              <TableHead className="text-right">Доля</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {slices.map((s) => (
              <TableRow key={s.term}>
                <TableCell>{s.label}</TableCell>
                <TableCell className="text-right num">{formatInt(s.services)}</TableCell>
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

/** Воронка: каждая ступень — полоса шириной в долю от всех клиентов, со ссылкой на список. */
function Funnel({ m }: { m: Marketing }) {
  const { c, level } = useLevelColors()
  const steps = useMemo(() => funnelSteps(m), [m])
  const colors = [c['chart-fact'], c['chart-rule-b'], level.medium, level.high]
  return (
    <Card className="flex min-w-0 flex-col" data-testid="chart-funnel">
      <CardHeader>
        <CardTitle>Воронка риска</CardTitle>
        <CardDescription>От всех клиентов к тем, кого вот-вот потеряем</CardDescription>
      </CardHeader>
      <CardContent className="flex-1">
        <ol className="space-y-3" aria-label={`Воронка: ${steps.map((s) => `${s.label} ${formatInt(s.value)}`).join(', ')}`}>
          {steps.map((s, i) => {
            const body = (
              <>
                <span className="flex items-baseline justify-between gap-3 text-sm">
                  <span className="min-w-0">
                    <span className="font-medium">{s.label}</span>
                    <span className="ml-2 text-xs text-muted-foreground">{s.hint}</span>
                  </span>
                  <span className="shrink-0 font-semibold num">
                    {formatInt(s.value)}
                    {s.fromPrev !== null && <span className="ml-1.5 text-xs font-normal text-muted-foreground">{formatShare(s.fromPrev, 0)} от прошлой</span>}
                  </span>
                </span>
                <span className="mt-1.5 flex h-6 w-full items-center justify-center overflow-hidden rounded-md bg-muted">
                  <span className="block h-full rounded-md transition-[width] duration-500" style={{ width: `${Math.max(s.share * 100, s.value > 0 ? 2 : 0)}%`, background: colors[i] }} />
                </span>
              </>
            )
            return (
              <li key={s.key}>
                {s.href ? (
                  <Link to={s.href} className="group -mx-2 block rounded-md px-2 py-1 outline-none hover:bg-accent focus-visible:ring-2 focus-visible:ring-ring">
                    {body}
                    <span className="mt-0.5 inline-flex items-center gap-1 text-xs text-link opacity-0 transition-opacity group-hover:opacity-100 group-focus-visible:opacity-100">
                      Открыть клиентов <ArrowRight className="size-3" aria-hidden />
                    </span>
                  </Link>
                ) : (
                  <div className="px-0 py-1">{body}</div>
                )}
              </li>
            )
          })}
        </ol>
      </CardContent>
    </Card>
  )
}

/** Графики маркетинга: сегменты, LTV, периоды оплаты, воронка и деньги по причинам. */
export function MarketingCharts({ m }: { m: Marketing }) {
  const summary = useQuery({ queryKey: queryKeys.summary, queryFn: ({ signal }) => api.summary(signal) })
  return (
    <div className="space-y-4" data-testid="marketing-charts">
      <div className="grid gap-4 lg:grid-cols-2">
        <MrrBySegment m={m} />
        <LtvBySegment m={m} />
      </div>
      <div className="grid gap-4 lg:grid-cols-3">
        <Funnel m={m} />
        <TermsDonut m={m} />
        <LossByReason s={summary.data} limit={6} title="Где теряем деньги" testId="mkt-loss-by-reason" />
      </div>
    </div>
  )
}

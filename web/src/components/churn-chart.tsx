import { useMemo, useState } from 'react'
import { Link } from 'react-router'
import {
  Bar,
  CartesianGrid,
  ComposedChart,
  Line,
  ReferenceArea,
  ReferenceLine,
  ResponsiveContainer,
  Tooltip as ChartTooltip,
  XAxis,
  YAxis,
} from 'recharts'
import { ArrowRight, LineChart as LineIcon, Table2 } from 'lucide-react'
import type { TimelinePoint } from '@/api/types'
import { EmptyState } from '@/components/states'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table'
import { useCssVars } from '@/hooks/use-css-vars'
import {
  buildRows,
  chartAriaLabel,
  churnStats,
  countAxisWidth,
  factGapNote,
  forecastBar,
  formatForecast,
  riskAxisWidth,
  type ChurnRow,
  type DeletedServices,
  type FactGapNote,
} from '@/lib/churn-chart'
import { formatCompact, formatDate, formatInt, formatMoney } from '@/lib/format'
import { cn } from '@/lib/utils'
import { useAuth } from '@/providers/auth'

const HATCH_ID = 'churn-forecast-hatch'
const HATCH_SMALL_ID = 'churn-forecast-hatch-sm'
const MARGIN = { top: 22, right: 4, bottom: 0, left: 0 }
const HEIGHT = 280

type SwatchKind = 'fact' | 'estimated' | 'forecast' | 'risk' | 'snapshot'

function Swatch({ kind }: { kind: SwatchKind }) {
  switch (kind) {
    case 'fact':
      return <span className="size-2.5 shrink-0 rounded-[3px] bg-chart-fact" aria-hidden />
    case 'estimated':
      return (
        <span className="relative size-2.5 shrink-0 overflow-hidden rounded-[3px] border border-chart-fact" aria-hidden>
          <span className="absolute inset-0 bg-chart-fact opacity-30" />
        </span>
      )
    case 'forecast':
      return (
        <svg width="10" height="10" viewBox="0 0 10 10" aria-hidden className="shrink-0">
          <rect x="0.5" y="0.5" width="9" height="9" rx="2" fill={`url(#${HATCH_SMALL_ID})`} className="stroke-chart-forecast" />
        </svg>
      )
    case 'risk':
      return (
        <svg width="18" height="10" viewBox="0 0 18 10" aria-hidden className="shrink-0">
          <polyline points="1,3 9,3 9,7 17,7" fill="none" strokeWidth="2" strokeLinejoin="round" className="stroke-chart-risk" />
        </svg>
      )
    case 'snapshot':
      return <span className="h-3 w-px shrink-0 bg-muted-foreground/70" aria-hidden />
  }
}

/** Штриховка прогнозных столбцов. Отдельный svg: url(#id) работает на всю страницу. */
function HatchDefs({ color }: { color: string }) {
  return (
    <svg width="0" height="0" className="absolute" aria-hidden focusable="false">
      <defs>
        <pattern id={HATCH_ID} width="5" height="5" patternUnits="userSpaceOnUse" patternTransform="rotate(45)">
          <rect width="5" height="5" fill={color} fillOpacity="0.14" />
          <line x1="0" y1="0" x2="0" y2="5" stroke={color} strokeWidth="2.6" />
        </pattern>
        <pattern id={HATCH_SMALL_ID} width="3.5" height="3.5" patternUnits="userSpaceOnUse" patternTransform="rotate(45)">
          <line x1="0" y1="0" x2="0" y2="3.5" stroke={color} strokeWidth="1.8" />
        </pattern>
      </defs>
    </svg>
  )
}

function TipLine({ swatch, label, value, muted }: { swatch: SwatchKind; label: string; value: string; muted?: boolean }) {
  return (
    <p className={cn('flex items-center gap-2', muted && 'opacity-55')}>
      <Swatch kind={swatch} />
      <span className="text-muted-foreground">{label}</span>
      <span className="ml-auto pl-3 font-semibold num">{value}</span>
    </p>
  )
}

function makeTip(snapshotDate: string | null) {
  return function ChartTip({ active, payload }: { active?: boolean; payload?: ReadonlyArray<{ payload?: unknown }> }) {
    if (!active || !payload?.length) return null
    const row = payload[0]?.payload as ChurnRow | undefined
    if (!row) return null
    const isFact = row.kind === 'fact'
    const badge = row.date === snapshotDate ? 'снимок данных' : isFact ? 'факт' : 'прогноз'
    return (
      <div className="min-w-56 space-y-1 rounded-lg border border-border bg-popover px-3 py-2.5 text-[13px] shadow-pop">
        <p className="mb-1.5 flex items-center justify-between gap-3 font-medium">
          {formatDate(row.date)}
          <span className="rounded bg-muted px-1.5 py-0.5 text-[11px] font-medium text-muted-foreground">{badge}</span>
        </p>
        <TipLine swatch="fact" label="Ушло услуг" value={isFact ? formatInt(row.fact) : '—'} muted={!isFact} />
        {isFact && (row.factEstimated ?? 0) > 0 && (
          <p className="pl-[18px] text-xs text-muted-foreground num">
            из них {formatInt(row.factEstimated)} — дата удаления оценочная
          </p>
        )}
        <TipLine swatch="forecast" label="Ожидаем непродлений" value={isFact ? '—' : formatForecast(row.forecast)} muted={isFact} />
        <TipLine
          swatch="risk"
          label="Под риском"
          value={row.atRisk === null ? '—' : formatInt(row.atRisk)}
          muted={row.atRisk === null}
        />
        {isFact && row.atRisk === null && (
          <p className="pl-[18px] text-xs text-muted-foreground">в прошлом не замерялось</p>
        )}
        {!isFact && row.loss !== null && (
          <p className="pt-1 text-xs text-muted-foreground num">Ожидаемая потеря за день: {formatMoney(row.loss, { cents: false })}</p>
        )}
      </div>
    )
  }
}

function FactNote({ note, isAdmin, className }: { note: FactGapNote; isAdmin: boolean; className?: string }) {
  return (
    <div className={cn('rounded-lg border border-dashed border-border px-3.5 py-3 text-[13px] leading-snug shadow-sm', className)}>
      <p className="font-medium">{note.title}</p>
      <p className="mt-1 text-muted-foreground">{note.text}</p>
      {isAdmin ? (
        <Link
          to="/data"
          className="mt-2 inline-flex items-center gap-1 font-medium text-link underline-offset-2 hover:underline focus-visible:underline"
        >
          Включить симулятор и смотреть живой поток
          <ArrowRight className="size-3.5" aria-hidden />
        </Link>
      ) : (
        <p className="mt-1 text-muted-foreground">Живой поток включает администратор на странице «Данные».</p>
      )}
    </div>
  )
}

/** Точка «под риском» только на дне снимка: там линия начинается. */
function SnapshotDot(props: { cx?: number; cy?: number; payload?: ChurnRow; stroke?: string; snapshotDate: string | null }) {
  const { cx, cy, payload, stroke, snapshotDate } = props
  if (cx === undefined || cy === undefined || payload?.date !== snapshotDate) return <g />
  return <circle cx={cx} cy={cy} r={3.5} fill={stroke} stroke="var(--card)" strokeWidth={2} />
}

export function ChurnChart({
  timeline,
  horizonDays,
  deletedServices,
}: {
  timeline: TimelinePoint[]
  horizonDays?: number
  deletedServices?: DeletedServices | null
}) {
  const [view, setView] = useState<'chart' | 'table'>('chart')
  const { isAdmin } = useAuth()
  const colors = useCssVars([
    'chart-fact',
    'chart-forecast',
    'chart-risk',
    'chart-grid',
    'chart-axis',
    'chart-forecast-zone',
    'muted-foreground',
    'border',
  ])
  const rows = useMemo(() => buildRows(timeline), [timeline])
  const stats = useMemo(() => churnStats(rows), [rows])
  const leftWidth = useMemo(() => countAxisWidth(rows), [rows])
  const rightWidth = useMemo(() => riskAxisWidth(rows), [rows])
  const note = factGapNote(stats, deletedServices)
  const ChartTip = useMemo(() => makeTip(stats.snapshotDate), [stats.snapshotDate])
  const hasRisk = stats.atRiskNow !== null

  if (rows.length === 0 || (stats.factDays === 0 && !stats.hasForecast)) {
    return (
      <EmptyState
        icon={<LineIcon />}
        title="Пока нечего показать"
        description="График появится после первого расчёта: факт удалений и прогноз непродлений."
      />
    )
  }

  // Зона факта занимает первые factDays полос категориальной оси.
  const factShare = stats.factDays / rows.length
  const plotInset = MARGIN.left + leftWidth + MARGIN.right + (hasRisk ? rightWidth : 0)

  return (
    <div>
      <HatchDefs color={colors['chart-forecast']} />
      <div className="mb-3 flex flex-wrap items-center gap-x-4 gap-y-2 text-[13px]">
        <ul className="contents" aria-label="Легенда">
          <li className="flex items-center gap-2 text-muted-foreground">
            <Swatch kind="fact" />
            Факт: ушло услуг
          </li>
          {stats.hasEstimated && (
            <li className="flex items-center gap-2 text-muted-foreground">
              <Swatch kind="estimated" />
              дата оценочная
            </li>
          )}
          <li className="flex items-center gap-2 text-muted-foreground">
            <Swatch kind="forecast" />
            Прогноз: ожидаемые непродления
          </li>
          {hasRisk && (
            <li className="flex items-center gap-2 text-muted-foreground">
              <Swatch kind="risk" />
              Под риском <span className="text-subtle-foreground">(правая шкала)</span>
            </li>
          )}
        </ul>
        <div className="ml-auto inline-flex rounded-[11px] border border-border bg-surface-2 p-[3px]" role="group" aria-label="Вид">
          {(
            [
              ['chart', 'График', LineIcon],
              ['table', 'Таблица', Table2],
            ] as const
          ).map(([key, label, Icon]) => (
            <button
              key={key}
              type="button"
              onClick={() => setView(key)}
              aria-pressed={view === key}
              className={cn(
                'inline-flex h-6 items-center gap-1.5 rounded-[8px] px-2 text-xs font-medium transition-colors',
                view === key ? 'bg-card text-foreground shadow-sm ring-1 ring-brand/45' : 'text-muted-foreground hover:text-foreground',
              )}
            >
              <Icon className="size-3.5" />
              {label}
            </button>
          ))}
        </div>
      </div>

      {view === 'chart' ? (
        <div className="relative w-full" style={{ height: HEIGHT }}>
          <div className="h-full w-full" role="img" aria-label={chartAriaLabel(stats, horizonDays)}>
            <ResponsiveContainer width="100%" height="100%">
              <ComposedChart data={rows} margin={MARGIN} barCategoryGap="22%">
                <CartesianGrid vertical={false} stroke={colors['chart-grid']} yAxisId="count" />
                {stats.firstDate && stats.snapshotDate && (
                  <ReferenceArea
                    yAxisId="count"
                    x1={stats.firstDate}
                    x2={stats.snapshotDate}
                    fill="none"
                    stroke="none"
                    ifOverflow="extendDomain"
                    label={{
                      value: 'факт',
                      position: 'insideTopLeft',
                      fill: colors['chart-fact'],
                      fontSize: 12,
                      fontWeight: 600,
                      dy: -18,
                    }}
                  />
                )}
                {stats.firstForecast && stats.lastDate && (
                  <ReferenceArea
                    yAxisId="count"
                    x1={stats.firstForecast}
                    x2={stats.lastDate}
                    fill={colors['chart-forecast-zone']}
                    fillOpacity={1}
                    stroke="none"
                    ifOverflow="extendDomain"
                    label={{
                      value: 'прогноз',
                      position: 'insideTopRight',
                      fill: colors['chart-forecast'],
                      fontSize: 12,
                      fontWeight: 600,
                      dy: -18,
                    }}
                  />
                )}
                <XAxis
                  dataKey="date"
                  tickFormatter={(d: string) => formatDate(d, { short: true })}
                  tick={{ fill: colors['chart-axis'], fontSize: 12 }}
                  tickLine={false}
                  axisLine={{ stroke: colors['border'] }}
                  minTickGap={28}
                  interval="preserveStartEnd"
                />
                <YAxis
                  yAxisId="count"
                  allowDecimals={false}
                  tick={{ fill: colors['chart-axis'], fontSize: 12 }}
                  tickFormatter={(v: number) => formatCompact(v)}
                  tickLine={false}
                  axisLine={false}
                  width={leftWidth}
                />
                {hasRisk && (
                  <YAxis
                    yAxisId="risk"
                    orientation="right"
                    allowDecimals={false}
                    domain={[0, 'auto']}
                    tick={{ fill: colors['chart-risk'], fontSize: 12 }}
                    tickFormatter={(v: number) => formatCompact(v)}
                    tickLine={false}
                    axisLine={false}
                    width={rightWidth}
                  />
                )}
                {stats.snapshotDate && (
                  <ReferenceLine
                    yAxisId="count"
                    x={stats.snapshotDate}
                    stroke={colors['muted-foreground']}
                    strokeOpacity={0.6}
                    strokeDasharray="2 3"
                    label={{
                      value: 'снимок',
                      position: 'insideTopRight',
                      fill: colors['muted-foreground'],
                      fontSize: 12,
                      dy: -18,
                      dx: -4,
                    }}
                  />
                )}
                <ChartTooltip
                  content={ChartTip}
                  cursor={{ fill: colors['chart-axis'], fillOpacity: 0.08 }}
                  isAnimationActive={false}
                />
                <Bar
                  yAxisId="count"
                  dataKey="factExact"
                  stackId="day"
                  name="Ушло услуг"
                  fill={colors['chart-fact']}
                  maxBarSize={12}
                  isAnimationActive={false}
                />
                <Bar
                  yAxisId="count"
                  dataKey={(r: ChurnRow) => (r.factEstimated ? r.factEstimated : null)}
                  stackId="day"
                  name="Ушло услуг, дата оценочная"
                  fill={colors['chart-fact']}
                  fillOpacity={0.3}
                  stroke={colors['chart-fact']}
                  strokeWidth={1}
                  maxBarSize={12}
                  isAnimationActive={false}
                />
                <Bar
                  yAxisId="count"
                  dataKey={forecastBar}
                  stackId="day"
                  name="Ожидаем непродлений"
                  fill={`url(#${HATCH_ID})`}
                  radius={[3, 3, 0, 0]}
                  maxBarSize={12}
                  isAnimationActive={false}
                />
                {hasRisk && (
                  <Line
                    yAxisId="risk"
                    type="stepAfter"
                    dataKey="atRisk"
                    name="Под риском"
                    stroke={colors['chart-risk']}
                    strokeWidth={2}
                    dot={(p: object) => <SnapshotDot key={(p as { index?: number }).index} {...p} snapshotDate={stats.snapshotDate} />}
                    activeDot={{ r: 4, strokeWidth: 2, stroke: 'var(--card)' }}
                    connectNulls={false}
                    isAnimationActive={false}
                  />
                )}
              </ComposedChart>
            </ResponsiveContainer>
          </div>
          {note && (
            // на широком экране подпись лежит прямо в пустой зоне факта, на узком — под графиком
            <div
              className="pointer-events-none absolute hidden items-center justify-center p-3 md:flex"
              style={{
                top: MARGIN.top,
                bottom: 30,
                left: MARGIN.left + leftWidth,
                width: `calc((100% - ${plotInset}px) * ${factShare})`,
              }}
            >
              <FactNote note={note} isAdmin={isAdmin} className="pointer-events-auto max-w-80 bg-card/90 backdrop-blur-[2px]" />
            </div>
          )}
        </div>
      ) : (
        <div className="overflow-y-auto rounded-lg border border-border" style={{ maxHeight: HEIGHT }}>
          <Table>
            <caption className="sr-only">Отток услуг по дням: факт, прогноз и услуги под риском</caption>
            <TableHeader className="sticky top-0 bg-card">
              <TableRow>
                <TableHead>Дата</TableHead>
                <TableHead>Тип</TableHead>
                <TableHead className="text-right">Ушло</TableHead>
                <TableHead className="text-right">Ожидаем</TableHead>
                <TableHead className="text-right">Под риском</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {rows.map((r) => (
                <TableRow key={r.date}>
                  <TableCell className="num">{formatDate(r.date)}</TableCell>
                  <TableCell className="text-muted-foreground">
                    {r.date === stats.snapshotDate ? 'снимок' : r.kind === 'fact' ? 'факт' : 'прогноз'}
                  </TableCell>
                  <TableCell className="text-right num">
                    {r.kind === 'fact' ? formatInt(r.fact) : '—'}
                    {(r.factEstimated ?? 0) > 0 && (
                      <span className="ml-1 text-xs text-muted-foreground">(оценка: {formatInt(r.factEstimated)})</span>
                    )}
                  </TableCell>
                  <TableCell className="text-right num">{r.kind === 'forecast' ? formatForecast(r.forecast) : '—'}</TableCell>
                  <TableCell className="text-right num">{formatInt(r.atRisk)}</TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </div>
      )}
      {view === 'chart' && note && <FactNote note={note} isAdmin={isAdmin} className="mt-3 md:hidden" />}
    </div>
  )
}

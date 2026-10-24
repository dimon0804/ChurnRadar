import { useMemo, useState } from 'react'
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
import { LineChart as LineIcon, Table2 } from 'lucide-react'
import type { TimelinePoint } from '@/api/types'
import { EmptyState } from '@/components/states'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table'
import { useCssVars } from '@/hooks/use-css-vars'
import { formatCompact, formatDate, formatDecimal, formatInt, formatMoney, plural } from '@/lib/format'
import { cn } from '@/lib/utils'

interface Row {
  date: string
  kind: 'fact' | 'forecast'
  fact: number | null
  forecast: number | null
  atRisk: number | null
  loss: number | null
}

function localToday(): string {
  const d = new Date()
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`
}

function num(v: unknown): number | null {
  return typeof v === 'number' && Number.isFinite(v) ? v : null
}

export function buildRows(timeline: TimelinePoint[]): Row[] {
  const sorted = [...timeline].filter((p) => typeof p.date === 'string').sort((a, b) => a.date.localeCompare(b.date))
  const rows: Row[] = sorted.map((p) => ({
    date: p.date,
    kind: p.kind === 'forecast' ? 'forecast' : 'fact',
    fact: p.kind === 'fact' ? num(p.deletions) : null,
    forecast: p.kind === 'forecast' ? num(p.expected_nonrenewals) : null,
    atRisk: num(p.at_risk_services),
    loss: num(p.expected_loss),
  }))
  return rows
}

function ChartTip({ active, payload }: { active?: boolean; payload?: ReadonlyArray<{ payload?: unknown }> }) {
  if (!active || !payload?.length) return null
  const row = payload[0]?.payload as Row | undefined
  if (!row) return null
  const services = ['услуга', 'услуги', 'услуг'] as const
  return (
    <div className="min-w-48 rounded-lg border border-border bg-popover px-3 py-2.5 text-[13px] shadow-pop">
      <p className="mb-1.5 flex items-center justify-between gap-3 font-medium">
        {formatDate(row.date)}
        {row.kind === 'forecast' && (
          <span className="rounded bg-muted px-1.5 py-0.5 text-[11px] font-medium text-muted-foreground">прогноз</span>
        )}
      </p>
      {row.kind === 'fact' ? (
        <TipLine swatch="fact" label="Ушло услуг" value={row.fact === null ? '—' : formatInt(row.fact)} />
      ) : (
        <TipLine
          swatch="forecast"
          label="Ожидаем непродлений"
          value={row.forecast === null ? '—' : `≈${formatDecimal(row.forecast, 1)}`}
        />
      )}
      {row.atRisk !== null && (
        <p className="mt-1 text-muted-foreground num">
          Под риском: {formatInt(row.atRisk)} {plural(row.atRisk, services)}
        </p>
      )}
      {row.kind === 'forecast' && row.loss !== null && (
        <p className="mt-0.5 text-muted-foreground num">Ожидаемая потеря: {formatMoney(row.loss, { cents: false })}</p>
      )}
    </div>
  )
}

function TipLine({ swatch, label, value }: { swatch: 'fact' | 'forecast'; label: string; value: string }) {
  return (
    <p className="flex items-center gap-2">
      <Swatch kind={swatch} />
      <span className="text-muted-foreground">{label}</span>
      <span className="ml-auto font-semibold num">{value}</span>
    </p>
  )
}

function Swatch({ kind }: { kind: 'fact' | 'forecast' }) {
  if (kind === 'fact') return <span className="size-2.5 shrink-0 rounded-[3px] bg-chart-fact" aria-hidden />
  return (
    <svg width="18" height="8" viewBox="0 0 18 8" aria-hidden className="shrink-0">
      <line
        x1="1"
        y1="4"
        x2="17"
        y2="4"
        strokeWidth="2.5"
        strokeLinecap="round"
        strokeDasharray="3 3"
        className="stroke-chart-forecast"
      />
    </svg>
  )
}

/** Ширина оси Y под самую длинную подпись, чтобы «1 600» не превращалось в «|600». */
export function yAxisWidth(rows: readonly Row[]): number {
  const max = Math.max(0, ...rows.map((r) => Math.max(r.fact ?? 0, r.forecast ?? 0)))
  // recharts округляет верх шкалы вверх, берём с запасом
  const label = formatCompact(Math.ceil(max * 1.25))
  return Math.max(32, Math.round(label.length * 7.5 + 12))
}

export function ChurnChart({ timeline, horizonDays }: { timeline: TimelinePoint[]; horizonDays?: number }) {
  const [view, setView] = useState<'chart' | 'table'>('chart')
  const colors = useCssVars(['chart-fact', 'chart-forecast', 'chart-grid', 'chart-axis', 'chart-forecast-zone', 'muted-foreground', 'border'])
  const rows = useMemo(() => buildRows(timeline), [timeline])
  const axisWidth = useMemo(() => yAxisWidth(rows), [rows])

  const firstForecast = rows.find((r) => r.kind === 'forecast')?.date
  const lastDate = rows.at(-1)?.date
  // «сегодня» для модели — момент снимка данных: граница между фактом и прогнозом
  const todayMark = firstForecast ?? (rows.some((r) => r.date === localToday()) ? localToday() : null)
  const hasFact = rows.some((r) => r.fact !== null)
  const hasForecast = rows.some((r) => r.kind === 'forecast' && r.forecast !== null)

  if (rows.length === 0 || (!hasFact && !hasForecast)) {
    return (
      <EmptyState
        icon={<LineIcon />}
        title="Пока нечего показать"
        description="График появится, когда в данных будут удалённые услуги или прогноз непродлений."
      />
    )
  }

  return (
    <div>
      <div className="mb-3 flex flex-wrap items-center gap-x-5 gap-y-2 text-[13px]">
        <span className="flex items-center gap-2 text-muted-foreground">
          <span className="size-2.5 rounded-[3px] bg-chart-fact" aria-hidden />
          Факт: ушло услуг за день
        </span>
        <span className="flex items-center gap-2 text-muted-foreground">
          <Swatch kind="forecast" />
          Прогноз: ожидаемые непродления
        </span>
        <div className="ml-auto inline-flex rounded-md border border-border p-0.5" role="group" aria-label="Вид">
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
                'inline-flex h-6 items-center gap-1.5 rounded px-2 text-xs font-medium transition-colors',
                view === key ? 'bg-accent text-foreground' : 'text-muted-foreground hover:text-foreground',
              )}
            >
              <Icon className="size-3.5" />
              {label}
            </button>
          ))}
        </div>
      </div>

      {view === 'chart' ? (
        <div
          className="h-[280px] w-full"
          role="img"
          aria-label={`График оттока: факт по дням и прогноз на ${horizonDays ?? ''} дней вперёд. Таблица доступна по кнопке «Таблица».`}
        >
          <ResponsiveContainer width="100%" height="100%">
            <ComposedChart data={rows} margin={{ top: 22, right: 12, bottom: 0, left: 0 }}>
              <CartesianGrid vertical={false} stroke={colors['chart-grid']} />
              {firstForecast && lastDate && (
                <ReferenceArea
                  x1={firstForecast}
                  x2={lastDate}
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
                allowDecimals={false}
                tick={{ fill: colors['chart-axis'], fontSize: 12 }}
                tickFormatter={(v: number) => formatCompact(v)}
                tickLine={false}
                axisLine={false}
                width={axisWidth}
              />
              {todayMark && (
                <ReferenceLine
                  x={todayMark}
                  stroke={colors['muted-foreground']}
                  strokeOpacity={0.55}
                  label={{
                    value: 'сегодня',
                    position: 'insideTopLeft',
                    fill: colors['muted-foreground'],
                    fontSize: 12,
                    dy: -18,
                    dx: -2,
                  }}
                />
              )}
              <ChartTooltip
                content={ChartTip}
                cursor={{ stroke: colors['chart-axis'], strokeWidth: 1, strokeOpacity: 0.4 }}
                isAnimationActive={false}
              />
              <Bar dataKey="fact" name="Факт" fill={colors['chart-fact']} radius={[3, 3, 0, 0]} maxBarSize={12} isAnimationActive={false} />
              <Line
                type="monotone"
                dataKey="forecast"
                name="Прогноз"
                stroke={colors['chart-forecast']}
                strokeWidth={2}
                strokeDasharray="5 4"
                dot={false}
                activeDot={{ r: 4, strokeWidth: 2, stroke: 'var(--card)' }}
                connectNulls={false}
                isAnimationActive={false}
              />
            </ComposedChart>
          </ResponsiveContainer>
        </div>
      ) : (
        <div className="max-h-[280px] overflow-y-auto rounded-lg border border-border">
          <Table>
            <TableHeader className="sticky top-0 bg-card">
              <TableRow>
                <TableHead>Дата</TableHead>
                <TableHead>Тип</TableHead>
                <TableHead className="text-right">Ушло / ожидаем</TableHead>
                <TableHead className="text-right">Под риском</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {rows.map((r) => (
                <TableRow key={r.date}>
                  <TableCell className="num">{formatDate(r.date)}</TableCell>
                  <TableCell className="text-muted-foreground">{r.kind === 'fact' ? 'факт' : 'прогноз'}</TableCell>
                  <TableCell className="text-right num">
                    {r.kind === 'fact' ? formatInt(r.fact) : r.forecast === null ? '—' : `≈${formatDecimal(r.forecast, 1)}`}
                  </TableCell>
                  <TableCell className="text-right num">{formatInt(r.atRisk)}</TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </div>
      )}
    </div>
  )
}

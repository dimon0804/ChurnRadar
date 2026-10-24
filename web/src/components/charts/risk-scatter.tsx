import { keepPreviousData, useQuery } from '@tanstack/react-query'
import { useMemo, useState } from 'react'
import { Link, useNavigate } from 'react-router'
import { CartesianGrid, ReferenceLine, ResponsiveContainer, Scatter, ScatterChart, Tooltip as RTooltip, XAxis, YAxis, ZAxis } from 'recharts'
import { api, queryKeys } from '@/api/endpoints'
import type { ScatterPoint } from '@/api/types'
import { AXIS_TICK, ChartTip, LegendItem, ViewToggle } from '@/components/charts/chart-card'
import { useLevelColors } from '@/components/charts/overview-charts'
import { RiskBadge } from '@/components/risk'
import { EmptyState, ErrorState } from '@/components/states'
import { Skeleton } from '@/components/ui/skeleton'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table'
import { formatInt, formatMoney, formatMoneyCompact, formatScore } from '@/lib/format'
import { RISK_LABEL } from '@/lib/labels'
import { toCustomersQuery, type CustomersState } from '@/lib/url-state'

const money = (v: number | null | undefined) => formatMoney(v, { cents: false })
const LEVELS = ['high', 'medium', 'low'] as const

/** Пороги уровней из настроек: вертикальные линии на оси score. */
function useThresholds() {
  const settings = useQuery({ queryKey: queryKeys.settings, queryFn: ({ signal }) => api.settings(signal), staleTime: 60_000 })
  return settings.data?.thresholds
}

/**
 * «Риск × ожидаемая потеря»: справа вверху — те, кто и уйдёт вероятнее, и стоит дороже.
 * Размер точки — MRR клиента. Нажатие открывает карточку.
 */
export function RiskScatter({ state, detailSuffix }: { state: CustomersState; detailSuffix: string }) {
  const navigate = useNavigate()
  const { c, level } = useLevelColors()
  const thresholds = useThresholds()
  const [view, setView] = useState<'chart' | 'table'>('chart')
  const base = toCustomersQuery(state)
  const query = {
    level: base.level,
    search: base.search,
    reason: base.reason,
    reason_scope: base.reason_scope,
    score_min: base.score_min,
    score_max: base.score_max,
  }
  const scatter = useQuery({
    queryKey: queryKeys.scatter(query),
    queryFn: ({ signal }) => api.scatter(query, signal),
    placeholderData: keepPreviousData,
  })
  const points = useMemo(() => scatter.data?.points ?? [], [scatter.data])
  const byLevel = useMemo(() => {
    const out: Record<(typeof LEVELS)[number], ScatterPoint[]> = { high: [], medium: [], low: [] }
    for (const p of points) if (p.risk_level in out) out[p.risk_level as (typeof LEVELS)[number]].push(p)
    return out
  }, [points])
  const open = (p: ScatterPoint | undefined) => p && navigate(`/customers/${p.id}${detailSuffix}`)
  const d = scatter.data

  const aria = d
    ? `Риск и ожидаемая потеря ${formatInt(d.shown)} самых дорогих клиентов из ${formatInt(d.total)}. ` +
      (points[0] ? `Дороже всех ${points[0].email || `#${points[0].id}`}: риск ${formatScore(points[0].risk_score)}, потеря ${money(points[0].expected_loss)}. ` : '') +
      'Таблица — по кнопке «Таблица».'
    : 'Загружаем точки'

  return (
    <div className="px-4 py-4 sm:px-5" data-testid="risk-scatter">
      <div className="mb-3 flex flex-wrap items-center gap-x-4 gap-y-2 text-[13px] text-muted-foreground">
        {LEVELS.map((l) => (
          <LegendItem key={l} color={level[l]} shape="dot">
            {RISK_LABEL[l]}
          </LegendItem>
        ))}
        <span className="text-subtle-foreground">размер точки — выручка в месяц</span>
        <span className="ml-auto">
          <ViewToggle value={view} onChange={setView} label="Вид: риск и потеря" />
        </span>
      </div>
      {scatter.isPending ? (
        <Skeleton className="h-[360px] w-full" />
      ) : scatter.isError ? (
        <ErrorState error={scatter.error} onRetry={() => void scatter.refetch()} />
      ) : points.length === 0 ? (
        <EmptyState title="Некого показать" description="Под эти фильтры не подходит ни один клиент с оценкой." />
      ) : view === 'chart' ? (
        <div className="h-[360px] w-full" role="img" aria-label={aria} data-chart>
          <ResponsiveContainer width="100%" height="100%">
            <ScatterChart margin={{ top: 8, right: 8, bottom: 4, left: 0 }}>
              <CartesianGrid stroke={c['chart-grid']} />
              <XAxis
                type="number"
                dataKey="risk_score"
                name="Риск"
                domain={[0, 100]}
                ticks={[0, 20, 40, 60, 80, 100]}
                tick={{ ...AXIS_TICK, fill: c['chart-axis'] }}
                tickLine={false}
                axisLine={{ stroke: c.border }}
                label={{ value: 'риск', position: 'insideBottomRight', offset: -2, fill: c['chart-axis'], fontSize: 12 }}
              />
              <YAxis
                type="number"
                dataKey="expected_loss"
                name="Потеря"
                tickFormatter={(v: number) => formatMoneyCompact(v)}
                tick={{ ...AXIS_TICK, fill: c['chart-axis'] }}
                tickLine={false}
                axisLine={false}
                width={60}
              />
              <ZAxis type="number" dataKey="monthly_revenue" range={[24, 360]} name="MRR" />
              {thresholds && (
                <>
                  <ReferenceLine x={thresholds.medium} stroke={level.medium} strokeDasharray="3 4" strokeOpacity={0.7} />
                  <ReferenceLine x={thresholds.high} stroke={level.high} strokeDasharray="3 4" strokeOpacity={0.7} />
                </>
              )}
              <RTooltip
                isAnimationActive={false}
                cursor={{ strokeDasharray: '3 3', stroke: c['chart-axis'] }}
                content={({ active, payload }) => {
                  const p = active ? (payload?.[0]?.payload as ScatterPoint | undefined) : undefined
                  if (!p) return null
                  return (
                    <ChartTip
                      title={p.email || `Клиент #${p.id}`}
                      lines={[
                        ['Риск', formatScore(p.risk_score)],
                        ['Ожидаемая потеря', money(p.expected_loss)],
                        ['Выручка в месяц', money(p.monthly_revenue)],
                      ]}
                      hint="Нажмите, чтобы открыть карточку"
                    />
                  )
                }}
              />
              {LEVELS.map((l) => (
                <Scatter
                  key={l}
                  name={RISK_LABEL[l]}
                  data={byLevel[l]}
                  fill={level[l]}
                  fillOpacity={0.7}
                  stroke={c.card}
                  strokeWidth={1}
                  isAnimationActive={false}
                  cursor="pointer"
                  onClick={(data: unknown) => {
                    const pt = data as { payload?: ScatterPoint } & Partial<ScatterPoint>
                    open(pt.payload ?? (pt.id !== undefined ? (pt as ScatterPoint) : undefined))
                  }}
                />
              ))}
            </ScatterChart>
          </ResponsiveContainer>
        </div>
      ) : (
        <div className="max-h-[360px] overflow-auto rounded-lg border border-border">
          <Table>
            <caption className="sr-only">Самые дорогие клиенты: риск и ожидаемая потеря</caption>
            <TableHeader className="sticky top-0 bg-card">
              <TableRow>
                <TableHead>Клиент</TableHead>
                <TableHead>Уровень</TableHead>
                <TableHead className="text-right">Риск</TableHead>
                <TableHead className="text-right">Потеря</TableHead>
                <TableHead className="text-right">В месяц</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {points.map((p) => (
                <TableRow key={p.id}>
                  <TableCell className="max-w-[220px]">
                    <Link to={`/customers/${p.id}${detailSuffix}`} className="block truncate font-medium text-link underline-offset-2 hover:underline">
                      {p.email || `#${p.id}`}
                    </Link>
                  </TableCell>
                  <TableCell>
                    <RiskBadge level={p.risk_level} />
                  </TableCell>
                  <TableCell className="text-right num">{formatScore(p.risk_score)}</TableCell>
                  <TableCell className="text-right num">{money(p.expected_loss)}</TableCell>
                  <TableCell className="text-right num">{money(p.monthly_revenue)}</TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </div>
      )}
      {d && d.total > d.shown && (
        <p className="mt-2 text-xs text-muted-foreground num">
          Показаны {formatInt(d.shown)} самых дорогих по ожидаемой потере из {formatInt(d.total)}. Остальных видно в таблице.
        </p>
      )}
    </div>
  )
}

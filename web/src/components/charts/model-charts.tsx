import { useQuery } from '@tanstack/react-query'
import { useMemo } from 'react'
import { Link } from 'react-router'
import { Bar, BarChart, CartesianGrid, Cell, ResponsiveContainer, Tooltip as RTooltip, XAxis, YAxis } from 'recharts'
import { api, queryKeys } from '@/api/endpoints'
import type { FeatureWeight, ModelSettingsPreview } from '@/api/types'
import { AXIS_TICK, ChartCard, ChartTip, LegendItem } from '@/components/charts/chart-card'
import { useLevelColors } from '@/components/charts/overview-charts'
import { EmptyState } from '@/components/states'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table'
import { compareAria, featureLossRows, whatIfLevelRows, type FeatureLossRow, type LevelCompareRow } from '@/lib/charts'
import { countLabel, formatInt, formatMoney, formatMoneyCompact } from '@/lib/format'
import { RISK_LABEL_LONG } from '@/lib/labels'
import { reasonHref } from '@/lib/url-state'

const money = (v: number | null | undefined) => formatMoney(v, { cents: false })

/** Сколько ожидаемой потери приходится на каждый признак модели (по последнему пересчёту). */
export function FeatureLossChart({ weights }: { weights: FeatureWeight[] }) {
  const summary = useQuery({ queryKey: queryKeys.summary, queryFn: ({ signal }) => api.summary(signal) })
  const { c } = useLevelColors()
  const rows = useMemo(() => featureLossRows(summary.data?.top_reasons, weights), [summary.data?.top_reasons, weights])
  const total = rows.reduce((s, r) => s + r.loss, 0)
  const height = Math.max(180, rows.length * 28 + 24)
  return (
    <ChartCard
      testId="chart-feature-loss"
      title="Вклад признаков в потерю"
      description="Сколько ожидаемой потери приходится на каждый признак по последнему пересчёту"
      ariaLabel={`Вклад признаков в ожидаемую потерю ${money(total)}: ${rows
        .filter((r) => r.loss > 0)
        .map((r) => `${r.title} ${money(r.loss)}`)
        .join(', ')}. Таблица — по кнопке «Таблица».`}
      height={height}
      chart={
        total === 0 ? (
          <EmptyState title="Потерь пока нет" className="py-6" />
        ) : (
          <ResponsiveContainer width="100%" height="100%">
            <BarChart data={rows} layout="vertical" margin={{ top: 0, right: 8, bottom: 0, left: 0 }} barCategoryGap="20%">
              <CartesianGrid horizontal={false} stroke={c['chart-grid']} />
              <XAxis type="number" tickFormatter={(v: number) => formatMoneyCompact(v)} tick={{ ...AXIS_TICK, fill: c['chart-axis'] }} tickLine={false} axisLine={false} />
              <YAxis
                type="category"
                dataKey="title"
                width={128}
                interval={0}
                tick={{ ...AXIS_TICK, fill: c['chart-axis'] }}
                tickLine={false}
                axisLine={false}
                tickFormatter={(t: string) => (t.length > 19 ? `${t.slice(0, 18)}…` : t)}
              />
              <RTooltip
                cursor={{ fill: c['chart-axis'], fillOpacity: 0.06 }}
                isAnimationActive={false}
                content={({ active, payload }) => {
                  const r = active ? (payload?.[0]?.payload as FeatureLossRow | undefined) : undefined
                  if (!r) return null
                  return (
                    <ChartTip
                      title={r.title}
                      lines={[
                        ['Ожидаемая потеря', money(r.loss)],
                        ['Клиентов', formatInt(r.customers)],
                      ]}
                      hint={r.enabled ? undefined : 'Признак выключен'}
                    />
                  )
                }}
              />
              <Bar dataKey="loss" radius={[0, 3, 3, 0]} isAnimationActive={false}>
                {rows.map((r) => (
                  <Cell key={r.code} fill={r.enabled ? c['chart-forecast'] : c['chart-axis']} fillOpacity={r.enabled ? 0.9 : 0.4} />
                ))}
              </Bar>
            </BarChart>
          </ResponsiveContainer>
        )
      }
      table={
        <Table>
          <caption className="sr-only">Вклад признаков в ожидаемую потерю</caption>
          <TableHeader className="sticky top-0 bg-card">
            <TableRow>
              <TableHead>Признак</TableHead>
              <TableHead className="text-right">Потеря</TableHead>
              <TableHead className="text-right">Клиентов</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {rows.map((r) => (
              <TableRow key={r.code}>
                <TableCell>
                  {r.customers > 0 ? (
                    <Link to={reasonHref(r.code)} className="text-link underline-offset-2 hover:underline">
                      {r.title}
                    </Link>
                  ) : (
                    <span className={r.enabled ? undefined : 'text-muted-foreground'}>{r.title}</span>
                  )}
                </TableCell>
                <TableCell className="text-right num">{money(r.loss)}</TableCell>
                <TableCell className="text-right num">{formatInt(r.customers)}</TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      }
    />
  )
}

/** «Что если»: сколько клиентов на каждом уровне сейчас и после правок. */
export function WhatIfLevelsChart({ preview }: { preview: ModelSettingsPreview | undefined }) {
  const { c, level } = useLevelColors()
  const rows = useMemo(() => whatIfLevelRows(preview), [preview])
  return (
    <ChartCard
      testId="chart-what-if-levels"
      title="Было и станет"
      description={preview ? `Клиенты по уровням риска${preview.exact ? '' : ' (по выборке, пересчитано на всю базу)'}` : 'Появится, как только вы что-нибудь поменяете'}
      ariaLabel={compareAria(rows)}
      height={200}
      legend={
        <>
          <LegendItem color={c['chart-axis']}>было</LegendItem>
          <LegendItem color={level.medium}>станет, цветом уровня</LegendItem>
        </>
      }
      chart={
        rows.length === 0 ? (
          <EmptyState title="Пока без изменений" className="py-6" />
        ) : (
          <ResponsiveContainer width="100%" height="100%">
            <BarChart data={rows} margin={{ top: 16, right: 4, bottom: 0, left: 0 }} barGap={2} barCategoryGap="24%">
              <CartesianGrid vertical={false} stroke={c['chart-grid']} />
              <XAxis dataKey="label" tick={{ ...AXIS_TICK, fill: c['chart-axis'] }} tickLine={false} axisLine={{ stroke: c.border }} />
              <YAxis allowDecimals={false} tick={{ ...AXIS_TICK, fill: c['chart-axis'] }} tickLine={false} axisLine={false} width={40} />
              <RTooltip
                cursor={{ fill: c['chart-axis'], fillOpacity: 0.08 }}
                isAnimationActive={false}
                content={({ active, payload }) => {
                  const r = active ? (payload?.[0]?.payload as LevelCompareRow | undefined) : undefined
                  if (!r) return null
                  const diff = r.after - r.before
                  return (
                    <ChartTip
                      title={RISK_LABEL_LONG[r.level]}
                      lines={[
                        ['Было', formatInt(r.before)],
                        ['Станет', formatInt(r.after)],
                      ]}
                      hint={diff === 0 ? 'Без изменений' : `${diff > 0 ? '+' : '−'}${countLabel(Math.abs(diff), ['клиент', 'клиента', 'клиентов'])}`}
                    />
                  )
                }}
              />
              <Bar dataKey="before" fill={c['chart-axis']} fillOpacity={0.45} radius={[3, 3, 0, 0]} isAnimationActive={false} />
              <Bar dataKey="after" radius={[3, 3, 0, 0]} isAnimationActive={false}>
                {rows.map((r) => (
                  <Cell key={r.level} fill={level[r.level]} />
                ))}
              </Bar>
            </BarChart>
          </ResponsiveContainer>
        )
      }
      table={
        <Table>
          <caption className="sr-only">Клиенты по уровням: было и станет</caption>
          <TableHeader>
            <TableRow>
              <TableHead>Уровень</TableHead>
              <TableHead className="text-right">Было</TableHead>
              <TableHead className="text-right">Станет</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {rows.map((r) => (
              <TableRow key={r.level}>
                <TableCell>{RISK_LABEL_LONG[r.level]}</TableCell>
                <TableCell className="text-right num">{formatInt(r.before)}</TableCell>
                <TableCell className="text-right num">{formatInt(r.after)}</TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      }
    />
  )
}

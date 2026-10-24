import { useQuery } from '@tanstack/react-query'
import { ChevronDown, ChevronUp, Lightbulb, Mails } from 'lucide-react'
import { useMemo, useState } from 'react'
import { Link } from 'react-router'
import { Bar, BarChart, CartesianGrid, ResponsiveContainer, Tooltip as RTooltip, XAxis, YAxis } from 'recharts'
import { api, queryKeys } from '@/api/endpoints'
import type { ReasonDetail, RiskLevel } from '@/api/types'
import { CampaignDialog } from '@/components/campaign-dialog'
import { AXIS_TICK, ChartTip, ViewToggle } from '@/components/charts/chart-card'
import { useLevelColors } from '@/components/charts/overview-charts'
import { RiskBadge } from '@/components/risk'
import { ErrorState } from '@/components/states'
import { Button } from '@/components/ui/button'
import { Card } from '@/components/ui/card'
import { Skeleton } from '@/components/ui/skeleton'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table'
import { countLabel, formatCompact, formatInt, formatMoney, formatPoints, formatShare } from '@/lib/format'
import { groupLabel, RISK_LABEL } from '@/lib/labels'
import { FEATURE_TITLE } from '@/lib/reasons'
import type { ReasonScope } from '@/lib/url-state'
import { cn } from '@/lib/utils'

const money = (v: number | null | undefined) => formatMoney(v, { cents: false })

function Stat({ label, value, hint }: { label: string; value: string; hint?: string }) {
  return (
    <div className="min-w-0 rounded-lg border border-border bg-card px-3 py-2.5">
      <p className="truncate text-xs text-muted-foreground">{label}</p>
      <p className="mt-0.5 truncate text-[17px] font-semibold tracking-[-0.01em] num">{value}</p>
      {hint && <p className="mt-0.5 truncate text-xs text-subtle-foreground">{hint}</p>}
    </div>
  )
}

function LevelBar({ r }: { r: ReasonDetail }) {
  const { level } = useLevelColors()
  const levels = (['high', 'medium', 'low', 'unknown'] as RiskLevel[]).filter((l) => r.by_level[l] > 0)
  const total = levels.reduce((s, l) => s + r.by_level[l], 0)
  if (total === 0) return null
  return (
    <div>
      <div className="flex h-2 w-full gap-0.5 overflow-hidden rounded-full bg-muted" aria-hidden>
        {levels.map((l) => (
          <div key={l} className="h-full first:rounded-l-full last:rounded-r-full" style={{ flexGrow: r.by_level[l], flexBasis: 0, background: level[l] }} />
        ))}
      </div>
      <p className="mt-1.5 text-xs text-muted-foreground num">
        {levels.map((l) => `${RISK_LABEL[l]}: ${formatInt(r.by_level[l])}`).join(' · ')}
      </p>
    </div>
  )
}

/** Сколько клиентов с каким вкладом этой причины в score: мелкий штрих или главная беда. */
function ContributionChart({ r }: { r: ReasonDetail }) {
  const [view, setView] = useState<'chart' | 'table'>('chart')
  const { c } = useLevelColors()
  const rows = r.contribution.map((b) => ({ ...b, label: `${b.from}–${b.to}` }))
  const total = rows.reduce((s, b) => s + b.customers, 0)
  if (total === 0) return null
  const heavy = rows.filter((b) => b.from >= 20).reduce((s, b) => s + b.customers, 0)
  return (
    <div>
      <div className="mb-2 flex items-center justify-between gap-3">
        <p className="text-[13px] font-medium">Насколько сильно причина влияет на score</p>
        <ViewToggle value={view} onChange={setView} label="Вид: вклад причины" />
      </div>
      {view === 'chart' ? (
        <div
          className="h-[150px] w-full"
          role="img"
          aria-label={`Вклад причины в score: ${rows.map((b) => `${b.label} пунктов — ${formatInt(b.customers)}`).join(', ')}. У ${formatInt(heavy)} клиентов вклад от 20 пунктов. Таблица — по кнопке «Таблица».`}
          data-chart
        >
          <ResponsiveContainer width="100%" height="100%">
            <BarChart data={rows} margin={{ top: 4, right: 4, bottom: 0, left: 0 }}>
              <CartesianGrid vertical={false} stroke={c['chart-grid']} />
              <XAxis dataKey="label" tick={{ ...AXIS_TICK, fill: c['chart-axis'] }} tickLine={false} axisLine={{ stroke: c.border }} interval={0} />
              <YAxis allowDecimals={false} tickFormatter={(v: number) => formatCompact(v)} tick={{ ...AXIS_TICK, fill: c['chart-axis'] }} tickLine={false} axisLine={false} width={32} />
              <RTooltip
                cursor={{ fill: c['chart-axis'], fillOpacity: 0.08 }}
                isAnimationActive={false}
                content={({ active, payload }) => {
                  const b = active ? (payload?.[0]?.payload as (typeof rows)[number] | undefined) : undefined
                  return b ? <ChartTip title={`Вклад ${b.label} пунктов`} lines={[['Клиентов', formatInt(b.customers)]]} /> : null
                }}
              />
              <Bar dataKey="customers" fill={c['chart-forecast']} radius={[3, 3, 0, 0]} isAnimationActive={false} />
            </BarChart>
          </ResponsiveContainer>
        </div>
      ) : (
        <div className="overflow-hidden rounded-lg border border-border">
          <Table>
            <caption className="sr-only">Клиенты по вкладу причины в score</caption>
            <TableHeader>
              <TableRow>
                <TableHead>Вклад, пунктов</TableHead>
                <TableHead className="text-right">Клиентов</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {rows.map((b) => (
                <TableRow key={b.label}>
                  <TableCell className="num">{b.label}</TableCell>
                  <TableCell className="text-right num">{formatInt(b.customers)}</TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </div>
      )}
    </div>
  )
}

/**
 * Панель причины над списком клиентов: что это простыми словами, сколько клиентов и денег,
 * как распределён вклад, что делать и рассылка всем с этой причиной.
 */
export function ReasonPanel({
  code,
  scope,
  onScopeChange,
  detailSuffix,
}: {
  code: string
  scope: ReasonScope
  onScopeChange: (scope: ReasonScope) => void
  detailSuffix: string
}) {
  const [open, setOpen] = useState(true)
  const [writing, setWriting] = useState(false)
  const query = useQuery({ queryKey: queryKeys.reason(code, scope), queryFn: ({ signal }) => api.reason(code, scope, signal) })
  const r = query.data
  const title = r?.title ?? FEATURE_TITLE[code] ?? code
  const target = useMemo(() => ({ reason: code, reason_scope: scope }), [code, scope])

  return (
    <Card className="mb-4 overflow-hidden" data-testid="reason-panel">
      <div className="flex flex-wrap items-center gap-x-3 gap-y-2 border-b border-border px-5 py-3">
        <div className="min-w-0 flex-1 basis-48">
          <p className="text-xs text-muted-foreground">Причина риска{r?.group ? ` · ${groupLabel(r.group)}` : ''}</p>
          <h2 className="truncate text-[15px] font-semibold">{title}</h2>
        </div>
        <div className="inline-flex shrink-0 rounded-md border border-border p-0.5" role="group" aria-label="Каких клиентов показывать">
          {(
            [
              ['any', 'Есть среди причин'],
              ['main', 'Главная причина'],
            ] as const
          ).map(([key, text]) => (
            <button
              key={key}
              type="button"
              aria-pressed={scope === key}
              onClick={() => onScopeChange(key)}
              className={cn(
                'inline-flex h-7 items-center rounded px-2.5 text-xs font-medium transition-colors outline-none focus-visible:ring-2 focus-visible:ring-ring',
                scope === key ? 'bg-accent text-foreground' : 'text-muted-foreground hover:text-foreground',
              )}
            >
              {text}
            </button>
          ))}
        </div>
        <Button variant="ghost" size="sm" onClick={() => setOpen((v) => !v)} aria-expanded={open} aria-controls="reason-panel-body">
          {open ? <ChevronUp /> : <ChevronDown />}
          {open ? 'Свернуть' : 'О причине'}
        </Button>
      </div>
      {open && (
        <div id="reason-panel-body" className="px-5 py-4">
          {query.isPending ? (
            <div className="grid gap-3 md:grid-cols-2">
              <Skeleton className="h-32" />
              <Skeleton className="h-32" />
            </div>
          ) : query.isError ? (
            <ErrorState error={query.error} onRetry={() => void query.refetch()} />
          ) : r ? (
            <div className="grid gap-5 lg:grid-cols-[minmax(0,1fr)_minmax(0,1fr)]">
              <div className="min-w-0 space-y-4">
                {r.plain && <p className="text-sm leading-6">{r.plain}</p>}
                {r.description && r.description !== r.plain && (
                  <details className="text-[13px] leading-5 text-muted-foreground">
                    <summary className="cursor-pointer font-medium text-foreground/80 outline-none focus-visible:underline">Как это считает модель</summary>
                    <p className="mt-1.5">{r.description}</p>
                  </details>
                )}
                {r.actions.length > 0 && (
                  <div>
                    <p className="mb-1.5 text-[13px] font-medium">Что делать</p>
                    <ul className="space-y-1.5" data-testid="reason-actions">
                      {r.actions.map((a) => (
                        <li key={a.title} className="flex gap-2.5 rounded-lg border border-border bg-card px-3 py-2">
                          <Lightbulb className="mt-0.5 size-4 shrink-0 text-chart-forecast" aria-hidden />
                          <span className="min-w-0 flex-1 text-sm leading-5">{a.title}</span>
                          <span className="shrink-0 text-xs text-muted-foreground num">{countLabel(a.customers, ['клиент', 'клиента', 'клиентов'])}</span>
                        </li>
                      ))}
                    </ul>
                  </div>
                )}
                <Button onClick={() => setWriting(true)} disabled={r.customers === 0} data-testid="reason-write-all">
                  <Mails />
                  Написать всем с этой причиной · {formatInt(r.customers)}
                </Button>
              </div>
              <div className="min-w-0 space-y-4">
                <div className="grid grid-cols-2 gap-2.5">
                  <Stat
                    label="Клиентов"
                    value={formatInt(r.customers)}
                    hint={scope === 'any' ? `главная у ${formatInt(r.primary_customers)}` : 'причина главная'}
                  />
                  <Stat
                    label="Потеря из-за причины"
                    value={money(r.expected_loss)}
                    hint={r.customers_expected_loss > 0 ? `${formatShare(r.expected_loss / r.customers_expected_loss, 0)} их ожидаемой потери` : undefined}
                  />
                  <Stat label="Выручка этих клиентов" value={money(r.monthly_revenue)} hint="в месяц" />
                  <Stat label="Средний вклад в score" value={r.avg_contribution === null ? '—' : `${formatPoints(r.avg_contribution)} п.`} hint="из 100" />
                </div>
                <LevelBar r={r} />
                <ContributionChart r={r} />
                {r.top_customers.length > 0 && (
                  <div>
                    <p className="mb-1.5 text-[13px] font-medium">Кому причина стоит дороже всего</p>
                    <ul className="divide-y divide-border rounded-lg border border-border bg-card">
                      {r.top_customers.map((cst) => (
                        <li key={cst.id}>
                          <Link
                            to={`/customers/${cst.id}${detailSuffix}`}
                            className="flex items-center gap-2.5 px-3 py-2 text-sm outline-none hover:bg-accent focus-visible:bg-accent"
                          >
                            <RiskBadge level={cst.risk_level} />
                            <span className="min-w-0 flex-1 truncate">{cst.email || `#${cst.id}`}</span>
                            <span className="shrink-0 font-medium num">{money(cst.expected_loss)}</span>
                          </Link>
                        </li>
                      ))}
                    </ul>
                  </div>
                )}
              </div>
            </div>
          ) : null}
        </div>
      )}
      <CampaignDialog
        key={`${code}-${scope}`}
        target={target}
        title={`Написать всем с причиной «${title}»`}
        open={writing}
        onOpenChange={setWriting}
      />
    </Card>
  )
}

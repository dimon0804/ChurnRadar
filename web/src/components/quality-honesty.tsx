import { BarChart3, Scale, Table2 } from 'lucide-react'
import { useMemo, useState } from 'react'
import {
  CartesianGrid,
  Line,
  LineChart,
  ReferenceLine,
  ResponsiveContainer,
  Tooltip as ChartTooltip,
  XAxis,
  YAxis,
} from 'recharts'
import type { QualityHonesty } from '@/api/types'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table'
import { useCssVars } from '@/hooks/use-css-vars'
import { formatDecimal, formatInt, formatShare, NBSP } from '@/lib/format'
import {
  brierRows,
  challengerVerdict,
  formatAucDelta,
  formatBrier,
  groupRows,
  groupsVerdict,
  HONESTY_METHOD_LABEL,
  HONESTY_METHODS,
  methodOf,
  RELIABILITY_SERIES,
  reliabilityDomain,
  reliabilitySeries,
  type ReliabilitySeries,
} from '@/lib/honesty'
import { formatAuc, formatLift, SCENARIO_LABEL, SCENARIOS, type ScenarioKey } from '@/lib/model-quality'
import { cn } from '@/lib/utils'

const VARS = ['chart-fact', 'chart-forecast', 'chart-rule-b', 'chart-grid', 'chart-axis', 'border', 'subtle-foreground'] as const
type Colors = Record<(typeof VARS)[number], string>

const SERIES_STYLE: Record<ReliabilitySeries, { color: (c: Colors) => string; dash?: string; label: string }> = {
  rules: { color: (c) => c['chart-rule-b'], dash: '5 4', label: 'Сырая вероятность правил' },
  rules_calibrated: { color: (c) => c['chart-fact'], label: 'После калибровки' },
  logreg: { color: (c) => c['chart-forecast'], dash: '2 3', label: 'Логистическая регрессия' },
}

const pct = (v: number) => `${Math.round(v * 100)}${NBSP}%`

function Swatch({ color, dash }: { color: string; dash?: string }) {
  return (
    <svg width="20" height="8" viewBox="0 0 20 8" aria-hidden className="shrink-0">
      <line x1="1" y1="4" x2="19" y2="4" stroke={color} strokeWidth="2.5" strokeLinecap="round" strokeDasharray={dash} />
    </svg>
  )
}

function ViewToggle({ view, onChange, title }: { view: 'chart' | 'table'; onChange: (v: 'chart' | 'table') => void; title: string }) {
  const options = [
    ['chart', 'График', <BarChart3 key="c" />],
    ['table', 'Таблица', <Table2 key="t" />],
  ] as const
  return (
    <div className="inline-flex shrink-0 rounded-md border border-border p-0.5" role="group" aria-label={`Вид: ${title}`}>
      {options.map(([key, text, icon]) => (
        <button
          key={key}
          type="button"
          onClick={() => onChange(key)}
          aria-pressed={view === key}
          className={cn(
            'inline-flex h-7 items-center gap-1.5 rounded px-2.5 text-[13px] font-medium transition-colors outline-none focus-visible:ring-2 focus-visible:ring-ring [&_svg]:size-3.5',
            view === key ? 'bg-accent text-foreground' : 'text-muted-foreground hover:text-foreground',
          )}
        >
          {icon}
          {text}
        </button>
      ))}
    </div>
  )
}

function ReliabilityCard({ h, scenario, colors }: { h: QualityHonesty; scenario: ScenarioKey; colors: Colors }) {
  const [view, setView] = useState<'chart' | 'table'>('chart')
  const series = useMemo(() => reliabilitySeries(h, scenario), [h, scenario])
  const top = reliabilityDomain(series)
  const ticks = Array.from({ length: 5 }, (_, i) => Number(((top * i) / 4).toFixed(3)))
  const shown = RELIABILITY_SERIES.filter((s) => series[s].length > 0)
  const title = 'Обещали против случилось'
  return (
    <Card className="flex flex-col" data-testid="quality-reliability">
      <CardHeader className="flex-row items-start justify-between gap-3">
        <div className="min-w-0 space-y-1">
          <CardTitle>{title}</CardTitle>
          <CardDescription>
            Услуги разбиты на группы по предсказанной вероятности непродления. Если точка лежит на диагонали, обещание
            сбылось: из услуг с прогнозом 20{NBSP}% не продлили как раз 20{NBSP}%
          </CardDescription>
        </div>
        <ViewToggle view={view} onChange={setView} title={title} />
      </CardHeader>
      <CardContent className="flex flex-1 flex-col">
        {view === 'chart' ? (
          <div>
            <ul className="mb-3 flex flex-wrap gap-x-4 gap-y-1.5 text-[13px] text-muted-foreground">
              {shown.map((s) => (
                <li key={s} className="flex items-center gap-2">
                  <Swatch color={SERIES_STYLE[s].color(colors)} dash={SERIES_STYLE[s].dash} />
                  {SERIES_STYLE[s].label}
                </li>
              ))}
              <li className="flex items-center gap-2">
                <Swatch color={colors['subtle-foreground']} dash="3 3" />
                Идеальная калибровка
              </li>
            </ul>
            <div
              className="h-[280px] w-full"
              role="img"
              aria-label={`Reliability-диаграмма, сценарий «${SCENARIO_LABEL[scenario]}»: предсказанная вероятность против фактической доли. Цифры — по кнопке «Таблица».`}
            >
              <ResponsiveContainer width="100%" height="100%">
                <LineChart margin={{ top: 8, right: 12, bottom: 18, left: 0 }}>
                  <CartesianGrid stroke={colors['chart-grid']} />
                  <XAxis
                    type="number"
                    dataKey="predicted"
                    domain={[0, top]}
                    ticks={ticks}
                    tickFormatter={pct}
                    allowDuplicatedCategory={false}
                    tick={{ fill: colors['chart-axis'], fontSize: 12 }}
                    tickLine={false}
                    axisLine={{ stroke: colors.border }}
                    label={{ value: 'Предсказали', position: 'insideBottom', offset: -12, fill: colors['chart-axis'], fontSize: 12 }}
                  />
                  <YAxis
                    type="number"
                    domain={[0, top]}
                    ticks={ticks}
                    tickFormatter={pct}
                    tick={{ fill: colors['chart-axis'], fontSize: 12 }}
                    tickLine={false}
                    axisLine={false}
                    width={44}
                  />
                  <ReferenceLine
                    segment={[
                      { x: 0, y: 0 },
                      { x: top, y: top },
                    ]}
                    stroke={colors['subtle-foreground']}
                    strokeDasharray="3 3"
                    ifOverflow="hidden"
                  />
                  <ChartTooltip
                    isAnimationActive={false}
                    cursor={false}
                    content={({ active, payload }) => {
                      if (!active || !payload?.length) return null
                      return (
                        <div className="min-w-48 rounded-lg border border-border bg-popover px-3 py-2.5 text-[13px] shadow-pop">
                          {payload.map((item) => {
                            const p = item.payload as { predicted: number; actual: number; n: number }
                            return (
                              <p key={String(item.name)} className="flex justify-between gap-4">
                                <span className="text-muted-foreground">{item.name}</span>
                                <span className="num">
                                  {formatShare(p.predicted)} → <b>{formatShare(p.actual)}</b>
                                </span>
                              </p>
                            )
                          })}
                        </div>
                      )
                    }}
                  />
                  {shown.map((s) => (
                    <Line
                      key={s}
                      data={series[s]}
                      dataKey="actual"
                      name={SERIES_STYLE[s].label}
                      stroke={SERIES_STYLE[s].color(colors)}
                      strokeDasharray={SERIES_STYLE[s].dash}
                      strokeWidth={2}
                      dot={{ r: 3 }}
                      activeDot={{ r: 5 }}
                      isAnimationActive={false}
                    />
                  ))}
                </LineChart>
              </ResponsiveContainer>
            </div>
          </div>
        ) : (
          <div className="overflow-x-auto rounded-lg border border-border">
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Метод</TableHead>
                  <TableHead className="text-right">Услуг</TableHead>
                  <TableHead className="text-right">Предсказали</TableHead>
                  <TableHead className="text-right">Случилось</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {shown.flatMap((s) =>
                  series[s].map((p, i) => (
                    <TableRow key={`${s}-${i}`}>
                      <TableCell className={cn(i > 0 && 'text-muted-foreground')}>{SERIES_STYLE[s].label}</TableCell>
                      <TableCell className="text-right num">{formatInt(p.n)}</TableCell>
                      <TableCell className="text-right num">{formatShare(p.predicted)}</TableCell>
                      <TableCell className="text-right num">{formatShare(p.actual)}</TableCell>
                    </TableRow>
                  )),
                )}
              </TableBody>
            </Table>
          </div>
        )}
        <p className="mt-4 text-[13px] leading-5 text-muted-foreground">
          Калибровку обучили на сценарии «{SCENARIO_LABEL[h.calibration.fit_scenario]}» ({formatInt(h.calibration.services)}{' '}
          услуг). Порядок клиентов она не меняет, только переводит score в честные проценты.
        </p>
      </CardContent>
    </Card>
  )
}

function BrierCard({ h }: { h: QualityHonesty }) {
  const rows = brierRows(h)
  const fit = SCENARIO_LABEL[h.calibration.fit_scenario]
  return (
    <Card data-testid="quality-brier">
      <CardHeader>
        <CardTitle>Brier score</CardTitle>
        <CardDescription>Средний квадрат ошибки вероятности по услугам. Меньше — лучше, 0 — идеальный прогноз</CardDescription>
      </CardHeader>
      <CardContent className="space-y-3">
        {rows.map((r) => (
          <div key={r.scenario} className="rounded-lg border border-border px-3.5 py-3" data-scenario={r.scenario}>
            <p className="text-[13px] font-medium text-muted-foreground">{SCENARIO_LABEL[r.scenario]}</p>
            <p className="mt-1 flex flex-wrap items-baseline gap-x-2 num">
              <span className="text-sm text-muted-foreground" data-role="raw">
                {formatBrier(r.raw)}
              </span>
              <span className="text-subtle-foreground">→</span>
              <span
                className={cn('text-xl font-semibold tracking-[-0.01em]', r.better === true && 'text-risk-low', r.better === false && 'text-risk-high')}
                data-role="calibrated"
              >
                {formatBrier(r.calibrated)}
              </span>
              <span className="text-xs text-muted-foreground">сырая → после калибровки</span>
            </p>
            {!r.fittedHere && r.better === false && r.ownFit !== null && (
              <p className="mt-1.5 text-xs leading-5 text-muted-foreground">
                Калибровка обучена на сценарии «{fit}», а здесь уровень потерь другой, поэтому она промахивается. Обученная на
                этом же сценарии дала бы {formatBrier(r.ownFit)}.
              </p>
            )}
          </div>
        ))}
      </CardContent>
    </Card>
  )
}

function ChallengerCard({ h, scenario }: { h: QualityHonesty; scenario: ScenarioKey }) {
  const verdict = useMemo(() => challengerVerdict(h), [h])
  const best = (metric: 'service_roc_auc' | 'customer_roc_auc' | 'customer_lift_top10' | 'service_brier', low = false) => {
    const vals = HONESTY_METHODS.map((m) => methodOf(h, scenario, m)?.[metric]).filter((v): v is number => typeof v === 'number')
    return vals.length ? (low ? Math.min(...vals) : Math.max(...vals)) : null
  }
  const top = {
    service_roc_auc: best('service_roc_auc'),
    customer_roc_auc: best('customer_roc_auc'),
    customer_lift_top10: best('customer_lift_top10'),
    service_brier: best('service_brier', true),
  }
  const cell = (v: number | null | undefined, key: keyof typeof top, text: string) => (
    <TableCell className={cn('text-right num', typeof v === 'number' && v === top[key] && 'font-semibold')}>{text}</TableCell>
  )
  return (
    <Card data-testid="quality-challenger">
      <CardHeader>
        <CardTitle>Правила против ML</CardTitle>
        <CardDescription>
          Те же признаки, те же клиенты, сценарий «{SCENARIO_LABEL[scenario]}». ML-модели и калибровку учили на исходах сценария
          «{SCENARIO_LABEL[h.calibration.fit_scenario]}» на одной половине клиентов, а проверяли на другой, и наоборот:
          цифры ниже вне обучающей выборки
        </CardDescription>
      </CardHeader>
      <CardContent>
        <div className="overflow-x-auto rounded-lg border border-border">
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Метод</TableHead>
                <TableHead className="text-right">AUC, услуги</TableHead>
                <TableHead className="text-right">AUC, клиенты</TableHead>
                <TableHead className="text-right">Lift топ-10{NBSP}%</TableHead>
                <TableHead className="text-right">Brier</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {HONESTY_METHODS.map((m) => {
                const row = methodOf(h, scenario, m)
                if (!row) return null
                return (
                  <TableRow key={m}>
                    <TableCell>{HONESTY_METHOD_LABEL[m]}</TableCell>
                    {cell(row.service_roc_auc, 'service_roc_auc', formatAuc(row.service_roc_auc))}
                    {cell(row.customer_roc_auc, 'customer_roc_auc', formatAuc(row.customer_roc_auc))}
                    {cell(row.customer_lift_top10, 'customer_lift_top10', formatLift(row.customer_lift_top10))}
                    {cell(row.service_brier, 'service_brier', formatBrier(row.service_brier))}
                  </TableRow>
                )
              })}
            </TableBody>
          </Table>
        </div>
        <div className="mt-4 space-y-2 text-[13px] leading-5 text-muted-foreground" data-role="verdict">
          {verdict.scenarios.map((s) => (
            <p key={s.scenario}>{s.text}</p>
          ))}
          <p className="text-foreground">{verdict.summary}</p>
        </div>
      </CardContent>
    </Card>
  )
}

function GroupsCard({ h }: { h: QualityHonesty }) {
  const rows = useMemo(() => groupRows(h), [h])
  if (rows.length === 0) return null
  return (
    <Card data-testid="quality-groups">
      <CardHeader>
        <CardTitle>Группа признаков «Оплата»</CardTitle>
        <CardDescription>
          «Ждёт оплаты», «не хватает денег» и «неуспешные платежи» часто срабатывают вместе и говорят об одном и том же.
          Группа складывает их не простой суммой, а с потолком, чтобы не считать одну проблему трижды
        </CardDescription>
      </CardHeader>
      <CardContent>
        <div className="overflow-x-auto rounded-lg border border-border">
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Вариант</TableHead>
                <TableHead className="text-right">Сменили уровень</TableHead>
                {SCENARIOS.map((k) => (
                  <TableHead key={k} className="text-right">
                    AUC · {SCENARIO_LABEL[k]}
                  </TableHead>
                ))}
                {SCENARIOS.map((k) => (
                  <TableHead key={`l-${k}`} className="text-right">
                    Lift «высокого» · {SCENARIO_LABEL[k]}
                  </TableHead>
                ))}
              </TableRow>
            </TableHeader>
            <TableBody>
              {rows.map((r) => (
                <TableRow key={r.label} className={cn(r.baseline && 'bg-accent/50')}>
                  <TableCell className={cn(r.baseline && 'font-medium')}>
                    {r.label}
                    {r.baseline && <span className="ml-1.5 text-xs text-muted-foreground">(по умолчанию)</span>}
                  </TableCell>
                  <TableCell className="text-right num">{r.baseline ? '—' : formatInt(r.levelChanges)}</TableCell>
                  {SCENARIOS.map((k) => (
                    <TableCell key={k} className="text-right num">
                      {formatAuc(r.auc[k])}
                      {!r.baseline && <span className="ml-1 text-xs text-muted-foreground">{formatAucDelta(r.aucDelta[k])}</span>}
                    </TableCell>
                  ))}
                  {SCENARIOS.map((k) => (
                    <TableCell key={`l-${k}`} className="text-right num">
                      {formatLift(r.lift[k])}
                    </TableCell>
                  ))}
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </div>
        <p className="mt-4 text-[13px] leading-5 text-muted-foreground">{groupsVerdict(rows)}</p>
      </CardContent>
    </Card>
  )
}

/** Раздел «Честность модели»: score — ранжирование, вероятность — калиброванная, и сравнение с ML. */
export function HonestySection({ honesty, scenario }: { honesty: QualityHonesty; scenario: ScenarioKey }) {
  const colors = useCssVars(VARS)
  const h = honesty
  const hasScenario = Boolean(h.calibration.by_scenario[scenario])
  return (
    <section className="space-y-4 pt-4" data-testid="quality-honesty" aria-labelledby="quality-honesty-title">
      <div className="flex items-start gap-3 rounded-xl border border-border bg-card px-5 py-4">
        <Scale className="mt-1 size-5 shrink-0 text-muted-foreground" aria-hidden />
        <div className="min-w-0">
          <h2 id="quality-honesty-title" className="text-xs font-semibold tracking-wide text-muted-foreground uppercase">
            Честность модели
          </h2>
          <p className="mt-1 text-[20px] leading-7 font-semibold tracking-[-0.01em]">
            Score — это ранжирование, вероятность — калиброванная
          </p>
          <p className="mt-1.5 max-w-3xl text-sm leading-6 text-muted-foreground">
            Score от 0 до 100 только расставляет клиентов по очереди: кому звонить первым. Ожидаемая потеря в евро и
            прогноз непродлений считаются по калиброванной вероятности, которую сверили с тем, что на самом деле произошло
            в симуляции.
          </p>
        </div>
      </div>
      {hasScenario && (
        <div className="grid gap-4 lg:grid-cols-[minmax(0,1fr)_minmax(0,22rem)]">
          <ReliabilityCard h={h} scenario={scenario} colors={colors} />
          <BrierCard h={h} />
        </div>
      )}
      {hasScenario && <ChallengerCard h={h} scenario={scenario} />}
      <GroupsCard h={h} />
      {typeof h.calibration.services === 'number' && (
        <p className="px-1 text-xs text-subtle-foreground">
          Расчёт на {formatInt(h.calibration.services)} услугах.{' '}
          {h.groups?.length ? `Вариантов группы «Оплата»: ${formatDecimal(h.groups.length - 1, 0)}.` : ''}
        </p>
      )}
    </section>
  )
}

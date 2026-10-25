import { useQuery } from '@tanstack/react-query'
import { AlertTriangle, BarChart3, FlaskConical, Info, Table2 } from 'lucide-react'
import { useMemo, useState, type ReactNode } from 'react'
import { Link } from 'react-router'
import {
  Bar,
  BarChart,
  CartesianGrid,
  Cell,
  LabelList,
  Line,
  LineChart,
  ReferenceLine,
  ResponsiveContainer,
  Tooltip as ChartTooltip,
  XAxis,
  YAxis,
} from 'recharts'
import { api, queryKeys } from '@/api/endpoints'
import type { ModelQuality, QualityScenario, Summary } from '@/api/types'
import { PageHeader } from '@/components/page-header'
import { RiskGlyph, RISK_TEXT } from '@/components/risk'
import { ErrorState } from '@/components/states'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Skeleton } from '@/components/ui/skeleton'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table'
import { useCssVars } from '@/hooks/use-css-vars'
import { countLabel, formatDecimal, formatInt, formatShare, NBSP } from '@/lib/format'
import { RISK_LABEL_LONG } from '@/lib/labels'
import {
  calibrationRows,
  formatAuc,
  formatLift,
  headline,
  levelRow,
  liftRows,
  METHOD_LABEL,
  METHOD_ORDER,
  rocRows,
  SCENARIO_HINT,
  SCENARIO_LABEL,
  SCENARIOS,
  scoreHistogram,
  settingsDrift,
  type RocMethod,
  type ScenarioKey,
} from '@/lib/model-quality'
import { cn } from '@/lib/utils'

const CHART_VARS = [
  'chart-fact',
  'chart-forecast',
  'chart-rule-a',
  'chart-rule-b',
  'chart-grid',
  'chart-axis',
  'muted-foreground',
  'subtle-foreground',
  'foreground',
  'border',
  'risk-low-bar',
  'risk-medium-bar',
  'risk-high-bar',
] as const

type Colors = Record<(typeof CHART_VARS)[number], string>

const pct = (v: number) => `${Math.round(v * 100)}${NBSP}%`

// --- общие кусочки ----------------------------------------------------------------------------------

function Segmented<K extends string>({
  value,
  options,
  onChange,
  label,
}: {
  value: K
  options: readonly (readonly [K, string, ReactNode?])[]
  onChange: (key: K) => void
  label: string
}) {
  return (
    <div className="inline-flex rounded-[11px] border border-border bg-surface-2 p-[3px]" role="group" aria-label={label}>
      {options.map(([key, text, icon]) => (
        <button
          key={key}
          type="button"
          onClick={() => onChange(key)}
          aria-pressed={value === key}
          className={cn(
            'inline-flex h-7 items-center gap-1.5 rounded-[8px] px-2.5 text-[13px] font-medium transition-colors outline-none focus-visible:ring-2 focus-visible:ring-ring [&_svg]:size-3.5',
            value === key ? 'bg-card text-foreground shadow-sm ring-1 ring-brand/45' : 'text-muted-foreground hover:text-foreground',
          )}
        >
          {icon}
          {text}
        </button>
      ))}
    </div>
  )
}

/** Карточка с графиком и переключателем на таблицу: у каждого графика есть текстовая версия. */
function ChartCard({
  title,
  description,
  chart,
  table,
  footer,
  className,
  testId,
}: {
  title: string
  description?: ReactNode
  chart: ReactNode
  table: ReactNode
  footer?: ReactNode
  className?: string
  testId?: string
}) {
  const [view, setView] = useState<'chart' | 'table'>('chart')
  return (
    <Card className={cn('flex flex-col', className)} data-testid={testId}>
      <CardHeader className="flex-row items-start justify-between gap-3">
        <div className="min-w-0 space-y-1">
          <CardTitle>{title}</CardTitle>
          {description && <CardDescription>{description}</CardDescription>}
        </div>
        <Segmented
          label={`Вид: ${title}`}
          value={view}
          onChange={setView}
          options={[
            ['chart', 'График', <BarChart3 key="c" />],
            ['table', 'Таблица', <Table2 key="t" />],
          ]}
        />
      </CardHeader>
      <CardContent className="flex flex-1 flex-col">
        {view === 'chart' ? chart : <div className="overflow-x-auto rounded-lg border border-border">{table}</div>}
        {footer && <div className="mt-4 text-[13px] leading-5 text-muted-foreground">{footer}</div>}
      </CardContent>
    </Card>
  )
}

function Tip({ title, lines }: { title: string; lines: [string, string][] }) {
  return (
    <div className="min-w-44 rounded-lg border border-border bg-popover px-3 py-2.5 text-[13px] shadow-pop">
      <p className="mb-1 font-medium">{title}</p>
      {lines.map(([k, v]) => (
        <p key={k} className="flex justify-between gap-4">
          <span className="text-muted-foreground">{k}</span>
          <span className="font-semibold num">{v}</span>
        </p>
      ))}
    </div>
  )
}

function LineSwatch({ color, dash }: { color: string; dash?: string }) {
  return (
    <svg width="20" height="8" viewBox="0 0 20 8" aria-hidden className="shrink-0">
      <line x1="1" y1="4" x2="19" y2="4" stroke={color} strokeWidth="2.5" strokeLinecap="round" strokeDasharray={dash} />
    </svg>
  )
}

// --- верхняя строка ------------------------------------------------------------------------------

export function QualityHeadline({ q }: { q: ModelQuality }) {
  const h = headline(q)
  const base = SCENARIOS.map((k) => q.scenarios[k].base_rates.churned)
  const baseText = base.every((v) => typeof v === 'number')
    ? `${formatDecimal(Math.min(...(base as number[])) * 100, 0)}–${formatDecimal(Math.max(...(base as number[])) * 100, 0)}${NBSP}%`
    : '—'
  const items: { label: string; value: string; sub: string; testId: string }[] = [
    {
      label: 'Высокий риск уходит чаще среднего',
      value: `в ${h.liftRange} раза`,
      sub: 'Lift уровня «высокий»: доля ушедших полностью за 30 дней против средней по базе',
      testId: 'quality-lift',
    },
    {
      label: 'ROC AUC',
      value: h.aucRange,
      sub: '0,5 — случайный порядок, 1 — все ушедшие выше всех оставшихся',
      testId: 'quality-auc',
    },
    {
      label: 'Из «высоких» ушли полностью',
      value: `${h.precisionRange}${NBSP}%`,
      sub: `В среднем по базе ушли ${baseText}`,
      testId: 'quality-precision',
    },
    {
      label: 'Клиентов в проверке',
      value: formatInt(h.accounts),
      sub: `Синтетика, seed ${q.params.seed}, горизонт ${q.params.horizon_days} дней, два сценария будущего`,
      testId: 'quality-accounts',
    },
  ]
  return (
    <div className="grid grid-cols-2 gap-3 sm:gap-4 xl:grid-cols-4">
      {items.map((it) => (
        <Card key={it.testId} className="flex flex-col px-5 py-4" data-testid={it.testId}>
          <p className="text-[13px] font-medium text-muted-foreground">{it.label}</p>
          <p className="mt-2 text-[28px] leading-none font-semibold tracking-[-0.025em] num" data-role="value">
            {it.value}
          </p>
          <p className="mt-2.5 text-[13px] leading-5 text-muted-foreground">{it.sub}</p>
        </Card>
      ))}
    </div>
  )
}

function Notices({ q, drift }: { q: ModelQuality; drift: string[] }) {
  const stale = q.current_model.matches === false
  if (!stale && drift.length === 0) return null
  return (
    <div className="space-y-2" data-testid="quality-notices">
      {stale && (
        <Notice>
          Код модели поменялся после проверки: цифры ниже посчитаны на версии{' '}
          <code className="num">{q.params.riskcore_sources}</code>, а работает{' '}
          <code className="num">{q.current_model.riskcore_sources}</code>. Чтобы обновить, запустите{' '}
          <code>python -m tools.calibration</code> и пересоберите api.
        </Notice>
      )}
      {drift.length > 0 && (
        <Notice>
          Проверка шла на весах и порогах по умолчанию, а сейчас в настройках модели: {drift.join('; ')}. На
          изменённых весах ранжирование будет другим, его нужно проверить заново.
        </Notice>
      )}
    </div>
  )
}

function Notice({ children }: { children: ReactNode }) {
  return (
    <div className="flex items-start gap-3 rounded-xl border border-risk-medium/30 bg-risk-medium-bg px-4 py-3 text-sm leading-6">
      <AlertTriangle className="mt-1 size-4 shrink-0 text-risk-medium" aria-hidden />
      <p>{children}</p>
    </div>
  )
}

// --- lift по уровням -----------------------------------------------------------------------------

function LiftCard({ sc, colors }: { sc: QualityScenario; colors: Colors }) {
  const rows = liftRows(sc)
  const all = levelRow(sc, 'all')
  const data = rows.map((r) => ({ ...r, label: RISK_LABEL_LONG[r.level] }))
  const fill = { low: colors['risk-low-bar'], medium: colors['risk-medium-bar'], high: colors['risk-high-bar'] }
  const max = Math.max(1.5, ...rows.map((r) => r.lift ?? 0))
  return (
    <ChartCard
      testId="quality-lift-chart"
      title="Во сколько раз чаще уходят"
      description={`Доля ушедших полностью по уровню риска против средней по базе (${formatShare(all?.churn_rate)})`}
      chart={
        <div
          className="h-[260px] w-full"
          role="img"
          aria-label={`Lift по уровням: ${rows.map((r) => `${RISK_LABEL_LONG[r.level]} ${formatLift(r.lift)}`).join(', ')}. Таблица — по кнопке «Таблица».`}
        >
          <ResponsiveContainer width="100%" height="100%">
            <BarChart data={data} margin={{ top: 24, right: 8, bottom: 0, left: 0 }}>
              <CartesianGrid vertical={false} stroke={colors['chart-grid']} />
              <XAxis
                dataKey="label"
                tick={{ fill: colors['chart-axis'], fontSize: 12 }}
                tickLine={false}
                axisLine={{ stroke: colors.border }}
              />
              <YAxis
                domain={[0, Math.ceil(max)]}
                tickFormatter={(v: number) => `×${formatDecimal(v, 1)}`}
                tick={{ fill: colors['chart-axis'], fontSize: 12 }}
                tickLine={false}
                axisLine={false}
                width={40}
              />
              <ReferenceLine
                y={1}
                stroke={colors['muted-foreground']}
                strokeDasharray="4 4"
                label={{ value: 'в среднем', position: 'insideTopLeft', fill: colors['muted-foreground'], fontSize: 12 }}
              />
              <ChartTooltip
                cursor={{ fill: colors['chart-grid'], fillOpacity: 0.5 }}
                isAnimationActive={false}
                content={({ active, payload }) => {
                  const row = active ? (payload?.[0]?.payload as (typeof data)[number] | undefined) : undefined
                  if (!row) return null
                  return (
                    <Tip
                      title={row.label}
                      lines={[
                        ['Клиентов', formatInt(row.n)],
                        ['Ушли полностью', formatShare(row.rate)],
                        ['Чаще среднего', formatLift(row.lift)],
                      ]}
                    />
                  )
                }}
              />
              <Bar dataKey="lift" radius={[4, 4, 0, 0]} maxBarSize={72} isAnimationActive={false}>
                {data.map((r) => (
                  <Cell key={r.level} fill={fill[r.level]} />
                ))}
                <LabelList
                  dataKey="lift"
                  position="top"
                  formatter={(v: unknown) => formatLift(typeof v === 'number' ? v : null)}
                  fill={colors.foreground}
                  fontSize={13}
                  fontWeight={600}
                />
              </Bar>
            </BarChart>
          </ResponsiveContainer>
        </div>
      }
      table={
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>Уровень</TableHead>
              <TableHead className="text-right">Клиентов</TableHead>
              <TableHead className="text-right">Ушли полностью</TableHead>
              <TableHead className="text-right">Lift</TableHead>
              <TableHead className="text-right">Потеряли ≥1 услугу</TableHead>
              <TableHead className="text-right">Lift</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {(['low', 'medium', 'high', 'all'] as const).map((l) => {
              const r = levelRow(sc, l)
              return (
                <TableRow key={l}>
                  <TableCell>{l === 'all' ? 'Вся база' : RISK_LABEL_LONG[l]}</TableCell>
                  <TableCell className="text-right num">{formatInt(r?.n)}</TableCell>
                  <TableCell className="text-right num">{formatShare(r?.churn_rate)}</TableCell>
                  <TableCell className="text-right num">{formatLift(r?.churn_lift)}</TableCell>
                  <TableCell className="text-right num">{formatShare(r?.lost_any_rate)}</TableCell>
                  <TableCell className="text-right num">{formatLift(r?.lost_any_lift)}</TableCell>
                </TableRow>
              )
            })}
          </TableBody>
        </Table>
      }
    />
  )
}

// --- ROC -----------------------------------------------------------------------------------------

const ROC_STYLE: Record<RocMethod, { color: (c: Colors) => string; dash?: string; width: number }> = {
  model: { color: (c) => c['chart-fact'], width: 2.5 },
  autoprolong_off: { color: (c) => c['chart-rule-a'], dash: '6 3', width: 2 },
  prolong_wait: { color: (c) => c['chart-rule-b'], dash: '2 3', width: 2 },
  segment_oracle: { color: (c) => c['subtle-foreground'], dash: '8 3 2 3', width: 1.5 },
}

function RocCard({ sc, colors }: { sc: QualityScenario; colors: Colors }) {
  const rows = useMemo(() => rocRows(sc), [sc])
  const ranking = sc.ranking.churned
  const methods: RocMethod[] = ['model', 'autoprolong_off', 'prolong_wait', 'segment_oracle']
  const ci = ranking.model.roc_auc_ci
  return (
    <ChartCard
      testId="quality-roc"
      title="ROC-кривая"
      description="Идём по списку от самого высокого score вниз: какую долю ушедших уже нашли и сколько оставшихся задели по пути"
      chart={
        <div>
          <ul className="mb-3 flex flex-wrap gap-x-4 gap-y-1.5 text-[13px]">
            {methods.map((m) => (
              <li key={m} className="flex items-center gap-2 text-muted-foreground">
                <LineSwatch color={ROC_STYLE[m].color(colors)} dash={ROC_STYLE[m].dash} />
                {METHOD_LABEL[m]}
                <span className="font-semibold text-foreground num">{formatAuc(ranking[m].roc_auc)}</span>
              </li>
            ))}
            <li className="flex items-center gap-2 text-muted-foreground">
              <LineSwatch color={colors.border} dash="3 3" />
              Случайно <span className="font-semibold text-foreground num">0,50</span>
            </li>
          </ul>
          <div
            className="h-[280px] w-full"
            role="img"
            aria-label={`ROC-кривые, цель «ушёл полностью». AUC: ${methods.map((m) => `${METHOD_LABEL[m]} ${formatAuc(ranking[m].roc_auc)}`).join(', ')}.`}
          >
            <ResponsiveContainer width="100%" height="100%">
              <LineChart data={rows} margin={{ top: 8, right: 12, bottom: 18, left: 0 }}>
                <CartesianGrid stroke={colors['chart-grid']} />
                <XAxis
                  type="number"
                  dataKey="fpr"
                  domain={[0, 1]}
                  ticks={[0, 0.25, 0.5, 0.75, 1]}
                  tickFormatter={pct}
                  tick={{ fill: colors['chart-axis'], fontSize: 12 }}
                  tickLine={false}
                  axisLine={{ stroke: colors.border }}
                  label={{ value: 'Задели оставшихся', position: 'insideBottom', offset: -12, fill: colors['chart-axis'], fontSize: 12 }}
                />
                <YAxis
                  type="number"
                  domain={[0, 1]}
                  ticks={[0, 0.25, 0.5, 0.75, 1]}
                  tickFormatter={pct}
                  tick={{ fill: colors['chart-axis'], fontSize: 12 }}
                  tickLine={false}
                  axisLine={false}
                  width={44}
                />
                <ChartTooltip
                  isAnimationActive={false}
                  cursor={{ stroke: colors['chart-axis'], strokeOpacity: 0.4 }}
                  content={({ active, payload }) => {
                    const row = active ? (payload?.[0]?.payload as (typeof rows)[number] | undefined) : undefined
                    if (!row) return null
                    return (
                      <Tip
                        title={`Задели ${formatShare(row.fpr, 0)} оставшихся`}
                        lines={methods.map((m) => [METHOD_LABEL[m], `нашли ${formatShare(row[m], 0)}`])}
                      />
                    )
                  }}
                />
                <Line dataKey="random" stroke={colors.border} strokeDasharray="3 3" strokeWidth={1.5} dot={false} activeDot={false} isAnimationActive={false} />
                {[...methods].reverse().map((m) => (
                  <Line
                    key={m}
                    dataKey={m}
                    stroke={ROC_STYLE[m].color(colors)}
                    strokeDasharray={ROC_STYLE[m].dash}
                    strokeWidth={ROC_STYLE[m].width}
                    dot={false}
                    activeDot={m === 'model' ? { r: 4, strokeWidth: 2, stroke: 'var(--card)' } : false}
                    isAnimationActive={false}
                  />
                ))}
              </LineChart>
            </ResponsiveContainer>
          </div>
        </div>
      }
      table={
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>Задели оставшихся</TableHead>
              {methods.map((m) => (
                <TableHead key={m} className="text-right">
                  {METHOD_LABEL[m]}
                </TableHead>
              ))}
            </TableRow>
          </TableHeader>
          <TableBody>
            {rows
              .filter((_, i) => i % 10 === 0)
              .map((r) => (
                <TableRow key={r.fpr}>
                  <TableCell className="num">{formatShare(r.fpr, 0)}</TableCell>
                  {methods.map((m) => (
                    <TableCell key={m} className="text-right num">
                      {formatShare(r[m], 0)}
                    </TableCell>
                  ))}
                </TableRow>
              ))}
          </TableBody>
        </Table>
      }
      footer={
        ci && typeof ci[0] === 'number' && typeof ci[1] === 'number' ? (
          <>
            AUC модели {formatAuc(ranking.model.roc_auc)}, 95 % интервал по бутстрепу {formatAuc(ci[0])}–{formatAuc(ci[1])}.
            Серая штрихпунктирная линия — скрытый сегмент генератора: модель его не видит, это ориентир того, сколько
            вообще можно вытащить из данных.
          </>
        ) : null
      }
    />
  )
}

// --- порог «высокий» -------------------------------------------------------------------------------

export function ThresholdCard({ sc, q }: { sc: QualityScenario; q: ModelQuality }) {
  const high = sc.thresholds.high
  const mid = sc.thresholds.medium_plus
  const t = q.params.thresholds
  const tiles: { label: string; value: string; sub: string }[] = [
    {
      label: 'Точность',
      value: formatShare(high.churned.precision, 0),
      sub: `из помеченных «высоким» ушли полностью; хотя бы одну услугу потеряли ${formatShare(high.lost_any.precision, 0)}`,
    },
    {
      label: 'Полнота',
      value: formatShare(high.churned.recall, 0),
      sub: 'всех ушедших полностью оказались в «высоком» уровне',
    },
    {
      label: 'Помечено',
      value: formatShare(high.flagged_share, 0),
      sub: countLabel(high.flagged, ['клиент', 'клиента', 'клиентов']) + ' из базы проверки',
    },
  ]
  return (
    <Card data-testid="quality-threshold">
      <CardHeader>
        <CardTitle>Если писать всем с высоким риском</CardTitle>
        <CardDescription>
          Порог «высокий» — score от {formatDecimal(t.high, 0)}. Точность и полнота для цели «ушёл полностью за{' '}
          {q.params.horizon_days} дней»
        </CardDescription>
      </CardHeader>
      <CardContent>
        <dl className="grid grid-cols-3 gap-4">
          {tiles.map((tile) => (
            <div key={tile.label}>
              <dt className="text-[13px] font-medium text-muted-foreground">{tile.label}</dt>
              <dd className="mt-1 text-2xl font-semibold tracking-[-0.02em] num">{tile.value}</dd>
              <dd className="mt-1 text-xs leading-5 text-muted-foreground">{tile.sub}</dd>
            </div>
          ))}
        </dl>
        <p className="mt-4 border-t border-border pt-3 text-[13px] leading-5 text-muted-foreground">
          Если брать и средний уровень (score от {formatDecimal(t.medium, 0)}), в список попадёт{' '}
          {formatShare(mid.flagged_share, 0)} базы, полнота вырастет до {formatShare(mid.churned.recall, 0)}, а точность
          упадёт до {formatShare(mid.churned.precision, 0)}. Пороги меняются на странице модели.
        </p>
      </CardContent>
    </Card>
  )
}

// --- бейзлайны -----------------------------------------------------------------------------------

export function BaselinesCard({ sc }: { sc: QualityScenario }) {
  const r = sc.ranking.churned
  const hard = sc.hard_subset
  const hardModel = hard.ranking.lost_any.model.roc_auc
  const hardRule = hard.ranking.lost_any.autoprolong_off.roc_auc
  return (
    <Card data-testid="quality-baselines">
      <CardHeader>
        <CardTitle>Модель против простых правил</CardTitle>
        <CardDescription>
          Те же клиенты и тот же исход. Правила упорядочивают клиентов по доле выручки без автопродления или в статусе
          «ждёт оплаты»
        </CardDescription>
      </CardHeader>
      <CardContent>
        <div className="overflow-x-auto rounded-lg border border-border">
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Метод</TableHead>
                <TableHead className="text-right">ROC AUC</TableHead>
                <TableHead className="text-right">PR AUC</TableHead>
                <TableHead className="text-right">Точность в топ-5 %</TableHead>
                <TableHead className="text-right">Точность в топ-10 %</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {METHOD_ORDER.map((m) => {
                const row = r[m]
                const best = m === 'model'
                return (
                  <TableRow key={m} className={cn(best && 'bg-accent/50', m === 'segment_oracle' && 'text-muted-foreground')}>
                    <TableCell className={cn(best && 'font-medium')}>{METHOD_LABEL[m]}</TableCell>
                    <TableCell className={cn('text-right num', best && 'font-semibold')}>{formatAuc(row.roc_auc)}</TableCell>
                    <TableCell className="text-right num">{formatAuc(row.pr_auc)}</TableCell>
                    <TableCell className="text-right num">{formatShare(row.precision_top5, 0)}</TableCell>
                    <TableCell className="text-right num">{formatShare(row.precision_top10, 0)}</TableCell>
                  </TableRow>
                )
              })}
            </TableBody>
          </Table>
        </div>
        <div className="mt-4 space-y-2 text-[13px] leading-5 text-muted-foreground">
          <p>
            По AUC модель обходит оба правила. В самом верху списка правило «ждёт оплаты» точнее: услуга, которая уже ждёт
            оплаты без денег на балансе, почти наверняка будет удалена. Но про остальных клиентов это правило ничего не
            говорит, поэтому его AUC ниже.
          </p>
          <p>
            Без клиентов, у которых что-то ждёт оплаты ({formatInt(hard.accounts)}), цель «потерял хотя бы одну услугу»: AUC
            модели {formatAuc(hardModel)} против {formatAuc(hardRule)} у правила «автопродление выключено».
          </p>
        </div>
      </CardContent>
    </Card>
  )
}

// --- калибровка ------------------------------------------------------------------------------------

function CalibrationCard({ sc, colors }: { sc: QualityScenario; colors: Colors }) {
  const rows = calibrationRows(sc)
  return (
    <ChartCard
      testId="quality-calibration"
      title="Прогноз против факта"
      description="Клиенты разбиты на 10 равных групп по score. Доля выручки, которую модель ждёт потерять, и сколько потеряно на самом деле"
      chart={
        <div>
          <div className="mb-3 flex flex-wrap gap-x-5 gap-y-1.5 text-[13px] text-muted-foreground">
            <span className="flex items-center gap-2">
              <LineSwatch color={colors['chart-forecast']} dash="5 4" />
              Прогноз модели
            </span>
            <span className="flex items-center gap-2">
              <LineSwatch color={colors['chart-fact']} />
              Факт в симуляции
            </span>
          </div>
          <div
            className="h-[260px] w-full"
            role="img"
            aria-label="Калибровка по децилям score: прогноз и факт доли потерянной выручки. Таблица — по кнопке «Таблица»."
          >
            <ResponsiveContainer width="100%" height="100%">
              <LineChart data={rows} margin={{ top: 8, right: 12, bottom: 18, left: 0 }}>
                <CartesianGrid vertical={false} stroke={colors['chart-grid']} />
                <XAxis
                  dataKey="group"
                  tick={{ fill: colors['chart-axis'], fontSize: 12 }}
                  tickLine={false}
                  axisLine={{ stroke: colors.border }}
                  label={{ value: 'Группа по score, от низкого к высокому', position: 'insideBottom', offset: -12, fill: colors['chart-axis'], fontSize: 12 }}
                />
                <YAxis
                  domain={[0, 1]}
                  ticks={[0, 0.25, 0.5, 0.75, 1]}
                  tickFormatter={pct}
                  tick={{ fill: colors['chart-axis'], fontSize: 12 }}
                  tickLine={false}
                  axisLine={false}
                  width={44}
                />
                <ChartTooltip
                  isAnimationActive={false}
                  cursor={{ stroke: colors['chart-axis'], strokeOpacity: 0.4 }}
                  content={({ active, payload }) => {
                    const row = active ? (payload?.[0]?.payload as (typeof rows)[number] | undefined) : undefined
                    if (!row) return null
                    return (
                      <Tip
                        title={`Группа ${row.group}, score ${row.label}`}
                        lines={[
                          ['Прогноз', formatShare(row.predicted)],
                          ['Факт', formatShare(row.actual)],
                          ['Клиентов', formatInt(row.n)],
                        ]}
                      />
                    )
                  }}
                />
                <Line dataKey="predicted" stroke={colors['chart-forecast']} strokeDasharray="5 4" strokeWidth={2} dot={{ r: 3 }} isAnimationActive={false} />
                <Line dataKey="actual" stroke={colors['chart-fact']} strokeWidth={2} dot={{ r: 3 }} activeDot={{ r: 5 }} isAnimationActive={false} />
              </LineChart>
            </ResponsiveContainer>
          </div>
        </div>
      }
      table={
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>Группа</TableHead>
              <TableHead>Score</TableHead>
              <TableHead className="text-right">Клиентов</TableHead>
              <TableHead className="text-right">Прогноз</TableHead>
              <TableHead className="text-right">Факт</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {rows.map((r) => (
              <TableRow key={r.group}>
                <TableCell className="num">{r.group}</TableCell>
                <TableCell className="num">{r.label}</TableCell>
                <TableCell className="text-right num">{formatInt(r.n)}</TableCell>
                <TableCell className="text-right num">{formatShare(r.predicted)}</TableCell>
                <TableCell className="text-right num">{formatShare(r.actual)}</TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      }
      footer="Порядок модель держит: чем выше группа, тем больше фактические потери. Абсолютные значения на синтетике не проверяются: в двух сценариях факт расходится с прогнозом в разные стороны, потому что уровень потерь задают параметры симуляции. Поэтому ожидаемую потерю стоит читать как способ сравнить клиентов между собой, а не как точный прогноз выручки в евро."
    />
  )
}

// --- устойчивость --------------------------------------------------------------------------------

export function RobustnessCard({ q }: { q: ModelQuality }) {
  if (q.robustness.length === 0) return null
  return (
    <Card data-testid="quality-robustness">
      <CardHeader>
        <CardTitle>Устойчивость</CardTitle>
        <CardDescription>
          Та же проверка с другим seed (другая база и другое будущее) и с другими параметрами симуляции
        </CardDescription>
      </CardHeader>
      <CardContent>
        <div className="overflow-x-auto rounded-lg border border-border">
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Прогон</TableHead>
                {SCENARIOS.map((k) => (
                  <TableHead key={`${k}-auc`} className="text-right">
                    {SCENARIO_LABEL[k]}: AUC
                  </TableHead>
                ))}
                {SCENARIOS.map((k) => (
                  <TableHead key={`${k}-lift`} className="text-right">
                    {SCENARIO_LABEL[k]}: lift
                  </TableHead>
                ))}
              </TableRow>
            </TableHeader>
            <TableBody>
              {q.robustness.map((row) => (
                <TableRow key={row.label}>
                  <TableCell>{row.label}</TableCell>
                  {SCENARIOS.map((k) => (
                    <TableCell key={`${k}-auc`} className="text-right num">
                      {formatAuc(row.scenarios[k]?.roc_auc)}
                    </TableCell>
                  ))}
                  {SCENARIOS.map((k) => (
                    <TableCell key={`${k}-lift`} className="text-right num">
                      {formatLift(row.scenarios[k]?.churn_lift_high)}
                    </TableCell>
                  ))}
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </div>
        <p className="mt-4 text-[13px] leading-5 text-muted-foreground">
          От seed результат почти не зависит. От темпа живого потока зависит заметно: чем чаще случайные события, тем больше
          потерь, которые на момент T предсказать было нельзя. Прочерк — сценарий в этом прогоне не считался, параметр на
          него не влияет.
        </p>
      </CardContent>
    </Card>
  )
}

// --- живые данные --------------------------------------------------------------------------------

function LiveCard({ summary, q, colors }: { summary: Summary; q: ModelQuality; colors: Colors }) {
  const bins = scoreHistogram(summary.risk_scores)
  const scored = summary.risk_scores?.scored ?? 0
  if (bins.length === 0 || scored === 0) return null
  const levels = (['high', 'medium', 'low'] as const).map((l) => ({ level: l, n: summary.by_level[l] ?? 0 }))
  const testedHigh = levelRow(q.scenarios.stream, 'high')?.share_of_base
  const liveHigh = scored > 0 ? (summary.by_level.high ?? 0) / scored : null
  return (
    <Card data-testid="quality-live">
      <CardHeader>
        <CardTitle>Что модель видит сейчас</CardTitle>
        <CardDescription>
          Распределение risk-score в текущих данных панели. Это не проверка качества: исхода у этих клиентов ещё нет
        </CardDescription>
      </CardHeader>
      <CardContent className="grid gap-6 lg:grid-cols-3">
        <div
          className="h-[200px] lg:col-span-2"
          role="img"
          aria-label={`Распределение risk-score: ${bins.map((b) => `${b.from}–${b.to}: ${b.count}`).join(', ')}`}
        >
          <ResponsiveContainer width="100%" height="100%">
            <BarChart data={bins} margin={{ top: 8, right: 8, bottom: 0, left: 0 }} barCategoryGap={2}>
              <CartesianGrid vertical={false} stroke={colors['chart-grid']} />
              <XAxis
                dataKey="to"
                tickFormatter={(v: number) => `≤${v}`}
                tick={{ fill: colors['chart-axis'], fontSize: 12 }}
                tickLine={false}
                axisLine={{ stroke: colors.border }}
              />
              <YAxis allowDecimals={false} tick={{ fill: colors['chart-axis'], fontSize: 12 }} tickLine={false} axisLine={false} width={44} />
              <ChartTooltip
                cursor={{ fill: colors['chart-grid'], fillOpacity: 0.5 }}
                isAnimationActive={false}
                content={({ active, payload }) => {
                  const b = active ? (payload?.[0]?.payload as (typeof bins)[number] | undefined) : undefined
                  if (!b) return null
                  return <Tip title={`Score ${b.from}–${b.to}`} lines={[['Клиентов', formatInt(b.count)]]} />
                }}
              />
              <Bar dataKey="count" fill={colors['chart-fact']} radius={[4, 4, 0, 0]} isAnimationActive={false} />
            </BarChart>
          </ResponsiveContainer>
        </div>
        <div className="flex flex-col gap-3">
          <ul className="flex flex-col">
            {levels.map(({ level, n }) => (
              <li key={level} className="flex h-9 items-center gap-2.5 text-sm">
                <RiskGlyph level={level} className={cn('size-2.5', RISK_TEXT[level])} />
                <span className="font-medium">{RISK_LABEL_LONG[level]}</span>
                <span className="ml-auto font-semibold num">{formatInt(n)}</span>
                <span className="w-12 text-right text-[13px] text-muted-foreground num">{formatShare(n / scored, 0)}</span>
              </li>
            ))}
          </ul>
          <p className="text-[13px] leading-5 text-muted-foreground">
            Средний score {formatDecimal(summary.risk_scores?.avg, 1)}. В проверке высокий риск был у{' '}
            {formatShare(testedHigh, 0)} базы, сейчас у {formatShare(liveHigh, 0)}.{' '}
            <Link to="/customers?level=high" className="text-link underline-offset-2 hover:underline">
              Открыть список
            </Link>
          </p>
        </div>
      </CardContent>
    </Card>
  )
}

// --- методика и ограничения ------------------------------------------------------------------------

export function MethodCard({ q }: { q: ModelQuality }) {
  const p = q.params
  return (
    <Card data-testid="quality-method">
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          <FlaskConical className="size-4 text-muted-foreground" aria-hidden />
          Как мы проверяли и чего это не доказывает
        </CardTitle>
      </CardHeader>
      <CardContent className="grid gap-6 text-sm leading-6 lg:grid-cols-2">
        <section className="space-y-3">
          <h3 className="font-semibold">Как проверяли</h3>
          <ol className="list-decimal space-y-2 pl-5 text-muted-foreground">
            <li>
              Генератор симулятора создал {formatInt(p.accounts)} клиентов четырёх скрытых сегментов: лояльные, рискованные,
              уходящие и новые. Сегмент в данные не попадает, модель его не видит.
            </li>
            <li>
              Модель посчитала risk-score на момент T с весами и порогами по умолчанию (средний от{' '}
              {formatDecimal(p.thresholds.medium, 0)}, высокий от {formatDecimal(p.thresholds.high, 0)}).
            </li>
            <li>
              Дальше {p.horizon_days} дней будущего прокрутили симуляцией в двух сценариях. «{SCENARIO_LABEL.stream}» — тот же
              код, что на стенде. «{SCENARIO_LABEL.calendar}» — продления строго в день окончания срока.
            </li>
            <li>
              Клиент ушёл, если к концу горизонта удалены все его услуги, которые действовали на T. Сравнили, кого модель
              поставила выше, с тем, кто ушёл на самом деле.
            </li>
          </ol>
          <h3 className="pt-2 font-semibold">Почему этому можно верить и в чём именно</h3>
          <p className="text-muted-foreground">
            Симулятор не читает risk-score и не вызывает модель. Он только списывает деньги, переводит неоплаченные услуги в
            «ждёт оплаты» и удаляет их через 5 дней. Поэтому проверка честно отвечает на вопрос, правильно ли модель
            складывает сигналы и ставит наверх тех, кто действительно теряет услуги. По AUC модель обходит каждое простое правило
            по отдельности и держит результат на других seed.
          </p>
        </section>
        <section className="space-y-3">
          <h3 className="font-semibold">Ограничения</h3>
          <ul className="list-disc space-y-2 pl-5 text-muted-foreground">
            <li>
              <span className="text-foreground">Данные синтетические.</span> Генератор и симулятор писала та же команда, что и
              модель, зная её признаки.
            </li>
            <li>
              <span className="text-foreground">Правила оттока в симуляторе частично совпадают с признаками модели.</span> Весов
              модели симулятор не знает, но услуги теряются через те же механизмы, которые модель читает: выключенное
              автопродление, нехватка денег на балансе, статус «ждёт оплаты». Остальные признаки (история уходов,
              неактивность, бонусы, выводы) на исход в симуляции прямо не влияют.
            </li>
            <li>
              <span className="text-foreground">В сценарии «{SCENARIO_LABEL.calendar}» есть утечка через сегмент.</span> Скрытое
              поведение задано по сегменту, а сегмент заодно сформировал признаки клиента. Поэтому AUC скрытого сегмента
              почти равен AUC модели. Вероятности поведения — наше допущение, ни на чём не откалиброванное.
            </li>
            <li>
              <span className="text-foreground">Живой поток не смотрит на дату окончания срока</span> и иногда удаляет услугу,
              оплаченную дальше горизонта. Для модели этот сценарий скорее пессимистичен.
            </li>
            <li>
              <span className="text-foreground">Нет обратной связи.</span> В симуляции клиентам никто не пишет. В жизни по
              высокому риску будут работать, и доля ушедших среди них снизится.
            </li>
          </ul>
          <h3 className="pt-2 font-semibold">Как перенести на реальные данные</h3>
          <p className="text-muted-foreground">
            Настоящая проверка возможна только на истории статусов реального хостинга. Скрипт{' '}
            <code className="text-foreground">python -m tools.calibration</code> для этого почти готов: вместо симуляции
            нужно подставить фактические статусы услуг через {p.horizon_days} дней. Вес каждого признака и пороги уровней
            администратор меняет на{' '}
            <Link to="/model" className="text-link underline-offset-2 hover:underline">
              странице модели
            </Link>
            , после чего проверку стоит повторить.
          </p>
          <p className="text-xs text-subtle-foreground num">
            Версия модели {p.riskcore_version}, исходники {p.riskcore_sources}, симулятор {p.simulator_sources}. Подробно:
            docs/calibration.md в репозитории.
          </p>
        </section>
      </CardContent>
    </Card>
  )
}

// --- страница ------------------------------------------------------------------------------------

function QualitySkeleton() {
  return (
    <div className="space-y-4" aria-busy="true" aria-label="Загружаем результаты проверки">
      <div className="grid grid-cols-2 gap-3 sm:gap-4 xl:grid-cols-4">
        {Array.from({ length: 4 }, (_, i) => (
          <Skeleton key={i} className="h-32 rounded-xl" />
        ))}
      </div>
      <div className="grid gap-4 lg:grid-cols-2">
        <Skeleton className="h-[360px] rounded-xl" />
        <Skeleton className="h-[360px] rounded-xl" />
      </div>
    </div>
  )
}

export function QualityBody({
  q,
  summary,
  drift,
}: {
  q: ModelQuality
  summary?: Summary
  drift: string[]
}) {
  const [scenario, setScenario] = useState<ScenarioKey>('stream')
  const colors = useCssVars(CHART_VARS)
  const sc = q.scenarios[scenario]
  return (
    <div className="space-y-4">
      <Notices q={q} drift={drift} />
      <QualityHeadline q={q} />
      <div className="flex flex-wrap items-center gap-x-4 gap-y-2 pt-2">
        <Segmented
          label="Сценарий будущего"
          value={scenario}
          onChange={setScenario}
          options={SCENARIOS.map((k) => [k, SCENARIO_LABEL[k]] as const)}
        />
        <p className="flex max-w-3xl items-start gap-2 text-[13px] leading-5 text-muted-foreground">
          <Info className="mt-0.5 size-3.5 shrink-0" aria-hidden />
          {SCENARIO_HINT[scenario]}
        </p>
      </div>
      <div className="grid gap-4 lg:grid-cols-2">
        <LiftCard sc={sc} colors={colors} />
        <RocCard sc={sc} colors={colors} />
      </div>
      <div className="grid gap-4 lg:grid-cols-2">
        <ThresholdCard sc={sc} q={q} />
        <CalibrationCard sc={sc} colors={colors} />
      </div>
      <BaselinesCard sc={sc} />
      <RobustnessCard q={q} />
      {summary && <LiveCard summary={summary} q={q} colors={colors} />}
      <MethodCard q={q} />
    </div>
  )
}

export default function ModelQualityPage() {
  const quality = useQuery({
    queryKey: queryKeys.modelQuality,
    queryFn: ({ signal }) => api.modelQuality(signal),
    staleTime: Infinity,
  })
  const summary = useQuery({ queryKey: queryKeys.summary, queryFn: ({ signal }) => api.summary(signal) })
  const settings = useQuery({ queryKey: queryKeys.settings, queryFn: ({ signal }) => api.settings(signal) })
  const q = quality.data
  const drift = useMemo(() => (q ? settingsDrift(q, settings.data) : []), [q, settings.data])

  return (
    <>
      <PageHeader
        title="Качество модели"
        description="Насколько модели можно верить. Проверили на синтетических клиентах: посчитали риск на момент T и прокрутили 30 дней вперёд симулятором. Здесь всё, что получилось, и всё, чего такая проверка не доказывает."
      />
      {quality.isPending ? (
        <QualitySkeleton />
      ) : quality.isError ? (
        <Card>
          <ErrorState
            error={quality.error}
            title="Результаты проверки недоступны"
            onRetry={() => void quality.refetch()}
          />
        </Card>
      ) : q ? (
        <QualityBody q={q} summary={summary.data} drift={drift} />
      ) : null}
    </>
  )
}

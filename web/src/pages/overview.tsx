import { useQuery } from '@tanstack/react-query'
import { ArrowRight, Info, ListChecks } from 'lucide-react'
import type { ReactNode } from 'react'
import { Link } from 'react-router'
import { api, queryKeys } from '@/api/endpoints'
import type { RiskLevel, Summary } from '@/api/types'
import { AnimatedNumber } from '@/components/animated-number'
import { ChurnChart } from '@/components/churn-chart'
import { PageHeader, RecalcNote } from '@/components/page-header'
import { RISK_BAR, RiskGlyph, RISK_TEXT } from '@/components/risk'
import { EmptyState, ErrorState } from '@/components/states'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Skeleton } from '@/components/ui/skeleton'
import { Tooltip } from '@/components/ui/tooltip'
import { countLabel, formatInt, formatMoney, formatShare, plural } from '@/lib/format'
import { RISK_LABEL_LONG } from '@/lib/labels'
import { RISK_LEVELS } from '@/lib/url-state'
import { cn } from '@/lib/utils'

const money = (v: number | null | undefined) => formatMoney(v, { cents: false })

function KpiCard({
  label,
  value,
  sub,
  info,
  emphasis,
  testId,
}: {
  label: string
  value: ReactNode
  sub?: ReactNode
  info?: ReactNode
  emphasis?: boolean
  testId?: string
}) {
  return (
    <Card className="flex flex-col px-5 py-4" data-testid={testId}>
      <div className="flex items-center gap-1.5 text-[13px] font-medium text-muted-foreground">
        {emphasis && <RiskGlyph level="high" className="text-risk-high" />}
        <span>{label}</span>
        {info && (
          <Tooltip content={info}>
            <button
              type="button"
              className="ml-auto -mr-1 inline-flex size-6 items-center justify-center rounded text-subtle-foreground outline-none hover:text-foreground focus-visible:ring-2 focus-visible:ring-ring"
              aria-label={`Что такое «${label}»`}
            >
              <Info className="size-3.5" />
            </button>
          </Tooltip>
        )}
      </div>
      <div className="mt-2 text-[28px] leading-none font-semibold tracking-[-0.025em] num" data-role="value">
        {value}
      </div>
      {sub && <div className="mt-2.5 line-clamp-2 text-[13px] leading-5 text-muted-foreground">{sub}</div>}
    </Card>
  )
}

function KpiSkeleton() {
  return (
    <Card className="px-5 py-4">
      <Skeleton className="h-4 w-36" />
      <Skeleton className="mt-3 h-7 w-28" />
      <Skeleton className="mt-3 h-4 w-44" />
    </Card>
  )
}

function KpiRow({ s }: { s: Summary }) {
  const total = s.customers_total
  const high = s.by_level.high
  const share = typeof s.revenue_at_risk_share === 'number' ? s.revenue_at_risk_share : null
  const horizon = s.horizon_days
  const horizonText = `${horizon} ${plural(horizon, ['день', 'дня', 'дней'])}`

  return (
    <div className="grid grid-cols-2 gap-4 xl:grid-cols-4">
      <KpiCard
        label="Клиенты под высоким риском"
        testId="kpi-high"
        emphasis
        value={<AnimatedNumber value={high} format={formatInt} />}
        sub={total > 0 ? `${formatShare(high / total, 0)} от ${countLabel(total, ['клиента', 'клиентов', 'клиентов'])}` : 'Клиентов пока нет'}
        info="Клиенты, у которых risk-score выше порога высокого риска. Порог задаётся в настройках модели."
      />
      <KpiCard
        label={`Ожидаемая потеря за ${horizonText}`}
        testId="kpi-expected-loss"
        value={<AnimatedNumber value={s.expected_loss} format={money} />}
        sub={<span title={s.money_explanation}>{s.money_explanation || 'Сумма с учётом вероятности ухода и покрытия балансом.'}</span>}
        info={
          <span>
            {s.money_explanation || 'Сколько выручки мы, вероятно, потеряем, если ничего не делать.'}
            <span className="mt-1.5 block text-muted-foreground">
              Учитывает цены услуг под риском, вероятность непродления и то, что уже покрыто балансом клиента.
            </span>
          </span>
        }
      />
      <KpiCard
        label="Максимальная экспозиция"
        value={<AnimatedNumber value={s.exposure} format={money} />}
        sub={s.services_at_risk > 0 ? `Если уйдут все ${countLabel(s.services_at_risk, ['услуга', 'услуги', 'услуг'])} под риском` : 'Услуг под риском нет'}
        info="Худший сценарий: сколько выручки за горизонт прогноза потеряем, если не продлится ни одна услуга под риском."
      />
      <KpiCard
        label="Доля выручки под риском"
        value={share === null ? '—' : <AnimatedNumber value={share} format={(v) => formatShare(v)} />}
        sub={`Месячная выручка ${money(s.monthly_revenue_total)}`}
        info="Какая часть месячной выручки приходится на услуги клиентов со средним и высоким риском."
      />
    </div>
  )
}

function LevelDistribution({ s }: { s: Summary }) {
  const total = RISK_LEVELS.reduce((acc, l) => acc + (s.by_level[l] ?? 0), 0)
  return (
    <Card className="flex flex-col">
      <CardHeader>
        <CardTitle>Клиенты по уровню риска</CardTitle>
        <CardDescription>{total > 0 ? `Всего ${countLabel(total, ['клиент', 'клиента', 'клиентов'])}` : 'Клиентов пока нет'}</CardDescription>
      </CardHeader>
      <CardContent className="flex flex-1 flex-col">
        <div className="flex h-2.5 w-full gap-0.5 overflow-hidden rounded-full bg-muted" aria-hidden>
          {total > 0 &&
            RISK_LEVELS.map((l) => {
              const v = s.by_level[l] ?? 0
              return v > 0 ? (
                <div
                  key={l}
                  className={cn('h-full transition-[flex-grow] duration-500 ease-out first:rounded-l-full last:rounded-r-full', RISK_BAR[l])}
                  style={{ flexGrow: v, flexBasis: 0 }}
                />
              ) : null
            })}
        </div>
        <ul className="mt-4 mb-4 flex flex-col">
          {RISK_LEVELS.map((l) => (
            <LevelRow key={l} level={l} count={s.by_level[l] ?? 0} total={total} />
          ))}
        </ul>
        <dl className="mt-auto grid grid-cols-2 gap-3 border-t border-border pt-4">
          <div>
            <dt className="text-xs text-muted-foreground">Услуги под риском</dt>
            <dd className="mt-0.5 text-[15px] font-semibold num">
              {formatInt(s.services_at_risk)}
              <span className="font-normal text-muted-foreground"> из {formatInt(s.services_total)}</span>
            </dd>
          </div>
          <div>
            <dt className="text-xs text-muted-foreground">Выручка в месяц</dt>
            <dd className="mt-0.5 text-[15px] font-semibold num">{money(s.monthly_revenue_total)}</dd>
          </div>
        </dl>
      </CardContent>
    </Card>
  )
}

function LevelRow({ level, count, total }: { level: RiskLevel; count: number; total: number }) {
  return (
    <li>
      <Link
        to={`/customers?level=${level}`}
        className="group -mx-2 flex h-10 items-center gap-2.5 rounded-md px-2 text-sm outline-none hover:bg-accent focus-visible:ring-2 focus-visible:ring-ring"
      >
        <RiskGlyph level={level} className={cn('size-2.5', RISK_TEXT[level])} />
        <span className="font-medium">{RISK_LABEL_LONG[level]}</span>
        <span className="ml-auto font-semibold num">
          <AnimatedNumber value={count} format={formatInt} />
        </span>
        <span className="w-12 text-right text-[13px] text-muted-foreground num">
          {total > 0 ? formatShare(count / total, 0) : '—'}
        </span>
        <ArrowRight className="size-3.5 text-subtle-foreground opacity-0 transition-opacity group-hover:opacity-100 group-focus-visible:opacity-100" />
      </Link>
    </li>
  )
}

function TopReasons({ s }: { s: Summary }) {
  const reasons = s.top_reasons ?? []
  const max = Math.max(1, ...reasons.map((r) => r.customers))
  return (
    <Card>
      <CardHeader>
        <CardTitle>Главные причины риска</CardTitle>
        <CardDescription>Что чаще всего толкает клиентов к уходу</CardDescription>
      </CardHeader>
      <CardContent>
        {reasons.length === 0 ? (
          <EmptyState
            icon={<ListChecks />}
            title="Причин риска не видно"
            description="Сейчас ни у одного клиента нет заметных сигналов ухода."
            className="py-8"
          />
        ) : (
          <ol className="grid gap-x-8 gap-y-1 lg:grid-cols-2">
            {reasons.slice(0, 10).map((r, i) => (
              <li key={`${r.code}-${i}`} className="flex items-center gap-3 border-b border-border py-2.5 last:border-0 lg:[&:nth-last-child(2):nth-child(odd)]:border-0">
                <span className="w-5 shrink-0 text-right text-[13px] text-subtle-foreground num">{i + 1}</span>
                <div className="min-w-0 flex-1">
                  <p className="truncate text-sm" title={r.title}>
                    {r.title}
                  </p>
                  <div className="mt-1.5 h-1 w-full overflow-hidden rounded-full bg-muted">
                    <div
                      className="h-full rounded-full bg-foreground/35 transition-[width] duration-500"
                      style={{ width: `${(r.customers / max) * 100}%` }}
                    />
                  </div>
                </div>
                <div className="shrink-0 text-right">
                  <p className="text-sm font-medium num">{countLabel(r.customers, ['клиент', 'клиента', 'клиентов'])}</p>
                  {typeof r.expected_loss === 'number' && (
                    <p className="text-xs text-muted-foreground num">{money(r.expected_loss)}</p>
                  )}
                </div>
              </li>
            ))}
          </ol>
        )}
      </CardContent>
    </Card>
  )
}

function OverviewSkeleton() {
  return (
    <div className="space-y-4" aria-busy="true" aria-label="Загружаем сводку">
      <div className="grid grid-cols-2 gap-4 xl:grid-cols-4">
        {Array.from({ length: 4 }, (_, i) => (
          <KpiSkeleton key={i} />
        ))}
      </div>
      <div className="grid gap-4 lg:grid-cols-3">
        <Skeleton className="h-[380px] rounded-xl lg:col-span-2" />
        <Skeleton className="h-[380px] rounded-xl" />
      </div>
      <Skeleton className="h-56 rounded-xl" />
    </div>
  )
}

export default function OverviewPage() {
  const summary = useQuery({ queryKey: queryKeys.summary, queryFn: ({ signal }) => api.summary(signal) })
  const s = summary.data

  return (
    <>
      <PageHeader
        title="Обзор"
        description="Кто может уйти, почему и сколько денег под угрозой."
        meta={s && <RecalcNote scoredAt={s.scored_at} modelVersion={s.model_version} />}
      />
      {summary.isPending ? (
        <OverviewSkeleton />
      ) : summary.isError ? (
        <Card>
          <ErrorState error={summary.error} onRetry={() => void summary.refetch()} />
        </Card>
      ) : s ? (
        <div className="space-y-4">
          <KpiRow s={s} />
          <div className="grid gap-4 lg:grid-cols-3">
            <Card className="lg:col-span-2">
              <CardHeader>
                <CardTitle>Отток услуг: факт и прогноз</CardTitle>
                <CardDescription>
                  Сколько услуг ушло по дням и сколько непродлений ожидаем в ближайшие {s.horizon_days}{' '}
                  {plural(s.horizon_days, ['день', 'дня', 'дней'])}
                </CardDescription>
              </CardHeader>
              <CardContent>
                <ChurnChart timeline={s.timeline ?? []} horizonDays={s.horizon_days} />
              </CardContent>
            </Card>
            <LevelDistribution s={s} />
          </div>
          <TopReasons s={s} />
        </div>
      ) : null}
    </>
  )
}

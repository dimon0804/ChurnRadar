import { useQuery } from '@tanstack/react-query'
import { ArrowRight, Info, ListChecks } from 'lucide-react'
import { Link } from 'react-router'
import { api, queryKeys } from '@/api/endpoints'
import type { RiskLevel, Summary } from '@/api/types'
import { AnimatedNumber } from '@/components/animated-number'
import { ContactTodayCard } from '@/components/contact-today'
import { ChurnChart } from '@/components/churn-chart'
import { DataClockBadge, useDataClock } from '@/components/data-clock'
import { KpiCard, KpiSkeleton } from '@/components/kpi-card'
import { PageHeader, RecalcNote } from '@/components/page-header'
import { RISK_BAR, RiskGlyph, RISK_TEXT } from '@/components/risk'
import { EmptyState, ErrorState } from '@/components/states'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Skeleton } from '@/components/ui/skeleton'
import { countLabel, formatInt, formatMoney, formatShare, plural } from '@/lib/format'
import { RISK_LABEL_LONG } from '@/lib/labels'
import { RISK_LEVELS } from '@/lib/url-state'
import { cn } from '@/lib/utils'

const money = (v: number | null | undefined) => formatMoney(v, { cents: false })

function KpiRow({ s }: { s: Summary }) {
  const total = s.customers_total
  const high = s.by_level.high
  const share = typeof s.revenue_at_risk_share === 'number' ? s.revenue_at_risk_share : null
  const horizon = s.horizon_days
  const horizonText = `${horizon} ${plural(horizon, ['день', 'дня', 'дней'])}`

  return (
    <div className="space-y-3">
      <div className="grid grid-cols-2 gap-3 sm:gap-4 xl:grid-cols-4">
        <KpiCard
          label="Клиенты под высоким риском"
          testId="kpi-high"
          emphasis
          watch={high}
          value={<AnimatedNumber value={high} format={formatInt} />}
          sub={total > 0 ? `${formatShare(high / total, 0)} от ${countLabel(total, ['клиента', 'клиентов', 'клиентов'])}` : 'Клиентов пока нет'}
          info="Клиенты, у которых risk-score выше порога высокого риска. Порог задаётся в настройках модели."
        />
        <KpiCard
          label={`Ожидаемая потеря за ${horizonText}`}
          testId="kpi-expected-loss"
          watch={s.expected_loss}
          value={<AnimatedNumber value={s.expected_loss} format={money} />}
          sub="С учётом вероятности ухода и денег на балансе клиентов"
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
          watch={s.exposure}
          value={<AnimatedNumber value={s.exposure} format={money} />}
          sub={s.services_at_risk > 0 ? `Если уйдут все ${countLabel(s.services_at_risk, ['услуга', 'услуги', 'услуг'])} под риском` : 'Услуг под риском нет'}
          info="Худший сценарий: сколько выручки за горизонт прогноза потеряем, если не продлится ни одна услуга под риском."
        />
        <KpiCard
          label="Доля выручки под риском"
          watch={share}
          value={share === null ? '—' : <AnimatedNumber value={share} format={(v) => formatShare(v)} />}
          sub={`Месячная выручка ${money(s.monthly_revenue_total)}`}
          info="Какая часть месячной выручки приходится на услуги клиентов со средним и высоким риском."
        />
      </div>
      {s.money_explanation && (
        <div className="flex items-start gap-3 rounded-xl border border-border bg-card px-5 py-3.5 shadow-card" data-testid="money-explanation">
          <Info className="mt-1 size-4 shrink-0 text-subtle-foreground" aria-hidden />
          <p className="text-sm leading-6 text-muted-foreground">
            <span className="font-medium text-foreground">Как считаем ожидаемую потерю. </span>
            {s.money_explanation}
          </p>
        </div>
      )}
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
  // причины, которые не главные ни у одного клиента, дают «0 клиентов · €3» и выглядят как ошибка
  const reasons = (s.top_reasons ?? []).map((r) => ({ ...r, count: r.customers })).filter((r) => r.count > 0)
    .slice(0, 10)
  const max = Math.max(1, ...reasons.map((r) => r.count))
  const half = Math.ceil(reasons.length / 2)
  return (
    <Card>
      <CardHeader>
        <CardTitle>Главные причины риска</CardTitle>
        <CardDescription>Что чаще всего толкает клиентов к уходу и сколько денег за этим стоит</CardDescription>
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
          // на широком экране — две колонки, но читаются сверху вниз: 1–5 слева, 6–10 справа
          <ol
            className="grid gap-x-8 lg:grid-flow-col lg:grid-cols-2"
            style={{ gridTemplateRows: `repeat(${half}, auto)` }}
          >
            {reasons.map((r, i) => (
              <li
                key={`${r.code}-${i}`}
                className={cn('flex items-center gap-3 border-b border-border py-2.5', (i === reasons.length - 1 || i === half - 1) && 'lg:border-0', i === reasons.length - 1 && 'border-0')}
              >
                <span className="w-5 shrink-0 text-right text-[13px] text-subtle-foreground num">{i + 1}</span>
                <div className="min-w-0 flex-1">
                  <p className="line-clamp-2 text-sm break-words sm:line-clamp-1" title={r.title}>
                    {r.title}
                  </p>
                  <div className="mt-1.5 h-1 w-full overflow-hidden rounded-full bg-muted">
                    <div
                      className="h-full rounded-full bg-foreground/35 transition-[width] duration-500"
                      style={{ width: `${(r.count / max) * 100}%` }}
                    />
                  </div>
                </div>
                <div className="max-w-[45%] shrink-0 text-right">
                  <p className="text-sm font-medium num">{countLabel(r.count, ['клиент', 'клиента', 'клиентов'])}</p>
                  <p className="text-xs text-muted-foreground num">
                    {[
                      typeof r.primary_customers === 'number' && r.primary_customers > 0
                        ? `главная у ${countLabel(r.primary_customers, ['клиента', 'клиентов', 'клиентов'])}`
                        : null,
                      typeof r.expected_loss === 'number' ? money(r.expected_loss) : null,
                    ]
                      .filter(Boolean)
                      .join(' · ')}
                  </p>
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
      <div className="grid grid-cols-2 gap-3 sm:gap-4 xl:grid-cols-4">
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
  const clock = useDataClock()

  return (
    <>
      <PageHeader
        title="Обзор"
        description="Кто может уйти, почему и сколько денег под угрозой."
        meta={
          s && (
            <div className="flex flex-wrap items-center justify-end gap-x-3 gap-y-2">
              <DataClockBadge clock={clock} />
              <RecalcNote scoredAt={s.scored_at} modelVersion={s.model_version} />
            </div>
          )
        }
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
          <ContactTodayCard />
          <div className="grid gap-4 lg:grid-cols-3">
            <Card className="lg:col-span-2">
              <CardHeader>
                <CardTitle>Отток услуг: факт и прогноз</CardTitle>
                <CardDescription>
                  Сколько услуг ушло по дням, сколько непродлений ожидаем в ближайшие {s.horizon_days}{' '}
                  {plural(s.horizon_days, ['день', 'дня', 'дней'])} и сколько услуг остаётся под риском
                </CardDescription>
              </CardHeader>
              <CardContent>
                <ChurnChart timeline={s.timeline ?? []} horizonDays={s.horizon_days} deletedServices={s.deleted_services} />
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

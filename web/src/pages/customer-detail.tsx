import { useQuery } from '@tanstack/react-query'
import { CheckCircle2, CircleHelp, Lightbulb, PiggyBank, RefreshCcw, RefreshCwOff, Target, UserX } from 'lucide-react'
import { useMemo, useState, type ReactNode } from 'react'
import { Link, useLocation, useNavigate, useParams } from 'react-router'
import { isApiError } from '@/api/client'
import { api, queryKeys } from '@/api/endpoints'
import type { CustomerDetail, FeatureContribution, ServiceRisk, Transaction } from '@/api/types'
import { CustomerActionPanel } from '@/components/customer-action'
import { DataClockBadge, referenceOf, useDataClock } from '@/components/data-clock'
import { RiskBadge, RISK_BAR, RISK_TEXT, riskLevelOf } from '@/components/risk'
import { EmptyState, ErrorState } from '@/components/states'
import { Button } from '@/components/ui/button'
import { Sheet, SheetContent, SheetDescription, SheetTitle } from '@/components/ui/sheet'
import { Skeleton } from '@/components/ui/skeleton'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table'
import { Tooltip } from '@/components/ui/tooltip'
import {
  countLabel,
  formatDate,
  formatDateTime,
  formatDaysToExpiry,
  formatDecimal,
  formatMoney,
  formatPoints,
  formatScore,
  formatShare,
  plural,
} from '@/lib/format'
import { formatMonths, formatTenure } from '@/lib/marketing'
import {
  labelOf,
  PAYMENT_TERM_LABEL,
  SERVICE_STATUS_LABEL,
  TX_STATUS_LABEL,
  TX_TYPE_LABEL,
} from '@/lib/labels'
import { clockFromReference, clockFromServices } from '@/lib/data-clock'
import { isChurned, shortReasonTitle } from '@/lib/reasons'
import { cn } from '@/lib/utils'
import { useLive } from '@/providers/live'

function Section({ title, aside, children, className }: { title: string; aside?: ReactNode; children: ReactNode; className?: string }) {
  return (
    <section className={cn('px-6 py-5', className)}>
      <div className="mb-3 flex items-baseline justify-between gap-3">
        <h3 className="text-[13px] font-semibold tracking-wide text-muted-foreground uppercase">{title}</h3>
        {aside}
      </div>
      {children}
    </section>
  )
}

function Stat({ label, value, hint, strong }: { label: string; value: ReactNode; hint?: ReactNode; strong?: boolean }) {
  return (
    <div className="min-w-0 rounded-lg border border-border bg-card px-3.5 py-3">
      <p className="truncate text-xs text-muted-foreground">{label}</p>
      <p className={cn('mt-1 truncate text-[17px] tracking-[-0.01em] num', strong ? 'font-semibold' : 'font-medium')}>{value}</p>
      {hint && <p className="mt-0.5 truncate text-xs text-subtle-foreground">{hint}</p>}
    </div>
  )
}

function Hero({ c }: { c: CustomerDetail }) {
  const level = riskLevelOf(c.risk_level)
  const unknown = level === 'unknown' || c.risk_score === null
  if (unknown && isChurned(c)) {
    return (
      <div className="flex gap-4 rounded-xl border border-dashed border-border bg-card p-5">
        <div className="flex size-11 shrink-0 items-center justify-center rounded-full bg-risk-unknown-bg text-risk-unknown">
          <UserX className="size-5" />
        </div>
        <div className="min-w-0">
          <p className="text-[17px] font-semibold">Клиент ушёл</p>
          <p className="mt-1 text-sm leading-6 text-muted-foreground">
            Все его услуги удалены или заблокированы, поэтому риск ухода уже не считаем. Это кандидат на возврат.
          </p>
        </div>
      </div>
    )
  }
  if (unknown) {
    const raw = c.reasons?.[0]?.title?.trim()
    const stripped = raw ? raw.replace(/^оценки нет\s*[:.—-]\s*/i, '') : ''
    const why = stripped ? stripped.charAt(0).toUpperCase() + stripped.slice(1) : undefined
    return (
      <div className="flex gap-4 rounded-xl border border-dashed border-border bg-card p-5">
        <div className="flex size-11 shrink-0 items-center justify-center rounded-full bg-risk-unknown-bg text-risk-unknown">
          <CircleHelp className="size-5" />
        </div>
        <div className="min-w-0">
          <p className="text-[17px] font-semibold">Оценки нет{why ? ':' : ''}</p>
          <p className="mt-1 text-sm leading-6 text-muted-foreground">
            {why ?? 'По этому клиенту пока недостаточно данных, чтобы честно оценить риск ухода.'}
          </p>
        </div>
      </div>
    )
  }
  return (
    <div className="grid gap-4 sm:grid-cols-[auto_1fr]">
      <div className="flex items-center gap-4 rounded-xl border border-border bg-card px-5 py-4">
        <div>
          <p className="text-xs text-muted-foreground">Риск ухода</p>
          <p className={cn('mt-0.5 flex items-baseline gap-1 num', RISK_TEXT[level])}>
            <span className="text-[44px] leading-none font-semibold tracking-[-0.03em]">{formatScore(c.risk_score)}</span>
            <span className="text-sm text-muted-foreground">/100</span>
          </p>
        </div>
        <div className="flex flex-col items-start gap-2">
          <RiskBadge level={level} size="md" />
          {typeof c.loyalty === 'number' && (
            <Tooltip content="Обратная шкала: чем выше, тем надёжнее клиент.">
              <span tabIndex={0} className="text-xs text-muted-foreground num outline-none">
                лояльность {formatScore(c.loyalty)}
              </span>
            </Tooltip>
          )}
        </div>
      </div>
      <div className="flex flex-col justify-center rounded-xl border border-border bg-card px-5 py-4">
        <p className="text-xs text-muted-foreground">Ожидаемая потеря</p>
        <p className="mt-0.5 text-[28px] leading-tight font-semibold tracking-[-0.02em] num">{formatMoney(c.money?.expected_loss ?? c.expected_loss)}</p>
        <p className="mt-1 text-xs text-muted-foreground num">
          максимум {formatMoney(c.money?.exposure ?? c.exposure)} · под риском {c.services_at_risk} из{' '}
          {countLabel(c.services_total, ['услуги', 'услуг', 'услуг'])}
        </p>
      </div>
    </div>
  )
}

function Reasons({ c, services }: { c: CustomerDetail; services: Map<number, ServiceRisk> }) {
  const reasons = (c.reasons ?? []).filter((r) => c.risk_level === 'unknown' || r.contribution > 0)
  const titles = useMemo(() => new Map((c.features ?? []).map((f) => [f.code, f.title] as const)), [c.features])
  if (c.risk_level === 'unknown') return null
  return (
    <Section title="Почему">
      {reasons.length === 0 ? (
        <p className="text-sm text-muted-foreground">Заметных сигналов ухода нет.</p>
      ) : (
        <ul className="space-y-2">
          {reasons.map((r, i) => {
            const svc = r.service_id ? services.get(r.service_id) : undefined
            return (
              <li key={`${r.code}-${r.service_id ?? ''}-${i}`} className="flex items-start gap-3 rounded-lg border border-border bg-card px-3.5 py-2.5">
                <span className={cn('mt-[7px] size-1.5 shrink-0 rounded-full', RISK_BAR[riskLevelOf(c.risk_level)])} aria-hidden />
                <div className="min-w-0 flex-1">
                  <p className="text-sm leading-6 font-medium">{shortReasonTitle(r, titles)}</p>
                  <p className="text-sm leading-6 text-muted-foreground">{r.title}</p>
                  {svc && <p className="truncate text-xs text-muted-foreground">Услуга: {svc.name}</p>}
                </div>
                <span className="shrink-0 rounded-md bg-muted px-1.5 py-0.5 text-xs font-medium text-muted-foreground num">
                  {formatPoints(r.contribution)} п.
                </span>
              </li>
            )
          })}
        </ul>
      )}
    </Section>
  )
}

function Features({ features, level }: { features: FeatureContribution[]; level: ReturnType<typeof riskLevelOf> }) {
  const [showAll, setShowAll] = useState(false)
  const sorted = useMemo(() => [...features].sort((a, b) => b.contribution - a.contribution), [features])
  const active = sorted.filter((f) => Math.abs(f.contribution) >= 0.05)
  const idle = sorted.length - active.length
  const list = showAll ? sorted : active
  const max = Math.max(1, ...sorted.map((f) => Math.abs(f.contribution)))
  if (sorted.length === 0) return null

  return (
    <Section
      title="Из чего сложилась оценка"
      aside={<span className="text-xs text-subtle-foreground">вклад в score, пунктов</span>}
    >
      {list.length === 0 && <p className="text-sm text-muted-foreground">Ни один признак не добавил риска.</p>}
      <ul className="space-y-3">
        {list.map((f) => (
          <li key={f.code}>
            <div className="flex items-baseline justify-between gap-3">
              <span className="min-w-0 truncate text-sm" title={f.explanation || f.title}>
                {f.title}
              </span>
              <span className={cn('shrink-0 text-sm font-medium num', f.contribution < 0.05 && 'text-muted-foreground')}>
                {formatPoints(f.contribution)}
              </span>
            </div>
            <div className="mt-1.5 h-2 w-full overflow-hidden rounded-full bg-muted">
              <div
                className={cn('h-full rounded-full transition-[width] duration-500 ease-out', f.contribution > 0 ? RISK_BAR[level] : 'bg-risk-low-bar')}
                style={{ width: `${(Math.abs(f.contribution) / max) * 100}%` }}
              />
            </div>
          </li>
        ))}
      </ul>
      {idle > 0 && (
        <button
          type="button"
          onClick={() => setShowAll((v) => !v)}
          className="mt-3 rounded text-[13px] font-medium text-link outline-none hover:underline focus-visible:ring-2 focus-visible:ring-ring"
        >
          {showAll ? 'Скрыть признаки без вклада' : `Ещё ${countLabel(idle, ['признак', 'признака', 'признаков'])} без вклада`}
        </button>
      )}
    </Section>
  )
}

function coverageText(days: number): string {
  if (days < 1) return 'меньше дня'
  const whole = Math.floor(days)
  return `${formatDecimal(whole, 0)} ${plural(whole, ['день', 'дня', 'дней'])}`
}

function Money({ c }: { c: CustomerDetail }) {
  const m = c.money
  const days = m?.balance_coverage_days
  return (
    <Section title="Деньги">
      <div className="grid grid-cols-2 gap-2.5 md:grid-cols-4">
        <Stat label="Выручка в месяц" value={formatMoney(m?.monthly_revenue ?? c.monthly_revenue)} />
        <Stat label="Ожидаемая потеря" value={formatMoney(m?.expected_loss ?? c.expected_loss)} strong />
        <Stat label="Максимум потерь" value={formatMoney(m?.exposure ?? c.exposure)} />
        <Stat
          label="Баланса хватит"
          value={typeof days === 'number' && Number.isFinite(days) ? coverageText(days) : '—'}
          hint={m?.funds_run_out_at ? `до ${formatDate(m.funds_run_out_at)}` : undefined}
        />
      </div>
      {m?.explanation && <p className="mt-3 text-sm leading-6 text-muted-foreground">{m.explanation}</p>}
      <dl className="mt-4 grid grid-cols-2 gap-x-6 gap-y-2 text-sm md:grid-cols-4">
        {(
          [
            ['Баланс', c.balance],
            ['Бонусы', c.bonus_balance],
            ['Доступно к выводу', c.withdraw_balance],
            ['Пополнено всего', c.total_replenished],
          ] as const
        ).map(([label, value]) => (
          <div key={label} className="min-w-0">
            <dt className="truncate text-xs text-muted-foreground">
              {label}
              {label === 'Бонусы' && c.bonus_state === 'locked' ? ' (заблокированы)' : ''}
            </dt>
            <dd className="mt-0.5 font-medium num">{formatMoney(value)}</dd>
          </div>
        ))}
      </dl>
    </Section>
  )
}

/** Маркетинговые показатели клиента: его MRR, ценность (LTV) по прогнозу и по факту, срок с нами. */
function MarketingStats({ c }: { c: CustomerDetail }) {
  const m = c.marketing
  if (!m) return null
  const margin = typeof m.margin === 'number' && m.margin < 1 ? `, маржа ${formatShare(m.margin, 0)}` : ''
  return (
    <Section
      title="Маркетинг"
      aside={
        <Link to="/marketing" className="text-xs text-link underline-offset-4 hover:underline">
          Что это значит
        </Link>
      }
    >
      <div className="grid grid-cols-2 gap-2.5 md:grid-cols-4">
        <Stat label="MRR (выручка в месяц)" value={formatMoney(m.mrr, { cents: false })} />
        <Stat
          label="LTV прогноз"
          value={m.monthly_churn === null ? '—' : formatMoney(m.ltv_forecast, { cents: false })}
          hint={
            m.expected_lifetime_months !== null
              ? `ещё ${formatMonths(m.expected_lifetime_months, m.lifetime_capped)}${margin}`
              : m.left_at
                ? 'клиент уже ушёл'
                : 'нет оценки риска'
          }
          strong
        />
        <Stat
          label="LTV факт (уже принёс)"
          value={formatMoney(m.ltv_fact, { cents: false })}
          hint={countLabel(m.payments, ['платёж', 'платежа', 'платежей'])}
        />
        <Stat
          label={m.left_at ? 'Был с нами' : 'С нами'}
          value={formatTenure(m.tenure_days)}
          hint={m.left_at ? `ушёл ~${formatDate(m.left_at)}` : m.first_seen ? `с ${formatDate(m.first_seen)}` : undefined}
        />
      </div>
      {m.monthly_churn !== null && (
        <p className="mt-3 text-sm leading-6 text-muted-foreground">
          Модель оценивает вероятность ухода за месяц в {formatShare(m.monthly_churn, 0)}, поэтому ждём, что клиент пробудет с нами ещё{' '}
          {formatMonths(m.expected_lifetime_months, m.lifetime_capped)} и принесёт {formatMoney(m.ltv_forecast, { cents: false })}.
        </p>
      )}
    </Section>
  )
}

type Recommendation = NonNullable<CustomerDetail['recommendations']>[number]

/** Главное действие по клиенту наверху карточки и ещё пара запасных под ним. */
function NextAction({ c }: { c: CustomerDetail }) {
  const recs = c.recommendations ?? []
  const fromReason = c.reasons?.find((r) => r.action)
  const main: Recommendation | null =
    c.next_action ?? recs.find((r) => r.primary) ?? recs[0] ?? (fromReason?.action ? { code: fromReason.code, title: fromReason.action } : null)
  if (!main) return null
  const rest = recs.filter((r) => !(r.code === main.code && r.title === main.title))
  return (
    <Section title="Что сделать">
      <div className="rounded-xl border border-chart-forecast/30 bg-card px-4 py-3.5" data-testid="next-action">
        <div className="flex gap-3">
          <Target className="mt-0.5 size-4 shrink-0 text-chart-forecast" aria-hidden />
          <div className="min-w-0 flex-1">
            <p className="text-[15px] leading-6 font-semibold">{main.title}</p>
            {main.detail && <p className="mt-1 text-sm leading-6 text-muted-foreground">{main.detail}</p>}
            {typeof main.expected_saving === 'number' && main.expected_saving > 0 && (
              <p className="mt-2 inline-flex items-center gap-1.5 rounded-md bg-risk-low-bg px-2 py-1 text-xs font-medium text-risk-low num">
                <PiggyBank className="size-3.5" aria-hidden />
                Сбережёт до {formatMoney(main.expected_saving)} ожидаемой потери
              </p>
            )}
          </div>
        </div>
      </div>
      {rest.length > 0 && (
        <ul className="mt-2 space-y-2">
          {rest.map((r, i) => (
            <li key={`${r.code}-${i}`} className="flex gap-3 rounded-lg border border-border bg-card px-3.5 py-2.5">
              <Lightbulb className="mt-1 size-4 shrink-0 text-muted-foreground" aria-hidden />
              <div className="min-w-0 flex-1">
                <p className="text-sm leading-6 font-medium">{r.title}</p>
                {r.detail && <p className="mt-0.5 text-[13px] leading-5 text-muted-foreground">{r.detail}</p>}
              </div>
              {typeof r.expected_saving === 'number' && r.expected_saving > 0 && (
                <span className="shrink-0 pt-0.5 text-xs text-muted-foreground num" title="Насколько снизится ожидаемая потеря">
                  до {formatMoney(r.expected_saving, { cents: false })}
                </span>
              )}
            </li>
          ))}
        </ul>
      )}
    </Section>
  )
}

function Services({ services }: { services: ServiceRisk[] }) {
  return (
    <Section title={`Услуги · ${services.length}`}>
      {services.length === 0 ? (
        <p className="text-sm text-muted-foreground">У клиента нет услуг.</p>
      ) : (
        <div className="overflow-hidden rounded-lg border border-border bg-card">
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Услуга</TableHead>
                <TableHead>Статус</TableHead>
                <TableHead>Истекает</TableHead>
                <TableHead>Автопродление</TableHead>
                <TableHead className="text-right">В месяц</TableHead>
                <TableHead>Риск</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {services.map((s) => (
                <TableRow key={s.id}>
                  <TableCell className="max-w-[140px]">
                    <span className="block truncate font-medium" title={s.name}>
                      {s.name}
                    </span>
                    <span className="text-xs text-muted-foreground num">#{s.id}</span>
                  </TableCell>
                  <TableCell className="whitespace-nowrap">
                    <span
                      className={cn(
                        'text-[13px]',
                        s.status === 'active' ? 'text-foreground' : s.status === 'deleted' || s.status === 'blocked' ? 'text-risk-high' : 'text-risk-medium',
                      )}
                    >
                      {labelOf(SERVICE_STATUS_LABEL, s.status)}
                    </span>
                  </TableCell>
                  <TableCell className="whitespace-nowrap">
                    <span className="block text-[13px] num">{formatDate(s.expires_at, { short: true })}</span>
                    <span
                      className={cn(
                        'text-xs num',
                        typeof s.days_to_expiry === 'number' && s.days_to_expiry <= 7 ? 'text-risk-medium' : 'text-muted-foreground',
                      )}
                    >
                      {formatDaysToExpiry(s.days_to_expiry)}
                    </span>
                  </TableCell>
                  <TableCell>
                    {s.auto_prolong ? (
                      <span className="inline-flex items-center gap-1.5 text-[13px]">
                        <RefreshCcw className="size-3.5 text-risk-low" aria-hidden />
                        Вкл.
                      </span>
                    ) : (
                      <span className="inline-flex items-center gap-1.5 text-[13px] text-muted-foreground">
                        <RefreshCwOff className="size-3.5" aria-hidden />
                        Выкл.
                      </span>
                    )}
                  </TableCell>
                  <TableCell className="text-right whitespace-nowrap">
                    <span className="block text-[13px] font-medium num">{formatMoney(s.monthly_price)}</span>
                    {s.payment_term !== 'month' && (
                      <span className="text-xs text-muted-foreground num">
                        {formatMoney(s.price)} за {labelOf(PAYMENT_TERM_LABEL, s.payment_term)}
                      </span>
                    )}
                  </TableCell>
                  <TableCell>
                    <Tooltip
                      content={
                        <span>
                          {typeof s.risk_score === 'number' && <span className="mb-1 block font-medium num">Риск {formatScore(s.risk_score)} из 100</span>}
                          {s.reasons?.[0]?.title ?? (s.risk_level === 'unknown' ? 'Оценки нет' : 'Сигналов ухода нет')}
                        </span>
                      }
                    >
                      <span tabIndex={0} className="inline-flex rounded-full outline-none focus-visible:ring-2 focus-visible:ring-ring">
                        <RiskBadge level={s.risk_level} />
                      </span>
                    </Tooltip>
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </div>
      )}
    </Section>
  )
}

function Transactions({ txs, services }: { txs: Transaction[]; services: Map<number, ServiceRisk> }) {
  const [all, setAll] = useState(false)
  const shown = all ? txs : txs.slice(0, 8)
  return (
    <Section title="Последние транзакции">
      {txs.length === 0 ? (
        <p className="text-sm text-muted-foreground">Транзакций нет.</p>
      ) : (
        <>
          <div className="overflow-hidden rounded-lg border border-border bg-card">
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Дата</TableHead>
                  <TableHead>Операция</TableHead>
                  <TableHead>Услуга</TableHead>
                  <TableHead>Статус</TableHead>
                  <TableHead className="text-right">Сумма</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {shown.map((t) => (
                  <TableRow key={t.id}>
                    <TableCell className="text-[13px] whitespace-nowrap num">{formatDateTime(t.performed_at ?? t.created_at)}</TableCell>
                    <TableCell className="text-[13px]">{labelOf(TX_TYPE_LABEL, t.type)}</TableCell>
                    <TableCell className="max-w-[140px] text-[13px] text-muted-foreground">
                      <span className="block truncate" title={services.get(t.service_id)?.name}>
                        {services.get(t.service_id)?.name ?? `#${t.service_id}`}
                      </span>
                    </TableCell>
                    <TableCell className="text-[13px] whitespace-nowrap">
                      {t.status === 'performed' ? (
                        <span className="inline-flex items-center gap-1.5">
                          <CheckCircle2 className="size-3.5 text-risk-low" aria-hidden />
                          {TX_STATUS_LABEL.performed}
                        </span>
                      ) : (
                        <span className="text-risk-high">{labelOf(TX_STATUS_LABEL, t.status)}</span>
                      )}
                    </TableCell>
                    <TableCell className="text-right text-[13px] font-medium whitespace-nowrap num">{formatMoney(t.amount)}</TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </div>
          {txs.length > shown.length || all ? (
            <Button variant="link" size="sm" className="mt-2 px-0" onClick={() => setAll((v) => !v)}>
              {all ? 'Свернуть' : `Показать все · ${txs.length}`}
            </Button>
          ) : null}
        </>
      )}
    </Section>
  )
}

function DetailSkeleton() {
  return (
    <div className="space-y-5 px-6 py-5" aria-busy="true" aria-label="Загружаем карточку клиента">
      <div className="grid gap-4 sm:grid-cols-[220px_1fr]">
        <Skeleton className="h-24 rounded-xl" />
        <Skeleton className="h-24 rounded-xl" />
      </div>
      <Skeleton className="h-4 w-24" />
      {Array.from({ length: 3 }, (_, i) => (
        <Skeleton key={i} className="h-12 rounded-lg" />
      ))}
      <Skeleton className="h-4 w-40" />
      {Array.from({ length: 4 }, (_, i) => (
        <Skeleton key={i} className="h-6" />
      ))}
    </div>
  )
}

export default function CustomerSheet() {
  const { id: rawId } = useParams()
  const id = Number(rawId)
  const valid = Number.isInteger(id) && id > 0
  const navigate = useNavigate()
  const location = useLocation()
  const { changed } = useLive()

  const detail = useQuery({
    queryKey: queryKeys.customer(id),
    queryFn: ({ signal }) => api.customer(id, signal),
    enabled: valid,
  })
  const c = detail.data
  const services = useMemo(() => new Map((c?.services ?? []).map((s) => [s.id, s])), [c])
  const flash = changed.get(id)
  const clock = useDataClock(clockFromReference(referenceOf(c)), clockFromServices(c?.services))

  const close = () => navigate(`/customers${location.search}`)
  const notFound = !valid || (isApiError(detail.error) && detail.error.status === 404)

  return (
    <Sheet open onOpenChange={(open) => !open && close()}>
      <SheetContent
        aria-describedby={undefined}
        onOpenAutoFocus={(e) => {
          e.preventDefault()
          ;(e.currentTarget as HTMLElement | null)?.focus()
        }}
        header={
          <div className="min-w-0">
            <p className="text-xs text-muted-foreground num">Клиент #{valid ? id : '—'}</p>
            <SheetTitle className="mt-0.5 truncate text-[17px] font-semibold tracking-[-0.01em]" title={c?.email}>
              {c?.email ?? (detail.isPending && valid ? 'Загрузка…' : 'Карточка клиента')}
            </SheetTitle>
            <SheetDescription className="sr-only">Оценка риска ухода, причины, услуги и деньги клиента</SheetDescription>
            {c && <DataClockBadge clock={clock} className="mt-2" />}
          </div>
        }
      >
        {notFound ? (
          <EmptyState
            icon={<UserX />}
            title="Клиент не найден"
            description="Возможно, его удалили при последней загрузке данных или ссылка устарела."
            action={
              <Button variant="outline" size="sm" onClick={close}>
                К списку клиентов
              </Button>
            }
          />
        ) : detail.isPending ? (
          <DetailSkeleton />
        ) : detail.isError ? (
          <ErrorState error={detail.error} onRetry={() => void detail.refetch()} />
        ) : c ? (
          <div key={flash ?? 0} className={cn('divide-y divide-border', flash && 'animate-row-flash')}>
            <div className="px-6 py-5">
              <Hero c={c} />
            </div>
            <NextAction c={c} />
            <CustomerActionPanel id={c.id} atRisk={riskLevelOf(c.risk_level) !== 'unknown' && c.expected_loss > 0} />
            <Reasons c={c} services={services} />
            {riskLevelOf(c.risk_level) !== 'unknown' && <Features features={c.features ?? []} level={riskLevelOf(c.risk_level)} />}
            <Money c={c} />
            <MarketingStats c={c} />
            <Services services={c.services ?? []} />
            <Transactions txs={c.transactions ?? []} services={services} />
            <p className="px-6 py-4 text-xs text-subtle-foreground num">
              Оценка от {formatDateTime(c.scored_at)} · модель v{c.model_version}
            </p>
          </div>
        ) : null}
      </SheetContent>
    </Sheet>
  )
}

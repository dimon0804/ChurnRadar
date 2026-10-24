import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { BookOpen, Info, PiggyBank } from 'lucide-react'
import { useState, type FormEvent, type ReactNode } from 'react'
import { errorMessage } from '@/api/client'
import { api, queryKeys } from '@/api/endpoints'
import type { Marketing, MarketingSegment, RetentionScenario } from '@/api/types'
import { AnimatedNumber } from '@/components/animated-number'
import { KpiCard, KpiSkeleton } from '@/components/kpi-card'
import { PageHeader, RecalcNote } from '@/components/page-header'
import { RISK_BAR, RiskGlyph, RISK_TEXT } from '@/components/risk'
import { ErrorState } from '@/components/states'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Skeleton } from '@/components/ui/skeleton'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table'
import { Tooltip } from '@/components/ui/tooltip'
import { countLabel, formatDecimal, formatInt, formatShare, plural } from '@/lib/format'
import { labelOf, PAYMENT_TERM_LABEL, RISK_LABEL_LONG } from '@/lib/labels'
import {
  eur,
  formatMonths,
  formatRatio,
  GLOSSARY,
  kpiHints,
  parseEuro,
  parsePercent,
  RATIO_TEXT,
  ratioVerdict,
  retentionSentence,
  targetSentence,
} from '@/lib/marketing'
import { cn } from '@/lib/utils'
import { useAuth } from '@/providers/auth'

const HINT_WIDTH = 'max-w-sm'

function KpiGrid({ m }: { m: Marketing }) {
  const hints = kpiHints(m)
  const churn = m.churn.customer_monthly
  const revenueChurn = m.churn.revenue_monthly
  const ue = m.unit_economics
  const verdict = ratioVerdict(ue.ltv_cac)
  return (
    <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 xl:grid-cols-3">
      <KpiCard
        label="MRR — выручка в месяц"
        testId="mkt-mrr"
        watch={m.mrr}
        value={<AnimatedNumber value={m.mrr} format={eur} />}
        sub={`С ${countLabel(m.active_customers, ['активного клиента', 'активных клиентов', 'активных клиентов'])}`}
        info={hints.mrr}
        infoClassName={HINT_WIDTH}
      />
      <KpiCard
        label="ARPU — доход с клиента"
        testId="mkt-arpu"
        watch={m.arpu}
        value={<AnimatedNumber value={m.arpu} format={eur} />}
        sub={`В месяц. ARPPU (только платящие): ${eur(m.arppu)}`}
        info={hints.arpu}
        infoClassName={HINT_WIDTH}
      />
      <KpiCard
        label="LTV — ценность клиента"
        testId="mkt-ltv"
        watch={m.ltv}
        value={<AnimatedNumber value={m.ltv} format={eur} />}
        sub={
          m.lifetime.expected_months !== null
            ? `Проживёт с нами ещё ${formatMonths(m.lifetime.expected_months, m.lifetime.capped)}${ue.margin < 1 ? `, маржа ${formatShare(ue.margin, 0)}` : ', в выручке'}`
            : 'Оценённых клиентов пока нет'
        }
        info={hints.ltv}
        infoClassName={HINT_WIDTH}
      />
      <KpiCard
        label="Отток клиентов за месяц"
        testId="mkt-customer-churn"
        watch={churn}
        value={churn === null ? '—' : <AnimatedNumber value={churn} format={(v) => formatShare(v)} />}
        sub={
          churn === null
            ? 'Нет платящих клиентов'
            : `Прогноз модели. Удержание (retention) ${formatShare(m.retention.customer_monthly, 0)}`
        }
        info={hints.customerChurn}
        infoClassName={HINT_WIDTH}
      />
      <KpiCard
        label="Отток выручки за месяц"
        testId="mkt-revenue-churn"
        watch={revenueChurn}
        value={revenueChurn === null ? '—' : <AnimatedNumber value={revenueChurn} format={(v) => formatShare(v)} />}
        sub={`Gross Revenue Churn: −${eur(m.churn.expected_mrr_loss)} MRR в месяц`}
        info={hints.revenueChurn}
        infoClassName={HINT_WIDTH}
      />
      <KpiCard
        label="LTV / CAC"
        testId="mkt-ltv-cac"
        watch={ue.ltv_cac}
        value={ue.ltv_cac === null ? <span className="text-muted-foreground">укажите CAC</span> : formatRatio(ue.ltv_cac)}
        sub={
          ue.payback_months !== null
            ? `Окупается за ${formatMonths(ue.payback_months)}. ${RATIO_TEXT[verdict]}`
            : verdict === 'none'
              ? 'Её задаёт администратор в блоке «Экономика привлечения»'
              : RATIO_TEXT[verdict]
        }
        info={hints.ltvCac}
        infoClassName={HINT_WIDTH}
      />
    </div>
  )
}

function ScenarioLine({ s, ltvTotal, main }: { s: RetentionScenario; ltvTotal: number; main?: boolean }) {
  return (
    <div className={cn(main ? 'text-[17px] leading-7 font-medium' : 'text-sm leading-6 text-muted-foreground')}>
      {retentionSentence(s, ltvTotal)}
      {s.ltv_uplift !== null && s.ltv_retained !== null && s.customers > 0 && (
        <span className={cn('block num', main ? 'mt-1 text-sm font-normal text-muted-foreground' : '')}>
          LTV этих клиентов: {eur(s.ltv_now)} сейчас → {eur(s.ltv_retained)}, если удержим.
        </span>
      )}
    </div>
  )
}

function Retention({ m }: { m: Marketing }) {
  const [high, atRisk] = m.retention_scenarios
  const main = high && high.customers > 0 ? high : atRisk
  const second = main === high ? atRisk : undefined
  const share = m.ltv_total > 0 ? m.ltv_at_risk / m.ltv_total : null
  return (
    <Card data-testid="mkt-retention">
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          <PiggyBank className="size-4 text-muted-foreground" aria-hidden />
          Что даст удержание
        </CardTitle>
        <CardDescription>Связь с риском: сколько стоят клиенты под угрозой и сколько вернёт работа с ними</CardDescription>
      </CardHeader>
      <CardContent className="grid gap-5 lg:grid-cols-[1fr_auto]">
        <div className="space-y-3">
          {main && <ScenarioLine s={main} ltvTotal={m.ltv_total} main />}
          {main && second && second.customers > main.customers && <ScenarioLine s={second} ltvTotal={m.ltv_total} />}
          {main && <p className="text-[13px] leading-5 text-subtle-foreground">{targetSentence(main)}</p>}
        </div>
        <dl className="grid min-w-56 grid-cols-2 gap-3 border-border lg:grid-cols-1 lg:border-l lg:pl-5">
          <div>
            <dt className="text-xs text-muted-foreground">LTV под риском</dt>
            <dd className="mt-0.5 text-[20px] font-semibold num" data-testid="mkt-ltv-at-risk">{eur(m.ltv_at_risk)}</dd>
            <dd className="text-xs text-muted-foreground">
              {share === null ? 'клиенты высокого и среднего риска' : `${formatShare(share, 0)} LTV всей базы (${eur(m.ltv_total)})`}
            </dd>
          </div>
          <div>
            <dt className="text-xs text-muted-foreground">Можно сохранить в месяц</dt>
            <dd className="mt-0.5 text-[20px] font-semibold num">{eur(main?.saved_mrr)}</dd>
            <dd className="text-xs text-muted-foreground">регулярной выручки</dd>
          </div>
        </dl>
      </CardContent>
    </Card>
  )
}

function SegmentRow({ row, mrr }: { row: MarketingSegment; mrr: number }) {
  const share = mrr > 0 ? row.mrr / mrr : 0
  return (
    <TableRow>
      <TableCell>
        <span className="inline-flex items-center gap-2 font-medium">
          <RiskGlyph level={row.level} className={cn('size-2.5', RISK_TEXT[row.level])} />
          {RISK_LABEL_LONG[row.level]}
        </span>
      </TableCell>
      <TableCell className="text-right num">{formatInt(row.customers)}</TableCell>
      <TableCell className="min-w-40">
        <div className="flex items-center gap-2">
          <span className="w-20 shrink-0 text-right num">{eur(row.mrr)}</span>
          <span className="h-1.5 flex-1 overflow-hidden rounded-full bg-muted" aria-hidden>
            <span className={cn('block h-full rounded-full', RISK_BAR[row.level])} style={{ width: `${share * 100}%` }} />
          </span>
          <span className="w-11 text-right text-xs text-muted-foreground num">{mrr > 0 ? formatShare(share, 0) : '—'}</span>
        </div>
      </TableCell>
      <TableCell className="text-right num">{row.customers && row.mrr ? eur(row.arpu) : '—'}</TableCell>
      <TableCell className="text-right num">{formatShare(row.monthly_churn, 0)}</TableCell>
      <TableCell className="text-right num">{row.mrr ? eur(row.ltv_avg) : '—'}</TableCell>
      <TableCell className="text-right num">{row.mrr ? eur(row.ltv) : '—'}</TableCell>
      <TableCell className="text-right font-medium num">{row.expected_mrr_loss ? `−${eur(row.expected_mrr_loss)}` : '—'}</TableCell>
    </TableRow>
  )
}

function HeadHint({ children, hint, className }: { children: ReactNode; hint: string; className?: string }) {
  return (
    <TableHead className={className}>
      <Tooltip content={hint}>
        <span tabIndex={0} className="cursor-help underline decoration-dotted underline-offset-4 outline-none focus-visible:ring-2 focus-visible:ring-ring">
          {children}
        </span>
      </Tooltip>
    </TableHead>
  )
}

function Segments({ m }: { m: Marketing }) {
  const rows = m.segments.filter((r) => r.customers > 0)
  return (
    <Card data-testid="mkt-segments">
      <CardHeader>
        <CardTitle>Сегменты по уровню риска</CardTitle>
        <CardDescription>Где деньги и где мы их теряем: выручка, ценность и ожидаемая потеря по каждому уровню</CardDescription>
      </CardHeader>
      <CardContent className="overflow-x-auto">
        <Table aria-label="Сегменты по уровню риска">
          <TableHeader>
            <TableRow>
              <TableHead>Уровень</TableHead>
              <TableHead className="text-right">Клиенты</TableHead>
              <HeadHint hint="Выручка сегмента в месяц и её доля в общей">MRR</HeadHint>
              <HeadHint className="text-right" hint="MRR сегмента на одного платящего клиента">ARPU</HeadHint>
              <HeadHint className="text-right" hint="Средняя вероятность ухода за месяц по прогнозу модели">Отток/мес</HeadHint>
              <HeadHint className="text-right" hint="Сколько в среднем принесёт один клиент сегмента">LTV клиента</HeadHint>
              <HeadHint className="text-right" hint="Сумма LTV всех клиентов сегмента">LTV сегмента</HeadHint>
              <HeadHint className="text-right" hint="Сколько регулярной выручки сегмент, вероятно, потеряет за месяц">Потеря MRR/мес</HeadHint>
            </TableRow>
          </TableHeader>
          <TableBody>
            {rows.map((row) => (
              <SegmentRow key={row.level} row={row} mrr={m.mrr} />
            ))}
          </TableBody>
        </Table>
        {m.segments.some((r) => r.level === 'unknown' && r.customers > 0) && (
          <p className="mt-3 text-[13px] leading-5 text-muted-foreground">
            «Нет оценки» — клиенты без действующих платных услуг: они уже ушли или ещё ничего не купили, поэтому выручки и LTV у них нет.
          </p>
        )}
      </CardContent>
    </Card>
  )
}

function Fact({ label, value, hint, testId }: { label: string; value: ReactNode; hint?: ReactNode; testId?: string }) {
  return (
    <div className="min-w-0" data-testid={testId}>
      <dt className="text-xs text-muted-foreground">{label}</dt>
      <dd className="mt-0.5 text-[17px] font-semibold tracking-[-0.01em] num">{value}</dd>
      {hint && <dd className="mt-0.5 text-xs leading-5 text-muted-foreground">{hint}</dd>}
    </div>
  )
}

function RevenueStructure({ m }: { m: Marketing }) {
  const terms = m.payment_terms.filter((t) => t.services > 0)
  const top = m.concentration
  return (
    <Card>
      <CardHeader>
        <CardTitle>Из чего складывается выручка</CardTitle>
        <CardDescription>Насколько выручка зависит от немногих клиентов и как платят</CardDescription>
      </CardHeader>
      <CardContent className="space-y-5">
        <dl className="grid grid-cols-2 gap-4">
          <Fact
            label="Концентрация выручки"
            testId="mkt-concentration"
            value={formatShare(top.top_share, 0)}
            hint={
              top.top_customers > 0
                ? `приносят ${countLabel(top.top_customers, ['самый крупный клиент', 'самых крупных клиента', 'самых крупных клиентов'])} (топ-10%). Чем выше, тем больнее уход одного`
                : 'платящих клиентов нет'
            }
          />
          <Fact
            label="Средний чек"
            value={eur(m.ltv_fact.avg_check)}
            hint={`средний успешный платёж, всего ${countLabel(m.ltv_fact.payments, ['платёж', 'платежа', 'платежей'])}`}
          />
          <Fact
            label="На автопродлении"
            value={formatShare(m.auto_prolong.customers_share, 0)}
            hint={`активных клиентов; ${formatShare(m.auto_prolong.mrr_share, 0)} MRR продлевается само`}
          />
          <Fact
            label="Платящие клиенты"
            value={formatInt(m.paying_customers)}
            hint={`из ${countLabel(m.customers_total, ['клиента', 'клиентов', 'клиентов'])} в базе`}
          />
        </dl>
        <div>
          <p className="mb-2 text-xs text-muted-foreground">Выручка по периоду оплаты</p>
          <ul className="space-y-2" data-testid="mkt-terms">
            {terms.map((t) => (
              <li key={t.term} className="flex items-center gap-3 text-sm">
                <span className="w-20 shrink-0">за {labelOf(PAYMENT_TERM_LABEL, t.term)}</span>
                <span className="h-1.5 flex-1 overflow-hidden rounded-full bg-muted" aria-hidden>
                  <span className="block h-full rounded-full bg-foreground/35" style={{ width: `${(t.share ?? 0) * 100}%` }} />
                </span>
                <span className="w-24 text-right num">{eur(t.mrr)}</span>
                <span className="w-24 text-right text-xs text-muted-foreground num">
                  {countLabel(t.services, ['услуга', 'услуги', 'услуг'])}
                </span>
              </li>
            ))}
          </ul>
          {terms.length === 0 && <p className="text-sm text-muted-foreground">Действующих услуг нет.</p>}
        </div>
      </CardContent>
    </Card>
  )
}

function History({ m }: { m: Marketing }) {
  const fact = m.churn.fact
  return (
    <Card>
      <CardHeader>
        <CardTitle>Что уже было</CardTitle>
        <CardDescription>Факт из истории платежей и статусов, без прогноза</CardDescription>
      </CardHeader>
      <CardContent>
        <dl className="grid grid-cols-2 gap-4">
          <Fact
            label="Клиенты уже принесли"
            testId="mkt-ltv-fact"
            value={eur(m.ltv_fact.total)}
            hint={`фактический LTV: сумма успешных платежей, в среднем ${eur(m.ltv_fact.avg_per_customer)} на клиента`}
          />
          <Fact
            label="Фактический отток"
            value={fact.customers_left > 0 ? `${formatShare(fact.monthly_churn)} в месяц` : '—'}
            hint={
              fact.customers_left > 0
                ? `ушли ${countLabel(fact.customers_left, ['клиент', 'клиента', 'клиентов'])} за ${formatDecimal(fact.exposure_months, 0)} клиенто-месяцев наблюдения`
                : 'за время наблюдения никто не ушёл'
            }
          />
          <Fact
            label="Средний срок с нами"
            value={formatMonths(fact.avg_tenure_months)}
            hint={fact.avg_lifetime_left_months !== null ? `ушедшие прожили в среднем ${formatMonths(fact.avg_lifetime_left_months)}` : 'по всем клиентам'}
          />
          <Fact
            label="Ожидаемый срок по модели"
            value={formatMonths(m.lifetime.expected_months, m.lifetime.capped)}
            hint="1 ÷ отток клиентов в месяц"
          />
        </dl>
        {fact.monthly_churn !== null && m.churn.customer_monthly !== null && fact.customers_left > 0 && (
          <p className="mt-4 text-[13px] leading-5 text-muted-foreground">
            Прогноз ({formatShare(m.churn.customer_monthly, 0)} в месяц) и факт ({formatShare(fact.monthly_churn, 1)}) считаются по-разному.
            Факт усредняет всю историю, а модель смотрит на сигналы у текущих клиентов прямо сейчас: выключенное автопродление, нехватку
            денег на балансе, неоплаченные счета.
          </p>
        )}
      </CardContent>
    </Card>
  )
}

function Economics({ m }: { m: Marketing }) {
  const { isAdmin } = useAuth()
  const queryClient = useQueryClient()
  const settings = m.settings
  const [margin, setMargin] = useState(() => formatDecimal(settings.margin * 100, 1))
  const [cac, setCac] = useState(() => (settings.cac === null ? '' : formatDecimal(settings.cac / 100, 2).replace(/\s/g, '')))
  const [message, setMessage] = useState<{ ok: boolean; text: string } | null>(null)
  const save = useMutation({
    mutationFn: api.putMarketingSettings,
    onSuccess: () => {
      setMessage({ ok: true, text: 'Сохранено, цифры наверху уже пересчитаны.' })
      void queryClient.invalidateQueries({ queryKey: queryKeys.marketing })
    },
    onError: (error) => setMessage({ ok: false, text: errorMessage(error) }),
  })

  const submit = (event: FormEvent) => {
    event.preventDefault()
    const share = parsePercent(margin)
    const cents = parseEuro(cac)
    if (!Number.isFinite(share) || share < 0.01 || share > 1) {
      setMessage({ ok: false, text: 'Маржа — число от 1 до 100 процентов.' })
      return
    }
    if (cents !== null && (!Number.isFinite(cents) || cents < 1)) {
      setMessage({ ok: false, text: 'CAC — сумма в евро больше нуля, например 25 или 12,50. Пустое поле — «не задано».' })
      return
    }
    setMessage(null)
    save.mutate({ margin: share, cac: cents })
  }

  return (
    <Card data-testid="mkt-economics">
      <CardHeader>
        <CardTitle>Экономика привлечения</CardTitle>
        <CardDescription>
          Маржи и стоимости привлечения в данных хостинга нет, их задаёт администратор. По умолчанию маржа 100%, то есть LTV показан в
          выручке, а не в прибыли.
        </CardDescription>
      </CardHeader>
      <CardContent>
        <form onSubmit={submit} className="flex flex-wrap items-end gap-4" aria-label="Маржа и стоимость привлечения">
          <div className="w-40 space-y-1.5">
            <Label htmlFor="mkt-margin">Маржа, %</Label>
            <Input id="mkt-margin" inputMode="decimal" value={margin} disabled={!isAdmin} onChange={(e) => setMargin(e.target.value)} />
          </div>
          <div className="w-64 space-y-1.5">
            <Label htmlFor="mkt-cac">Стоимость привлечения (CAC), €</Label>
            <Input
              id="mkt-cac"
              inputMode="decimal"
              placeholder="не задана"
              value={cac}
              disabled={!isAdmin}
              onChange={(e) => setCac(e.target.value)}
            />
          </div>
          {isAdmin ? (
            <Button type="submit" disabled={save.isPending}>
              {save.isPending ? 'Сохраняем…' : 'Сохранить'}
            </Button>
          ) : (
            <p className="pb-2 text-[13px] text-muted-foreground">Менять может только администратор.</p>
          )}
        </form>
        {message && (
          <p role="status" className={cn('mt-3 text-sm', message.ok ? 'text-risk-low' : 'text-destructive')}>
            {message.text}
          </p>
        )}
        {settings.updated_at && (
          <p className="mt-3 text-xs text-subtle-foreground">
            Изменено {new Date(settings.updated_at).toLocaleString('ru-RU')}
            {settings.updated_by ? ` · ${settings.updated_by}` : ''}
          </p>
        )}
      </CardContent>
    </Card>
  )
}

function Glossary() {
  return (
    <Card data-testid="mkt-glossary">
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          <BookOpen className="size-4 text-muted-foreground" aria-hidden />
          Словарь терминов
        </CardTitle>
        <CardDescription>Маркетинговые слова простым языком</CardDescription>
      </CardHeader>
      <CardContent>
        <dl className="grid gap-x-8 gap-y-4 md:grid-cols-2">
          {GLOSSARY.map((g) => (
            <div key={g.term}>
              <dt className="text-sm">
                <span className="font-semibold">{g.term}</span>
                <span className="text-muted-foreground"> — {g.name}</span>
              </dt>
              <dd className="mt-0.5 text-[13px] leading-5 text-muted-foreground">{g.text}</dd>
            </div>
          ))}
        </dl>
      </CardContent>
    </Card>
  )
}

function Assumptions({ m }: { m: Marketing }) {
  return (
    <div className="flex items-start gap-3 rounded-xl border border-border bg-card px-5 py-3.5 shadow-card">
      <Info className="mt-1 size-4 shrink-0 text-subtle-foreground" aria-hidden />
      <p className="text-sm leading-6 text-muted-foreground">
        <span className="font-medium text-foreground">Как считаем. </span>
        Месяц — 30 дней, суммы в евро. Отток — прогноз модели на {m.horizon_days}{' '}
        {plural(m.horizon_days, ['день', 'дня', 'дней'])}
        {m.horizon_days !== 30 ? ', пересчитанный на месяц' : ''}: score клиента — доля его выручки, которую модель ожидает потерять. Срок
        жизни ограничен {formatDecimal(m.lifetime.cap_months, 0)} месяцами, чтобы при оттоке около нуля LTV не уходил в бесконечность.
        Фактический LTV — только платежи, которые есть в данных.
      </p>
    </div>
  )
}

function MarketingSkeleton() {
  return (
    <div className="space-y-4" aria-busy="true" aria-label="Загружаем маркетинговые метрики">
      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 xl:grid-cols-3">
        {Array.from({ length: 6 }, (_, i) => (
          <KpiSkeleton key={i} />
        ))}
      </div>
      <Skeleton className="h-44 rounded-xl" />
      <Skeleton className="h-64 rounded-xl" />
    </div>
  )
}

export default function MarketingPage() {
  const query = useQuery({ queryKey: queryKeys.marketing, queryFn: ({ signal }) => api.marketing(signal) })
  const m = query.data
  return (
    <>
      <PageHeader
        title="Маркетинг"
        description="Сколько приносят клиенты, сколько из них уйдёт и что даст удержание. Непонятное слово — в словаре внизу страницы."
        meta={m && <RecalcNote scoredAt={m.scored_at} modelVersion={m.model_version} />}
      />
      {query.isPending ? (
        <MarketingSkeleton />
      ) : query.isError ? (
        <Card>
          <ErrorState error={query.error} onRetry={() => void query.refetch()} />
        </Card>
      ) : m ? (
        <div className="space-y-4">
          <KpiGrid m={m} />
          <Assumptions m={m} />
          <Retention m={m} />
          <Segments m={m} />
          <div className="grid gap-4 lg:grid-cols-2">
            <RevenueStructure m={m} />
            <History m={m} />
          </div>
          <Economics key={`${m.settings.margin}-${m.settings.cac}`} m={m} />
          <Glossary />
        </div>
      ) : null}
    </>
  )
}

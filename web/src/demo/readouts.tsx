import { useQuery } from '@tanstack/react-query'
import type { ReactNode } from 'react'
import { api, queryKeys } from '@/api/endpoints'
import { AnimatedNumber } from '@/components/animated-number'
import { formatInt, formatMoney } from '@/lib/format'
import { cn } from '@/lib/utils'
import { RecalcNote } from '@/components/page-header'
import type { Readout } from './steps'

const money = (v: number | null | undefined) => formatMoney(v, { cents: false })

function Cell({ label, children, className }: { label: string; children: ReactNode; className?: string }) {
  return (
    <div className={cn('min-w-0 rounded-lg border border-border bg-background/60 px-3 py-2', className)}>
      <p className="truncate text-[11px] text-muted-foreground">{label}</p>
      <p className="mt-0.5 truncate text-[15px] font-semibold tracking-[-0.01em] num">{children}</p>
    </div>
  )
}

function SummaryReadout() {
  const summary = useQuery({ queryKey: queryKeys.summary, queryFn: ({ signal }) => api.summary(signal) })
  const s = summary.data
  if (!s) return <div className="h-[58px] animate-pulse rounded-lg bg-muted" />
  return (
    <div className="space-y-1.5">
      <div className="grid grid-cols-[1fr_1fr_1.5fr] gap-1.5">
        <Cell label="Высокий">
          <AnimatedNumber value={s.by_level.high} format={formatInt} />
        </Cell>
        <Cell label="Средний">
          <AnimatedNumber value={s.by_level.medium} format={formatInt} />
        </Cell>
        <Cell label="Ожидаемая потеря">
          <AnimatedNumber value={s.expected_loss} format={money} />
        </Cell>
      </div>
      <RecalcNote scoredAt={s.scored_at} modelVersion={s.model_version} />
    </div>
  )
}

function SimulatorReadout() {
  const simulator = useQuery({
    queryKey: queryKeys.simulator,
    queryFn: ({ signal }) => api.simulator(signal),
    refetchInterval: 1500,
  })
  const summary = useQuery({ queryKey: queryKeys.summary, queryFn: ({ signal }) => api.summary(signal) })
  const sim = simulator.data
  const s = summary.data
  return (
    <div className="grid grid-cols-3 gap-1.5">
      <Cell label="Поток">
        <span className={cn('inline-flex items-center gap-1.5', sim?.running ? 'text-risk-low' : 'text-muted-foreground')}>
          <span className={cn('size-2 rounded-full', sim?.running ? 'animate-pulse bg-risk-low-bar' : 'bg-risk-unknown-bar')} />
          {sim ? (sim.running ? 'идёт' : 'стоит') : '…'}
        </span>
      </Cell>
      <Cell label="Событий">
        <AnimatedNumber value={sim?.events_emitted ?? null} format={formatInt} />
      </Cell>
      <Cell label="Клиентов">
        <AnimatedNumber value={s?.customers_total ?? null} format={formatInt} />
      </Cell>
    </div>
  )
}

export function ReadoutView({ kind }: { kind: Readout }) {
  return kind === 'summary' ? <SummaryReadout /> : <SimulatorReadout />
}

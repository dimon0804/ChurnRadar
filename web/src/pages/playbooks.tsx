import { keepPreviousData, useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { FlaskConical, Pencil, Play, Plus, Trash2, Workflow, Zap } from 'lucide-react'
import { useMemo, useState, type ReactNode } from 'react'
import { Bar, BarChart, CartesianGrid, Legend, ResponsiveContainer, Tooltip as ChartTooltip, XAxis, YAxis } from 'recharts'
import { Link, useSearchParams } from 'react-router'
import { errorMessage } from '@/api/client'
import {
  playbookKeys,
  playbooksApi,
  type Playbook,
  type PlaybookInput,
  type PlaybookList,
  type PlaybookPreset,
  type PlaybookUpliftWindow,
} from '@/api/playbooks'
import type { RiskLevel } from '@/api/types'
import { PageHeader } from '@/components/page-header'
import { RiskBadge } from '@/components/risk'
import { EmptyState, ErrorState } from '@/components/states'
import { ConfirmDialog } from '@/components/ui/alert-dialog'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Dialog } from '@/components/ui/modal'
import { Select } from '@/components/ui/select'
import { Skeleton } from '@/components/ui/skeleton'
import { Switch } from '@/components/ui/switch'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table'
import { useCssVars } from '@/hooks/use-css-vars'
import { useDebouncedValue } from '@/hooks/use-debounced'
import { TEMPLATE_TITLE } from '@/lib/contact-today'
import { formatDateTime, formatInt, formatMoney, plural } from '@/lib/format'
import { RISK_LABEL } from '@/lib/labels'
import {
  buildInput,
  CONTROL_SHARES,
  defaultWindow,
  describeAction,
  describeConditions,
  emptyDraft,
  formatInterval,
  formatPp,
  presetDraft,
  shareLabel,
  toDraft,
  upliftRows,
  VERDICT,
  WINDOW_LABEL,
  type PlaybookDraft,
} from '@/lib/playbooks'
import { cn } from '@/lib/utils'
import { useAuth } from '@/providers/auth'

const LEVELS: RiskLevel[] = ['high', 'medium', 'low', 'unknown']
const CUSTOMERS: readonly [string, string, string] = ['клиент', 'клиента', 'клиентов']

function toInput(p: Playbook): PlaybookInput {
  return {
    name: p.name,
    description: p.description,
    enabled: p.enabled,
    mode: p.mode,
    conditions: p.conditions,
    actions: p.actions,
    control_share: p.control_share,
    max_per_day: p.max_per_day,
    cooldown_days: p.cooldown_days,
    preset: p.preset,
  }
}

function Pill({ children, tone = 'neutral' }: { children: ReactNode; tone?: 'neutral' | 'live' | 'sim' | 'off' }) {
  return (
    <span
      className={cn(
        'inline-flex h-5 items-center gap-1 rounded-full px-2 text-[11px] font-medium whitespace-nowrap',
        tone === 'live' && 'bg-risk-low-bg text-risk-low',
        tone === 'sim' && 'bg-risk-medium-bg text-risk-medium',
        tone === 'off' && 'bg-muted text-muted-foreground',
        tone === 'neutral' && 'bg-muted text-foreground',
      )}
    >
      {children}
    </span>
  )
}

function StatusPills({ p }: { p: Playbook }) {
  return (
    <div className="flex flex-wrap gap-1.5">
      {!p.enabled ? <Pill tone="off">Выключен</Pill> : p.mode === 'live' ? <Pill tone="live">Работает</Pill> : <Pill tone="sim">Симуляция</Pill>}
      <Pill>контроль {shareLabel(p.control_share)}</Pill>
    </div>
  )
}

// --- конструктор ---------------------------------------------------------------------------

function Field({ label, htmlFor, hint, children }: { label: string; htmlFor?: string; hint?: string; children: ReactNode }) {
  return (
    <div className="space-y-1.5">
      <Label htmlFor={htmlFor}>{label}</Label>
      {children}
      {hint && <p className="text-xs text-subtle-foreground">{hint}</p>}
    </div>
  )
}

function Toggle({ id, label, checked, onChange, children }: { id: string; label: string; checked: boolean; onChange: (v: boolean) => void; children?: ReactNode }) {
  return (
    <div className="flex flex-wrap items-center gap-x-3 gap-y-2 rounded-lg border border-border px-3 py-2.5">
      <label htmlFor={id} className="flex min-w-0 flex-1 cursor-pointer items-center gap-2.5 text-[13px]">
        <Switch id={id} checked={checked} onCheckedChange={onChange} />
        {label}
      </label>
      {checked && children}
    </div>
  )
}

function Preview({ input, id }: { input: PlaybookInput | null; id: number | null }) {
  const debounced = useDebouncedValue(input, 400)
  const preview = useQuery({
    queryKey: playbookKeys.preview(debounced as PlaybookInput, id),
    queryFn: ({ signal }) => playbooksApi.preview(debounced as PlaybookInput, id, signal),
    enabled: debounced !== null,
    placeholderData: keepPreviousData,
  })
  if (!input) {
    return <p className="text-[13px] text-muted-foreground">Заполните условие и действие — здесь появится, кого заденет правило.</p>
  }
  if (preview.isError) return <p role="alert" className="text-[13px] text-risk-high">{errorMessage(preview.error)}</p>
  const data = preview.data
  if (!data) return <Skeleton className="h-24 w-full" />
  const skipped = data.skipped_cooldown + data.skipped_limit + data.skipped_no_email
  return (
    <div className={cn('space-y-3 transition-opacity', preview.isFetching && 'opacity-60')} data-testid="playbook-preview">
      <p className="text-sm leading-6">
        Сейчас под правило попадают <b className="num" data-testid="preview-matched">{formatInt(data.matched)}</b>{' '}
        {plural(data.matched, CUSTOMERS)}. Действие получат{' '}
        <b className="num" data-testid="preview-treated">{formatInt(data.treated)}</b>, в контроле останутся{' '}
        <b className="num" data-testid="preview-control">{formatInt(data.control)}</b>.
      </p>
      {skipped > 0 && (
        <p className="text-xs text-muted-foreground">
          Не сейчас: {data.skipped_limit > 0 && `${formatInt(data.skipped_limit)} сверх дневного лимита`}
          {data.skipped_cooldown > 0 && `${data.skipped_limit ? ', ' : ''}${formatInt(data.skipped_cooldown)} уже получали действие недавно`}
          {data.skipped_no_email > 0 && `${data.skipped_limit || data.skipped_cooldown ? ', ' : ''}${formatInt(data.skipped_no_email)} без email`}.
        </p>
      )}
      {!data.levels_ready && input.conditions.became_high && (
        <p className="text-xs text-risk-medium">«Стал высоким» начнёт срабатывать со следующего пересчёта: пока не с чем сравнить.</p>
      )}
      {data.sample.length > 0 && (
        <ul className="divide-y divide-border rounded-lg border border-border text-[13px]">
          {data.sample.slice(0, 5).map((s) => (
            <li key={s.customer_id} className="flex items-center gap-2 px-3 py-1.5">
              <RiskBadge level={s.risk_level} />
              <span className="min-w-0 flex-1 truncate">{s.email || `#${s.customer_id}`}</span>
              <span className="num text-muted-foreground">{formatMoney(s.expected_loss, { cents: false })}</span>
              <span className={cn('w-20 text-right text-xs', s.group === 'control' ? 'text-subtle-foreground' : 'font-medium')}>
                {s.group === 'control' ? 'контроль' : 'действие'}
              </span>
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}

function Editor({
  open,
  onOpenChange,
  initial,
  editing,
  reasons,
  mailEnabled,
  onSaved,
}: {
  open: boolean
  onOpenChange: (open: boolean) => void
  initial: PlaybookDraft
  editing: Playbook | null
  reasons: { code: string; title: string }[]
  mailEnabled: boolean
  onSaved: (p: Playbook) => void
}) {
  const queryClient = useQueryClient()
  const [d, setD] = useState(initial)
  const set = <K extends keyof PlaybookDraft>(key: K, value: PlaybookDraft[K]) => setD((old) => ({ ...old, [key]: value }))
  const built = useMemo(() => buildInput(d), [d])
  const save = useMutation({
    mutationFn: (input: PlaybookInput) => (editing ? playbooksApi.update(editing.id, input) : playbooksApi.create(input)),
    onSuccess: (saved) => {
      void queryClient.invalidateQueries({ queryKey: playbookKeys.all })
      onSaved(saved)
    },
  })
  const toggleLevel = (level: RiskLevel) =>
    set('levels', d.levels.includes(level) ? d.levels.filter((l) => l !== level) : [...d.levels, level])

  return (
    <Dialog
      open={open}
      onOpenChange={onOpenChange}
      title={editing ? 'Изменить сценарий' : 'Новый сценарий'}
      description="Если клиент подходит под все условия — выполняем действия. Часть клиентов уходит в контроль: им ничего не делаем, чтобы честно измерить эффект."
      className="w-[min(920px,calc(100vw-32px))]"
      footer={
        <>
          {save.isError && <p role="alert" className="mr-auto self-center text-[13px] text-risk-high">{errorMessage(save.error)}</p>}
          <Button variant="ghost" onClick={() => onOpenChange(false)}>
            Отмена
          </Button>
          <Button onClick={() => built.input && save.mutate(built.input)} disabled={!built.input || save.isPending}>
            {save.isPending ? 'Сохраняем…' : 'Сохранить'}
          </Button>
        </>
      }
    >
      <div className="grid gap-6 lg:grid-cols-[1fr_320px]">
        <div className="space-y-5">
          <Field label="Название" htmlFor="pb-name">
            <Input id="pb-name" value={d.name} onChange={(e) => set('name', e.target.value)} maxLength={120} placeholder="Например: высокий риск и давно не писали" />
          </Field>

          <section className="space-y-2.5">
            <h3 className="text-[13px] font-semibold">Если</h3>
            <div className="flex flex-wrap items-center gap-1.5" role="group" aria-label="Уровень риска">
              <span className="mr-1 text-[13px] text-muted-foreground">Уровень риска:</span>
              {LEVELS.map((level) => (
                <button
                  key={level}
                  type="button"
                  aria-pressed={d.levels.includes(level)}
                  onClick={() => toggleLevel(level)}
                  className={cn(
                    'h-7 rounded-full border px-2.5 text-xs outline-none focus-visible:ring-2 focus-visible:ring-ring',
                    d.levels.includes(level) ? 'border-brand bg-brand-soft text-foreground' : 'border-border hover:bg-accent',
                  )}
                >
                  {RISK_LABEL[level]}
                </button>
              ))}
            </div>
            <Toggle id="pb-became" label="Риск стал высоким с прошлого пересчёта" checked={d.becameHigh} onChange={(v) => set('becameHigh', v)} />
            <div className="grid gap-3 sm:grid-cols-2">
              <Field label="Главная причина">
                <Select
                  ariaLabel="Главная причина"
                  value={d.reason || 'any'}
                  onValueChange={(v) => set('reason', v === 'any' ? '' : v)}
                  options={[{ value: 'any', label: 'Любая' }, ...reasons.map((r) => ({ value: r.code, label: r.title }))]}
                  className="w-full"
                />
              </Field>
              <Field label="Ожидаемая потеря от, €" htmlFor="pb-loss">
                <Input id="pb-loss" inputMode="decimal" value={d.minLoss} onChange={(e) => set('minLoss', e.target.value)} placeholder="не важно" />
              </Field>
              <Field label="Не писали клиенту, дней" htmlFor="pb-noemail">
                <Input id="pb-noemail" inputMode="numeric" value={d.noEmailDays} onChange={(e) => set('noEmailDays', e.target.value)} placeholder="не важно" />
              </Field>
              <Field label="Списание в ближайшие, дней" htmlFor="pb-expires" hint="По действующим услугам: когда кончится оплаченный срок">
                <Input id="pb-expires" inputMode="numeric" value={d.expiresWithin} onChange={(e) => set('expiresWithin', e.target.value)} placeholder="не важно" />
              </Field>
              <Field label="MRR клиента от, €" htmlFor="pb-mrr-min">
                <Input id="pb-mrr-min" inputMode="decimal" value={d.minMrr} onChange={(e) => set('minMrr', e.target.value)} placeholder="не важно" />
              </Field>
              <Field label="MRR клиента до, €" htmlFor="pb-mrr-max">
                <Input id="pb-mrr-max" inputMode="decimal" value={d.maxMrr} onChange={(e) => set('maxMrr', e.target.value)} placeholder="не важно" />
              </Field>
            </div>
            <Toggle id="pb-autoprolong" label="У действующей услуги выключено автопродление" checked={d.autoprolongOff} onChange={(v) => set('autoprolongOff', v)} />
          </section>

          <section className="space-y-2.5">
            <h3 className="text-[13px] font-semibold">То</h3>
            <Toggle id="pb-email" label="Отправить письмо" checked={d.email} onChange={(v) => set('email', v)}>
              <Select
                ariaLabel="Шаблон письма"
                value={d.template || 'auto'}
                onValueChange={(v) => set('template', v === 'auto' ? '' : v)}
                options={[{ value: 'auto', label: 'По главной причине' }, ...Object.entries(TEMPLATE_TITLE).map(([value, label]) => ({ value, label }))]}
                className="w-56"
              />
            </Toggle>
            {d.email && !mailEnabled && (
              <p className="text-xs text-risk-medium">Почта не настроена (нет SMTP_HOST): письма будут пропущены, остальное сработает.</p>
            )}
            <Toggle id="pb-status" label="Поставить статус" checked={d.status} onChange={(v) => set('status', v)}>
              <Select
                ariaLabel="Статус"
                value={d.statusKind}
                onValueChange={(v) => set('statusKind', v as PlaybookDraft['statusKind'])}
                options={[
                  { value: 'snoozed', label: 'Отложить' },
                  { value: 'resolved', label: 'Решено' },
                ]}
                className="w-32"
              />
              {d.statusKind === 'snoozed' && (
                <Input aria-label="Отложить на дней" inputMode="numeric" value={d.statusDays} onChange={(e) => set('statusDays', e.target.value)} className="w-20" />
              )}
            </Toggle>
            <Toggle id="pb-notify" label="Уведомить ответственного" checked={d.notify} onChange={(v) => set('notify', v)}>
              <Input aria-label="Логин ответственного" value={d.notifyUser} onChange={(e) => set('notifyUser', e.target.value)} className="w-40" />
            </Toggle>
          </section>

          <section className="space-y-2.5">
            <h3 className="text-[13px] font-semibold">Ограничители</h3>
            <div className="grid gap-3 sm:grid-cols-3">
              <Field label="Контрольная группа" hint="Им действие не выполняется">
                <Select
                  ariaLabel="Контрольная группа"
                  value={String(d.controlShare)}
                  onValueChange={(v) => set('controlShare', Number(v))}
                  options={CONTROL_SHARES.map((s) => ({ value: String(s), label: shareLabel(s) }))}
                  className="w-full"
                />
              </Field>
              <Field label="Клиентов в день, не больше" htmlFor="pb-perday">
                <Input id="pb-perday" inputMode="numeric" value={d.maxPerDay} onChange={(e) => set('maxPerDay', e.target.value)} />
              </Field>
              <Field label="Одному клиенту не чаще раза в, дней" htmlFor="pb-cooldown">
                <Input id="pb-cooldown" inputMode="numeric" value={d.cooldownDays} onChange={(e) => set('cooldownDays', e.target.value)} />
              </Field>
            </div>
            <div className="grid gap-3 sm:grid-cols-2">
              <Field label="Режим">
                <Select
                  ariaLabel="Режим"
                  value={d.mode}
                  onValueChange={(v) => set('mode', v as PlaybookDraft['mode'])}
                  options={[
                    { value: 'simulation', label: 'Симуляция — только показать, кого заденет' },
                    { value: 'live', label: 'Боевой — выполнять действия' },
                  ]}
                  className="w-full"
                />
              </Field>
              <div className="flex items-end">
                <Toggle id="pb-enabled" label="Срабатывать после каждого пересчёта" checked={d.enabled} onChange={(v) => set('enabled', v)} />
              </div>
            </div>
          </section>

          {built.errors.length > 0 && (
            <ul className="list-disc space-y-0.5 pl-5 text-[13px] text-risk-high" aria-live="polite">
              {built.errors.map((e) => (
                <li key={e}>{e}</li>
              ))}
            </ul>
          )}
        </div>
        <aside className="space-y-2 lg:sticky lg:top-0 lg:self-start">
          <h3 className="text-[13px] font-semibold">Кого заденет сейчас</h3>
          <Preview input={built.input} id={editing?.id ?? null} />
        </aside>
      </div>
    </Dialog>
  )
}

// --- эффект ---------------------------------------------------------------------------------

function UpliftChart({ window: w }: { window: PlaybookUpliftWindow }) {
  const colors = useCssVars(['chart-fact', 'chart-axis', 'chart-grid', 'muted-foreground'])
  const rows = upliftRows(w)
  return (
    <div className="h-56 w-full" role="img" aria-label="Доли у затронутых и в контроле по трём исходам">
      <ResponsiveContainer width="100%" height="100%">
        <BarChart data={rows} margin={{ top: 8, right: 8, left: -12, bottom: 0 }} barGap={4}>
          <CartesianGrid vertical={false} stroke={colors['chart-grid']} />
          <XAxis dataKey="metric" tickLine={false} axisLine={false} tick={{ fill: colors['muted-foreground'], fontSize: 12 }} />
          <YAxis domain={[0, 100]} tickFormatter={(v: number) => `${v}%`} tickLine={false} axisLine={false} tick={{ fill: colors['muted-foreground'], fontSize: 12 }} />
          <ChartTooltip
            formatter={(value) => (value == null ? '—' : `${String(value).replace('.', ',')}%`)}
            contentStyle={{ borderRadius: 8, fontSize: 12 }}
          />
          <Legend wrapperStyle={{ fontSize: 12 }} />
          <Bar dataKey="treated" name={`Затронутые (${w.treated_n})`} fill={colors['chart-fact']} radius={[4, 4, 0, 0]} />
          <Bar dataKey="control" name={`Контроль (${w.control_n})`} fill={colors['chart-axis']} radius={[4, 4, 0, 0]} />
        </BarChart>
      </ResponsiveContainer>
    </div>
  )
}

function EffectCard({ playbook }: { playbook: Playbook }) {
  const uplift = useQuery({ queryKey: playbookKeys.uplift(playbook.id), queryFn: ({ signal }) => playbooksApi.uplift(playbook.id, signal) })
  const [days, setDays] = useState<number | null>(null)
  const windows = uplift.data?.windows ?? []
  const chosen = days ?? defaultWindow(windows)
  const current = windows.find((w) => w.days === chosen)
  return (
    <Card data-testid="playbook-effect">
      <CardHeader className="flex flex-row flex-wrap items-start justify-between gap-3">
        <div>
          <CardTitle>Помогает ли сценарий</CardTitle>
          <CardDescription>
            Сравниваем тех, кому выполнили действие, с контрольной группой: у кого спустя срок риск снизился, кто пополнил баланс и кто не ушёл.
          </CardDescription>
        </div>
        {windows.length > 0 && (
          <Select
            ariaLabel="Срок оценки"
            value={String(chosen)}
            onValueChange={(v) => setDays(Number(v))}
            options={windows.map((w) => ({ value: String(w.days), label: WINDOW_LABEL[w.days] ?? `${w.days} дн.` }))}
            className="w-40"
          />
        )}
      </CardHeader>
      <CardContent className="space-y-4">
        {uplift.isPending ? (
          <Skeleton className="h-56 w-full" />
        ) : uplift.isError ? (
          <ErrorState error={uplift.error} onRetry={() => void uplift.refetch()} className="py-6" />
        ) : current ? (
          <>
            <p className={cn('rounded-lg px-3 py-2 text-[13px] leading-5', current.enough ? 'bg-muted' : 'bg-risk-medium-bg text-risk-medium')} data-testid="uplift-summary">
              {current.summary}
            </p>
            {current.treated_n + current.control_n > 0 && <UpliftChart window={current} />}
            {current.treated_n > 0 && current.control_n > 0 && (
              <Table aria-label="Эффект сценария">
                <TableHeader>
                  <TableRow>
                    <TableHead>Исход</TableHead>
                    <TableHead className="text-right">Затронутые</TableHead>
                    <TableHead className="text-right">Контроль</TableHead>
                    <TableHead className="text-right">Разница</TableHead>
                    <TableHead>95% интервал</TableHead>
                    <TableHead>Вывод</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {current.metrics.map((m) => (
                    <TableRow key={m.code}>
                      <TableCell className="text-[13px]">{m.title}</TableCell>
                      <TableCell className="num text-right text-[13px]">{m.treated} из {current.treated_n}</TableCell>
                      <TableCell className="num text-right text-[13px]">{m.control} из {current.control_n}</TableCell>
                      <TableCell className="num text-right text-[13px] font-medium">{formatPp(m.diff)}</TableCell>
                      <TableCell className="num text-[13px] text-muted-foreground">{formatInterval(m)}</TableCell>
                      <TableCell
                        className={cn(
                          'text-[13px]',
                          VERDICT[m.verdict].tone === 'good' && 'text-risk-low',
                          VERDICT[m.verdict].tone === 'bad' && 'text-risk-high',
                          VERDICT[m.verdict].tone === 'neutral' && 'text-muted-foreground',
                        )}
                      >
                        {VERDICT[m.verdict].label}
                      </TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            )}
            <p className="text-xs leading-5 text-subtle-foreground">
              Для каждого клиента берём первое срабатывание не позже чем {chosen} дн. назад и сравниваем с его состоянием сейчас. Интервал
              Агрести — Каффо; меньше {uplift.data?.min_group} клиентов в группе — честно пишем «мало данных».
            </p>
          </>
        ) : null}
      </CardContent>
    </Card>
  )
}

// --- журнал ---------------------------------------------------------------------------------

const RESULT_LABEL: Record<string, string> = {
  queued: 'в очереди',
  done: 'готово',
  mail_disabled: 'почта не настроена',
  no_email: 'нет email',
  not_found: 'клиент не найден',
}
const EMAIL_STATUS: Record<string, string> = { queued: 'в очереди', sending: 'отправляется', sent: 'отправлено', failed: 'не ушло' }

function Journal({ playbook }: { playbook: Playbook }) {
  const [tab, setTab] = useState<'hits' | 'runs'>('hits')
  const hits = useQuery({ queryKey: playbookKeys.hits(playbook.id), queryFn: ({ signal }) => playbooksApi.hits(playbook.id, signal), enabled: tab === 'hits' })
  const runs = useQuery({ queryKey: playbookKeys.runs(playbook.id), queryFn: ({ signal }) => playbooksApi.runs(playbook.id, signal), enabled: tab === 'runs' })
  const active = tab === 'hits' ? hits : runs
  return (
    <Card>
      <CardHeader className="flex flex-row flex-wrap items-center justify-between gap-3">
        <div>
          <CardTitle>Журнал срабатываний</CardTitle>
          <CardDescription>Кого задел сценарий и что с ним сделали</CardDescription>
        </div>
        <div className="flex gap-1 rounded-lg bg-muted p-0.5" role="tablist">
          {(
            [
              ['hits', 'Клиенты'],
              ['runs', 'Запуски'],
            ] as const
          ).map(([key, label]) => (
            <button
              key={key}
              type="button"
              role="tab"
              aria-selected={tab === key}
              onClick={() => setTab(key)}
              className={cn('rounded-md px-2.5 py-1 text-[13px]', tab === key ? 'bg-card font-medium shadow-card' : 'text-muted-foreground hover:text-foreground')}
            >
              {label}
            </button>
          ))}
        </div>
      </CardHeader>
      <CardContent>
        {active.isPending ? (
          <Skeleton className="h-24 w-full" />
        ) : active.isError ? (
          <ErrorState error={active.error} onRetry={() => void active.refetch()} className="py-6" />
        ) : tab === 'hits' ? (
          (hits.data?.items.length ?? 0) === 0 ? (
            <EmptyState icon={<Workflow />} title="Пока никого" description="В боевом режиме здесь появятся клиенты после пересчёта или ручного запуска." className="py-6" />
          ) : (
            <Table aria-label="Срабатывания сценария">
              <TableHeader>
                <TableRow>
                  <TableHead>Когда</TableHead>
                  <TableHead>Клиент</TableHead>
                  <TableHead>Риск</TableHead>
                  <TableHead>Группа</TableHead>
                  <TableHead>Что сделали</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {hits.data?.items.map((h) => (
                  <TableRow key={h.id}>
                    <TableCell className="num text-[13px] whitespace-nowrap">{formatDateTime(h.hit_at)}</TableCell>
                    <TableCell className="max-w-[220px] truncate text-[13px]">
                      <Link className="text-link hover:underline" to={`/customers/${h.customer_id}`}>
                        {h.email ?? `#${h.customer_id}`}
                      </Link>
                    </TableCell>
                    <TableCell>
                      <RiskBadge level={h.risk_level} />
                    </TableCell>
                    <TableCell className="text-[13px]">{h.group === 'control' ? 'контроль' : 'действие'}</TableCell>
                    <TableCell className="text-[13px] text-muted-foreground">
                      {h.group === 'control'
                        ? 'ничего — для сравнения'
                        : h.actions
                            .map((a) => {
                              const result = a.type === 'email' && h.email_status ? EMAIL_STATUS[h.email_status] ?? h.email_status : RESULT_LABEL[a.result] ?? a.result
                              return `${a.type === 'email' ? 'письмо' : a.type === 'status' ? 'статус' : 'уведомление'}: ${result}`
                            })
                            .join(', ')}
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          )
        ) : (runs.data?.items.length ?? 0) === 0 ? (
          <EmptyState icon={<Workflow />} title="Запусков ещё не было" className="py-6" />
        ) : (
          <Table aria-label="Запуски сценария">
            <TableHeader>
              <TableRow>
                <TableHead>Когда</TableHead>
                <TableHead>Повод</TableHead>
                <TableHead className="text-right">Подошли</TableHead>
                <TableHead className="text-right">Действие</TableHead>
                <TableHead className="text-right">Контроль</TableHead>
                <TableHead className="text-right">Писем</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {runs.data?.items.map((r) => (
                <TableRow key={r.id}>
                  <TableCell className="num text-[13px] whitespace-nowrap">{formatDateTime(r.created_at)}</TableCell>
                  <TableCell className="text-[13px]">
                    {r.cause === 'scores' ? 'после пересчёта' : `вручную, ${r.created_by ?? '—'}`}
                    {r.mode === 'simulation' && <span className="ml-1.5 text-xs text-risk-medium">симуляция</span>}
                  </TableCell>
                  <TableCell className="num text-right text-[13px]">{formatInt(r.matched)}</TableCell>
                  <TableCell className="num text-right text-[13px]">{formatInt(r.treated)}</TableCell>
                  <TableCell className="num text-right text-[13px]">{formatInt(r.control)}</TableCell>
                  <TableCell className="num text-right text-[13px]">{formatInt(r.emails)}</TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        )}
      </CardContent>
    </Card>
  )
}

// --- карточка сценария ----------------------------------------------------------------------

function Detail({
  playbook,
  reasons,
  canEdit,
  onEdit,
  onDeleted,
}: {
  playbook: Playbook
  reasons: Record<string, string>
  canEdit: boolean
  onEdit: () => void
  onDeleted: () => void
}) {
  const queryClient = useQueryClient()
  const [note, setNote] = useState<string | null>(null)
  const refresh = () => void queryClient.invalidateQueries({ queryKey: playbookKeys.all })
  const update = useMutation({
    mutationFn: (patch: Partial<PlaybookInput>) => playbooksApi.update(playbook.id, { ...toInput(playbook), ...patch }),
    onSuccess: refresh,
  })
  const run = useMutation({
    mutationFn: () => playbooksApi.run(playbook.id),
    onSuccess: (r) => {
      setNote(
        r.mode === 'simulation'
          ? `Симуляция: подошли ${formatInt(r.matched)}, действие получили бы ${formatInt(r.treated)}, в контроле ${formatInt(r.control)}. Ничего не отправлено.`
          : `Готово: действие выполнено для ${formatInt(r.treated)}, в контроле ${formatInt(r.control)}, писем в очереди ${formatInt(r.emails)}.`,
      )
      refresh()
    },
  })
  const remove = useMutation({
    mutationFn: () => playbooksApi.remove(playbook.id),
    onSuccess: () => {
      refresh()
      onDeleted()
    },
  })
  const error = update.error ?? run.error ?? remove.error
  const conditions = describeConditions(playbook.conditions, reasons)

  return (
    <div className="space-y-4" data-testid="playbook-detail">
      <Card>
        <CardHeader className="flex flex-row flex-wrap items-start justify-between gap-3">
          <div className="min-w-0">
            <CardTitle className="text-base">{playbook.name}</CardTitle>
            {playbook.description && <CardDescription>{playbook.description}</CardDescription>}
          </div>
          <StatusPills p={playbook} />
        </CardHeader>
        <CardContent className="space-y-4">
          <div className="grid gap-3 text-[13px] sm:grid-cols-2">
            <div>
              <p className="mb-1 font-medium">Если</p>
              <ul className="list-disc space-y-0.5 pl-5 text-muted-foreground">
                {conditions.map((c) => (
                  <li key={c}>{c}</li>
                ))}
              </ul>
            </div>
            <div>
              <p className="mb-1 font-medium">То</p>
              <ul className="list-disc space-y-0.5 pl-5 text-muted-foreground">
                {playbook.actions.map((a) => (
                  <li key={a.type}>{describeAction(a)}</li>
                ))}
              </ul>
              <p className="mt-2 text-xs text-subtle-foreground">
                Не больше {formatInt(playbook.max_per_day)} в день, одному клиенту не чаще раза в {playbook.cooldown_days} дн.
              </p>
            </div>
          </div>
          <div className="flex flex-wrap items-center gap-x-5 gap-y-3 border-t border-border pt-4">
            <label className="flex items-center gap-2 text-[13px]">
              <Switch
                checked={playbook.enabled}
                disabled={!canEdit || update.isPending}
                onCheckedChange={(enabled) => update.mutate({ enabled })}
                aria-label="Сценарий включён"
              />
              Включён
            </label>
            <label className="flex items-center gap-2 text-[13px]">
              <Switch
                checked={playbook.mode === 'live'}
                disabled={!canEdit || update.isPending}
                onCheckedChange={(live) => update.mutate({ mode: live ? 'live' : 'simulation' })}
                aria-label="Боевой режим"
              />
              Боевой режим
            </label>
            {canEdit && (
              <div className="ml-auto flex flex-wrap gap-2">
                <Button variant="outline" size="sm" onClick={() => run.mutate()} disabled={run.isPending}>
                  {playbook.mode === 'live' ? <Play /> : <FlaskConical />}
                  {run.isPending ? 'Запускаем…' : 'Запустить сейчас'}
                </Button>
                <Button variant="outline" size="sm" onClick={onEdit}>
                  <Pencil />
                  Изменить
                </Button>
                <ConfirmDialog
                  trigger={
                    <Button variant="ghost" size="sm" aria-label="Удалить сценарий">
                      <Trash2 />
                    </Button>
                  }
                  title="Удалить сценарий?"
                  description="Журнал и оценка эффекта удалятся вместе с ним. Отправленные письма останутся в истории клиентов."
                  confirmLabel="Удалить"
                  destructive
                  onConfirm={() => remove.mutate()}
                />
              </div>
            )}
          </div>
          {note && (
            <p role="status" className="rounded-lg bg-muted px-3 py-2 text-[13px]" data-testid="playbook-run-note">
              {note}
            </p>
          )}
          {error && <p role="alert" className="text-[13px] text-risk-high">{errorMessage(error)}</p>}
        </CardContent>
      </Card>
      <EffectCard playbook={playbook} />
      <Journal playbook={playbook} />
    </div>
  )
}

function Presets({ presets, onPick }: { presets: PlaybookPreset[]; onPick: (p: PlaybookPreset) => void }) {
  return (
    <div className="grid gap-3 md:grid-cols-3" data-testid="playbook-presets">
      {presets.map((p) => (
        <button
          key={p.code}
          type="button"
          onClick={() => onPick(p)}
          className="rounded-xl border border-border bg-card p-4 text-left shadow-card outline-none transition-colors hover:bg-accent focus-visible:ring-2 focus-visible:ring-ring"
        >
          <span className="flex items-center gap-2 text-sm font-medium">
            <Zap className="size-4 text-risk-medium" aria-hidden />
            {p.name}
          </span>
          <span className="mt-1.5 block text-[13px] leading-5 text-muted-foreground">{p.description}</span>
        </button>
      ))}
    </div>
  )
}

export default function PlaybooksPage() {
  const { isAdmin, user } = useAuth()
  const list = useQuery({ queryKey: playbookKeys.list, queryFn: ({ signal }) => playbooksApi.list(signal) })
  const [params, setParams] = useSearchParams()
  const [editor, setEditor] = useState<{ draft: PlaybookDraft; editing: Playbook | null; key: number } | null>(null)
  const data: PlaybookList | undefined = list.data
  const items = useMemo(() => data?.items ?? [], [data])
  const reasons = useMemo(() => Object.fromEntries((data?.reasons ?? []).map((r) => [r.code, r.title])), [data])
  const selectedId = Number(params.get('id')) || items[0]?.id || null
  const selected = items.find((p) => p.id === selectedId) ?? items[0] ?? null
  const select = (id: number | null) => setParams(id ? { id: String(id) } : {}, { replace: true })
  const openEditor = (draft: PlaybookDraft, editing: Playbook | null = null) => setEditor({ draft, editing, key: Date.now() })

  return (
    <>
      <PageHeader
        title="Сценарии"
        description="Удержание на автопилоте: правило «если — то» срабатывает после каждого пересчёта, а контрольная группа показывает, помогает ли оно на самом деле."
        actions={
          isAdmin && (
            <Button onClick={() => openEditor(emptyDraft(user.username))}>
              <Plus />
              Новый сценарий
            </Button>
          )
        }
      />
      {list.isPending ? (
        <Skeleton className="h-80 w-full rounded-xl" />
      ) : list.isError ? (
        <ErrorState error={list.error} onRetry={() => void list.refetch()} />
      ) : (
        <div className="space-y-6" data-testid="playbooks-list">
          {(items.length === 0 || isAdmin) && data && (
            <section className="space-y-2.5">
              <h2 className="text-sm font-semibold">{items.length === 0 ? 'Начните с готового сценария' : 'Готовые сценарии'}</h2>
              <Presets presets={data.presets} onPick={(p) => (isAdmin ? openEditor(presetDraft(p, user.username)) : undefined)} />
              {!isAdmin && <p className="text-[13px] text-muted-foreground">Создавать и включать сценарии может администратор.</p>}
            </section>
          )}
          {items.length > 0 && (
            <div className="grid gap-4 lg:grid-cols-[320px_1fr]">
              <nav aria-label="Сценарии" className="space-y-2">
                {items.map((p) => (
                  <button
                    key={p.id}
                    type="button"
                    onClick={() => select(p.id)}
                    aria-current={p.id === selected?.id ? 'true' : undefined}
                    className={cn(
                      'w-full rounded-xl border bg-card p-3.5 text-left shadow-card outline-none transition-colors hover:bg-accent focus-visible:ring-2 focus-visible:ring-ring',
                      p.id === selected?.id ? 'border-primary' : 'border-border',
                    )}
                  >
                    <span className="block truncate text-sm font-medium">{p.name}</span>
                    <span className="mt-1 block text-xs leading-5 text-muted-foreground">
                      {describeConditions(p.conditions, reasons).join(', ')}
                    </span>
                    <span className="mt-2 flex flex-wrap items-center justify-between gap-2">
                      <StatusPills p={p} />
                      <span className="num text-xs text-subtle-foreground">
                        действие {formatInt(p.stats.treated)} · контроль {formatInt(p.stats.control)}
                      </span>
                    </span>
                  </button>
                ))}
              </nav>
              {selected && (
                <Detail
                  key={selected.id}
                  playbook={selected}
                  reasons={reasons}
                  canEdit={isAdmin}
                  onEdit={() => openEditor(toDraft(toInput(selected), user.username), selected)}
                  onDeleted={() => select(null)}
                />
              )}
            </div>
          )}
        </div>
      )}
      {editor && data && (
        <Editor
          key={editor.key}
          open
          onOpenChange={(open) => !open && setEditor(null)}
          initial={editor.draft}
          editing={editor.editing}
          reasons={data.reasons}
          mailEnabled={data.mail_enabled}
          onSaved={(saved) => {
            setEditor(null)
            select(saved.id)
          }}
        />
      )}
    </>
  )
}

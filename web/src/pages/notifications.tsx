import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { CheckCircle2, Loader2, Mail, MessageCircle, MonitorSmartphone, RotateCcw, Send, Unlink, XCircle } from 'lucide-react'
import { useState, type ReactNode } from 'react'
import { Link } from 'react-router'
import {
  alertsApi,
  alertsQueryKey,
  inboxQueryKey,
  prefsQueryKey,
  subscribersQueryKey,
  type AlertChannel,
  type AlertEventCode,
  type AlertEventDefinition,
  type AlertEventPreference,
  type AlertPreferencesInput,
  type AlertPreferencesResponse,
  type TestResult,
} from '@/api/alerts'
import { errorMessage } from '@/api/client'
import { PageHeader } from '@/components/page-header'
import { EmptyState, ErrorState } from '@/components/states'
import { ConfirmDialog } from '@/components/ui/alert-dialog'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Select } from '@/components/ui/select'
import { Skeleton } from '@/components/ui/skeleton'
import { Switch } from '@/components/ui/switch'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table'
import { Tooltip } from '@/components/ui/tooltip'
import { CHANNEL_LABEL, KIND_LABEL, periodLabel, previewTitle, timezoneOptions, unitSuffix } from '@/lib/alerts'
import { formatDateTime } from '@/lib/format'
import { cn } from '@/lib/utils'
import { useAuth } from '@/providers/auth'

interface Form {
  email: string
  timezone: string
  quiet_from: string
  quiet_to: string
  digest_enabled: boolean
  digest_time: string
  digest_channels: ('email' | 'telegram')[]
  events: Record<string, AlertEventPreference>
}

type Period = AlertEventPreference['period_h']

function defaultEvent(def: AlertEventDefinition): AlertEventPreference {
  return {
    enabled: def.default_enabled,
    threshold: def.default_threshold,
    period_h: (def.default_period_h ?? null) as Period,
    channels: ['panel'],
  }
}

const CHANNELS: AlertChannel[] = ['panel', 'email', 'telegram']
const CHANNEL_ICON: Record<AlertChannel, ReactNode> = {
  panel: <MonitorSmartphone />,
  email: <Mail />,
  telegram: <MessageCircle />,
}

function toForm(data: AlertPreferencesResponse, fallbackEmail: string | null): Form {
  const p = data.preferences
  return {
    email: p.email ?? fallbackEmail ?? '',
    timezone: p.timezone ?? 'Europe/Moscow',
    quiet_from: p.quiet_from ?? '',
    quiet_to: p.quiet_to ?? '',
    digest_enabled: p.digest_enabled ?? false,
    digest_time: p.digest_time ?? '09:00',
    digest_channels: p.digest_channels ?? ['email'],
    events: Object.fromEntries(data.catalog.map((def) => [def.code, p.events?.[def.code] ?? defaultEvent(def)])),
  }
}

function toBody(form: Form): AlertPreferencesInput {
  return {
    ...form,
    email: form.email.trim() || null,
    quiet_from: form.quiet_from || null,
    quiet_to: form.quiet_to || null,
  }
}

/** Проверки до отправки: те же, что делает сервер, но с понятными подсказками у полей. */
function validate(form: Form, catalog: AlertEventDefinition[]): Record<string, string> {
  const errors: Record<string, string> = {}
  const email = form.email.trim()
  if (email && !/^[^@\s]+@[^@\s]+\.[^@\s]+$/.test(email)) errors.email = 'Похоже, в адресе опечатка'
  const wantsEmail =
    (form.digest_enabled && form.digest_channels.includes('email')) ||
    Object.values(form.events).some((e) => e.enabled && e.channels.includes('email'))
  if (wantsEmail && !email) errors.email = 'Чтобы получать письма, укажите адрес'
  if (Boolean(form.quiet_from) !== Boolean(form.quiet_to)) errors.quiet = 'Укажите и начало, и конец'
  for (const def of catalog) {
    const value = form.events[def.code]?.threshold
    if (value === undefined || !Number.isFinite(value) || value < def.min_threshold || value > def.max_threshold) {
      errors[def.code] = `От ${def.min_threshold} до ${def.max_threshold.toLocaleString('ru-RU')}`
    }
  }
  return errors
}

function ChannelToggle({
  channel,
  active,
  available,
  reason,
  onToggle,
}: {
  channel: AlertChannel
  active: boolean
  available: boolean
  reason?: string | null
  onToggle: () => void
}) {
  const button = (
    <button
      type="button"
      aria-pressed={active}
      disabled={!available && !active}
      onClick={onToggle}
      className={cn(
        'inline-flex h-7 items-center gap-1.5 rounded-full border px-2.5 text-xs font-medium transition-colors outline-none focus-visible:ring-2 focus-visible:ring-ring [&_svg]:size-3.5',
        active ? 'border-primary bg-primary text-primary-foreground' : 'border-border bg-card text-muted-foreground hover:bg-accent',
        !available && 'opacity-50',
      )}
    >
      {CHANNEL_ICON[channel]}
      {CHANNEL_LABEL[channel]}
    </button>
  )
  if (available || !reason) return button
  return (
    <Tooltip content={reason}>
      <span tabIndex={0}>{button}</span>
    </Tooltip>
  )
}

function EventRow({
  def,
  value,
  periods,
  error,
  availability,
  onChange,
}: {
  def: AlertEventDefinition
  value: AlertEventPreference
  periods: number[]
  error?: string
  availability: Record<AlertChannel, { available: boolean; reason?: string | null }>
  onChange: (next: AlertEventPreference) => void
}) {
  const id = `event-${def.code}`
  return (
    <li className="py-4 first:pt-0 last:pb-0" data-testid={id}>
      <div className="flex items-start gap-3">
        <Switch
          id={id}
          checked={value.enabled}
          onCheckedChange={(enabled) => onChange({ ...value, enabled })}
          aria-label={def.title}
          className="mt-0.5"
        />
        <div className="min-w-0 flex-1">
          <Label htmlFor={id} className="text-sm font-medium">
            {def.title}
          </Label>
          <p className="mt-0.5 text-[13px] leading-5 text-muted-foreground">{def.description}</p>
          {value.enabled && (
            <div className="mt-3 space-y-3">
              <div className="flex flex-wrap items-center gap-2 text-[13px]">
                <span className="text-muted-foreground">Порог</span>
                <div className="relative">
                  <Input
                    type="number"
                    inputMode="decimal"
                    aria-label={`Порог: ${def.title}`}
                    aria-invalid={Boolean(error)}
                    value={Number.isFinite(value.threshold) ? value.threshold : ''}
                    min={def.min_threshold}
                    max={def.max_threshold}
                    onChange={(e) => onChange({ ...value, threshold: e.target.value === '' ? NaN : Number(e.target.value) })}
                    className="h-8 w-28 pr-14 num"
                  />
                  <span className="pointer-events-none absolute top-1/2 right-2.5 -translate-y-1/2 text-xs text-muted-foreground">
                    {unitSuffix(def.unit)}
                  </span>
                </div>
                {def.default_period_h != null && (
                  <Select
                    ariaLabel={`Период: ${def.title}`}
                    value={String(value.period_h ?? def.default_period_h)}
                    onValueChange={(v) => onChange({ ...value, period_h: Number(v) as Period })}
                    options={periods.map((h) => ({ value: String(h), label: periodLabel(h) }))}
                    className="w-32"
                  />
                )}
                {error && <span className="text-xs text-risk-high">{error}</span>}
              </div>
              <div className="flex flex-wrap items-center gap-1.5" role="group" aria-label={`Каналы: ${def.title}`}>
                {CHANNELS.map((channel) => (
                  <ChannelToggle
                    key={channel}
                    channel={channel}
                    active={value.channels.includes(channel)}
                    available={availability[channel].available}
                    reason={availability[channel].reason}
                    onToggle={() =>
                      onChange({
                        ...value,
                        channels: value.channels.includes(channel)
                          ? value.channels.filter((c) => c !== channel)
                          : [...value.channels, channel],
                      })
                    }
                  />
                ))}
                {value.channels.length === 0 && (
                  <span className="text-xs text-muted-foreground">Не выбран ни один канал — событие только в журнале</span>
                )}
              </div>
              <p className="rounded-md bg-muted/60 px-3 py-2 text-xs leading-5 text-muted-foreground">
                Пример: «{previewTitle(def.code as AlertEventCode, value.threshold, value.period_h)}»
              </p>
            </div>
          )}
        </div>
      </div>
    </li>
  )
}

function TelegramBlock({ data }: { data: AlertPreferencesResponse }) {
  const queryClient = useQueryClient()
  const tg = data.channels.telegram
  const link = useMutation({
    mutationFn: alertsApi.telegramLink,
    onSuccess: ({ url }) => window.open(url, '_blank', 'noopener,noreferrer'),
  })
  const unlink = useMutation({
    mutationFn: alertsApi.telegramUnlink,
    onSuccess: () => void queryClient.invalidateQueries({ queryKey: prefsQueryKey }),
  })
  if (!tg.available) {
    return (
      <p className="text-[13px] leading-5 text-muted-foreground" data-testid="telegram-status">
        Бот не настроен. Администратору нужно задать <code className="text-xs">TELEGRAM_BOT_TOKEN</code> — после
        этого здесь появится кнопка привязки.
      </p>
    )
  }
  if (tg.linked) {
    return (
      <div className="flex flex-wrap items-center gap-3" data-testid="telegram-status">
        <span className="inline-flex items-center gap-1.5 text-[13px]">
          <CheckCircle2 className="size-4 text-risk-low" />
          Привязан{tg.tg_username ? ` к @${tg.tg_username}` : ''}. В чате работают /top, /customer, /summary, /mute.
        </span>
        <Button variant="outline" size="sm" onClick={() => unlink.mutate()} disabled={unlink.isPending}>
          <Unlink />
          Отвязать
        </Button>
      </div>
    )
  }
  return (
    <div className="space-y-2" data-testid="telegram-status">
      <div className="flex flex-wrap items-center gap-3">
        <Button variant="outline" size="sm" onClick={() => link.mutate()} disabled={link.isPending}>
          {link.isPending ? <Loader2 className="animate-spin" /> : <MessageCircle />}
          Привязать Telegram
        </Button>
        <span className="text-[13px] text-muted-foreground">
          Откроется чат с ботом{tg.bot_username ? ` @${tg.bot_username}` : ''} — нажмите Start. Ссылка действует 10 минут.
        </span>
      </div>
      {link.isError && <p className="text-[13px] text-risk-high">{errorMessage(link.error)}</p>}
      {link.isSuccess && (
        <p className="text-[13px] text-muted-foreground">
          Если чат не открылся, <a className="underline" href={link.data.url} target="_blank" rel="noopener noreferrer">откройте ссылку</a>.
          После Start обновите страницу.
        </p>
      )}
    </div>
  )
}

function TestResults({ results }: { results: TestResult[] }) {
  return (
    <ul className="space-y-1 text-[13px]" data-testid="test-results">
      {results.map((r) => (
        <li key={r.channel} className="flex items-center gap-2">
          {r.ok ? <CheckCircle2 className="size-4 text-risk-low" /> : <XCircle className="size-4 text-muted-foreground" />}
          <span className="font-medium">{CHANNEL_LABEL[r.channel]}:</span>
          <span className="text-muted-foreground">{r.ok ? 'отправлено' : r.reason}</span>
        </li>
      ))}
    </ul>
  )
}

function Journal() {
  const query = useQuery({ queryKey: alertsQueryKey, queryFn: ({ signal }) => alertsApi.journal(signal) })
  return (
    <Card>
      <CardHeader>
        <CardTitle>Журнал событий</CardTitle>
        <CardDescription>
          Общий для команды: критические события по порогам по умолчанию, независимо от личных настроек.
        </CardDescription>
      </CardHeader>
      <CardContent>
        {query.isPending ? (
          <Skeleton className="h-24 rounded-lg" />
        ) : query.isError ? (
          <ErrorState error={query.error} title="Журнал недоступен" onRetry={() => void query.refetch()} />
        ) : query.data.items.length === 0 ? (
          <p className="text-[13px] text-muted-foreground">Критических событий пока не было.</p>
        ) : (
          <ul className="divide-y divide-border" data-testid="alerts-journal">
            {query.data.items.slice(0, 10).map((item) => (
              <li key={item.id} className="py-2.5 first:pt-0">
                <Link to={item.link} className="group block rounded-md outline-none focus-visible:ring-2 focus-visible:ring-ring">
                  <span className="flex items-center gap-2 text-xs text-muted-foreground">
                    <span className="font-medium text-foreground">{KIND_LABEL[item.kind]}</span>
                    <span className="ml-auto num">{item.created_at ? formatDateTime(item.created_at) : ''}</span>
                  </span>
                  <span className="mt-0.5 block text-[13px] leading-5 group-hover:underline">{item.message}</span>
                </Link>
              </li>
            ))}
          </ul>
        )}
      </CardContent>
    </Card>
  )
}

function TeamSettings() {
  const queryClient = useQueryClient()
  const query = useQuery({ queryKey: subscribersQueryKey, queryFn: ({ signal }) => alertsApi.subscribers(signal) })
  const reset = useMutation({
    mutationFn: alertsApi.resetSubscriber,
    onSuccess: () => void queryClient.invalidateQueries({ queryKey: ['alerts'] }),
  })
  return (
    <Card>
      <CardHeader>
        <CardTitle>Настройки команды</CardTitle>
        <CardDescription>Видно только администратору: кто какие уведомления получает. Сброс возвращает значения по умолчанию.</CardDescription>
      </CardHeader>
      <CardContent>
        {query.isPending ? (
          <Skeleton className="h-24 rounded-lg" />
        ) : query.isError ? (
          <ErrorState error={query.error} onRetry={() => void query.refetch()} />
        ) : query.data.items.length === 0 ? (
          <p className="text-[13px] text-muted-foreground">Никто ещё не открывал уведомления.</p>
        ) : (
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Пользователь</TableHead>
                <TableHead>Событий</TableHead>
                <TableHead>Каналы</TableHead>
                <TableHead>Сводка</TableHead>
                <TableHead className="text-right">
                  <span className="sr-only">Действия</span>
                </TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {query.data.items.map((s) => (
                <TableRow key={s.username}>
                  <TableCell>
                    <span className="font-medium">{s.username}</span>
                    {s.email && <span className="block text-xs text-muted-foreground">{s.email}</span>}
                  </TableCell>
                  <TableCell className="num">{s.enabled_events.length}</TableCell>
                  <TableCell className="text-[13px]">
                    {s.channels.map((c) => CHANNEL_LABEL[c]).join(', ') || '—'}
                    {s.telegram_linked && <span className="block text-xs text-muted-foreground">Telegram привязан</span>}
                  </TableCell>
                  <TableCell className="text-[13px]">{s.digest_enabled ? 'да' : 'нет'}</TableCell>
                  <TableCell className="text-right">
                    <ConfirmDialog
                      title={`Сбросить настройки ${s.username}?`}
                      description="Пороги, каналы, тихие часы и сводка вернутся к значениям по умолчанию: только критические события и только в панели. Привязка Telegram останется."
                      confirmLabel="Сбросить"
                      destructive
                      onConfirm={() => reset.mutate(s.username)}
                      trigger={
                        <Button variant="ghost" size="sm">
                          <RotateCcw />
                          Сбросить
                        </Button>
                      }
                    />
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        )}
      </CardContent>
    </Card>
  )
}

function SettingsForm({ data }: { data: AlertPreferencesResponse }) {
  const queryClient = useQueryClient()
  const { user } = useAuth()
  const meEmail = (user as { email?: string | null }).email ?? null
  const [form, setForm] = useState<Form>(() => toForm(data, meEmail))
  const [saved, setSaved] = useState<Form>(() => toForm(data, null))
  const errors = validate(form, data.catalog)
  const dirty = JSON.stringify(toBody(form)) !== JSON.stringify(toBody(saved))
  const availability = data.channels

  const save = useMutation({
    mutationFn: () => alertsApi.savePreferences(toBody(form)),
    onSuccess: (next) => {
      queryClient.setQueryData(prefsQueryKey, next)
      const fresh = toForm(next, null)
      setForm(fresh)
      setSaved(fresh)
    },
  })
  const test = useMutation({
    mutationFn: alertsApi.sendTest,
    onSuccess: () => void queryClient.invalidateQueries({ queryKey: inboxQueryKey }),
  })

  const setEvent = (code: string, next: AlertEventPreference) =>
    setForm((f) => ({ ...f, events: { ...f.events, [code]: next } }))
  const toggleDigest = (channel: 'email' | 'telegram') =>
    setForm((f) => ({
      ...f,
      digest_channels: f.digest_channels.includes(channel)
        ? f.digest_channels.filter((c) => c !== channel)
        : [...f.digest_channels, channel],
    }))

  return (
    <div className="grid gap-6 lg:grid-cols-[minmax(0,1fr)_360px]">
      <div className="space-y-6">
        <Card>
          <CardHeader>
            <CardTitle>О чём сообщать</CardTitle>
            <CardDescription>
              Только критические события. Порог у каждого свой; одно и то же событие приходит не чаще раза в час.
            </CardDescription>
          </CardHeader>
          <CardContent>
            <ul className="divide-y divide-border">
              {data.catalog.map((def) => (
                <EventRow
                  key={def.code}
                  def={def}
                  value={form.events[def.code] ?? defaultEvent(def)}
                  periods={data.periods_h}
                  error={errors[def.code]}
                  availability={availability}
                  onChange={(next) => setEvent(def.code, next)}
                />
              ))}
            </ul>
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <div className="flex items-center justify-between gap-3">
              <CardTitle>Ежедневная сводка</CardTitle>
              <Switch
                checked={form.digest_enabled}
                onCheckedChange={(digest_enabled) => setForm((f) => ({ ...f, digest_enabled }))}
                aria-label="Ежедневная сводка"
              />
            </div>
            <CardDescription>
              Раз в день: сколько клиентов в высоком риске, ожидаемая потеря и её изменение за сутки, самые дорогие клиенты.
            </CardDescription>
          </CardHeader>
          {form.digest_enabled && (
            <CardContent className="flex flex-wrap items-center gap-3">
              <Label htmlFor="digest-time" className="text-[13px] text-muted-foreground">
                Присылать в
              </Label>
              <Input
                id="digest-time"
                type="time"
                value={form.digest_time}
                onChange={(e) => setForm((f) => ({ ...f, digest_time: e.target.value }))}
                className="h-8 w-28 num"
              />
              {(['email', 'telegram'] as const).map((channel) => (
                <ChannelToggle
                  key={channel}
                  channel={channel}
                  active={form.digest_channels.includes(channel)}
                  available={availability[channel].available}
                  reason={availability[channel].reason}
                  onToggle={() => toggleDigest(channel)}
                />
              ))}
            </CardContent>
          )}
        </Card>
      </div>

      <div className="space-y-6">
        <Card>
          <CardHeader>
            <CardTitle>Куда присылать</CardTitle>
          </CardHeader>
          <CardContent className="space-y-5">
            <div className="space-y-1.5">
              <Label htmlFor="notify-email">Почта</Label>
              <Input
                id="notify-email"
                type="email"
                autoComplete="email"
                placeholder="name@company.ru"
                value={form.email}
                aria-invalid={Boolean(errors.email)}
                onChange={(e) => setForm((f) => ({ ...f, email: e.target.value }))}
              />
              {errors.email ? (
                <p className="text-xs text-risk-high">{errors.email}</p>
              ) : !availability.email.available ? (
                <p className="text-xs text-muted-foreground">{availability.email.reason}</p>
              ) : null}
            </div>
            <div className="space-y-1.5">
              <span className="text-sm font-medium">Telegram</span>
              <TelegramBlock data={data} />
            </div>
            <div className="space-y-1.5">
              <span className="text-sm font-medium">Тихие часы</span>
              <p className="text-xs text-muted-foreground">В это время письма и сообщения ждут утра, в панели всё видно сразу.</p>
              <div className="flex flex-wrap items-center gap-2">
                <Input
                  type="time"
                  aria-label="Тихие часы: начало"
                  value={form.quiet_from}
                  onChange={(e) => setForm((f) => ({ ...f, quiet_from: e.target.value }))}
                  className="h-8 w-28 num"
                />
                <span className="text-muted-foreground">—</span>
                <Input
                  type="time"
                  aria-label="Тихие часы: конец"
                  value={form.quiet_to}
                  onChange={(e) => setForm((f) => ({ ...f, quiet_to: e.target.value }))}
                  className="h-8 w-28 num"
                />
              </div>
              {errors.quiet && <p className="text-xs text-risk-high">{errors.quiet}</p>}
              <Select
                ariaLabel="Часовой пояс"
                value={form.timezone}
                onValueChange={(timezone) => setForm((f) => ({ ...f, timezone }))}
                options={timezoneOptions(form.timezone)}
                className="w-full"
              />
            </div>
          </CardContent>
        </Card>

        <Card className="lg:sticky lg:top-20">
          <CardContent className="space-y-3 pt-5">
            <Button
              className="w-full"
              disabled={!dirty || Object.keys(errors).length > 0 || save.isPending}
              onClick={() => save.mutate()}
              data-testid="save-preferences"
            >
              {save.isPending && <Loader2 className="animate-spin" />}
              {dirty ? 'Сохранить' : save.isSuccess ? 'Сохранено' : 'Изменений нет'}
            </Button>
            {save.isError && <p className="text-[13px] text-risk-high">{errorMessage(save.error)}</p>}
            <Button variant="outline" className="w-full" onClick={() => test.mutate()} disabled={test.isPending || dirty}>
              {test.isPending ? <Loader2 className="animate-spin" /> : <Send />}
              Отправить тестовое уведомление
            </Button>
            {dirty && <p className="text-xs text-muted-foreground">Сначала сохраните изменения.</p>}
            {test.isError && <p className="text-[13px] text-risk-high">{errorMessage(test.error)}</p>}
            {test.data && <TestResults results={test.data.results} />}
          </CardContent>
        </Card>
      </div>
    </div>
  )
}

export default function NotificationsPage() {
  const { isAdmin } = useAuth()
  const query = useQuery({ queryKey: prefsQueryKey, queryFn: ({ signal }) => alertsApi.preferences(signal) })
  return (
    <>
      <PageHeader
        title="Уведомления"
        description="Выберите, о каких критических событиях узнавать, с какими порогами и куда: в панели, на почту или в Telegram."
      />
      {query.isPending ? (
        <div className="grid gap-6 lg:grid-cols-[minmax(0,1fr)_360px]" aria-busy="true" aria-label="Загрузка">
          <Skeleton className="h-96 rounded-xl" />
          <Skeleton className="h-72 rounded-xl" />
        </div>
      ) : query.isError ? (
        <ErrorState error={query.error} title="Настройки уведомлений недоступны" onRetry={() => void query.refetch()} />
      ) : query.data.catalog.length === 0 ? (
        <EmptyState title="Нет событий для настройки" />
      ) : (
        <SettingsForm key={query.data.preferences.username} data={query.data} />
      )}
      <div className={cn('mt-6 grid gap-6', isAdmin && 'xl:grid-cols-2')}>
        <Journal />
        {isAdmin && <TeamSettings />}
      </div>
    </>
  )
}

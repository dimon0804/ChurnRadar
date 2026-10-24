import { useMutation, useQuery, useQueryClient, type UseMutationResult } from '@tanstack/react-query'
import {
  AlertTriangle,
  Check,
  Copy,
  Download,
  ExternalLink,
  KeyRound,
  Loader2,
  Mail,
  Plus,
  Send,
  ShieldCheck,
  TerminalSquare,
} from 'lucide-react'
import { useId, useState, type FormEvent, type ReactNode } from 'react'
import { errorMessage } from '@/api/client'
import { api, queryKeys, WEEKLY_REPORT_PDF_URL } from '@/api/endpoints'
import type { ApiToken, ApiTokenCreated, ApiTokenRole, ReportSchedule, WeeklyReportSent } from '@/api/types'
import { PageHeader } from '@/components/page-header'
import { EmptyState, ErrorState } from '@/components/states'
import { ConfirmDialog } from '@/components/ui/alert-dialog'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Textarea } from '@/components/ui/dialog'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Dialog } from '@/components/ui/modal'
import { Select } from '@/components/ui/select'
import { Skeleton } from '@/components/ui/skeleton'
import { Switch } from '@/components/ui/switch'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table'
import { Tooltip } from '@/components/ui/tooltip'
import { useNow } from '@/hooks/use-now'
import { formatDateTime } from '@/lib/format'
import {
  curlExample,
  describeSchedule,
  EXPIRY_OPTIONS,
  expiresSoon,
  expiryDays,
  formatAllowedIps,
  formatExpiry,
  formatLastUsed,
  parseEmails,
  parseIpList,
  parseRateLimit,
  SEND_TIME_RE,
  TOKEN_ROLE_HINT,
  TOKEN_ROLE_LABEL,
  TOKEN_ROLE_OPTIONS,
  TOKEN_STATUS_LABEL,
  tokenNameError,
  WEEKDAYS,
  type ExpiryChoice,
} from '@/lib/integrations'
import { SWAGGER_PATH } from '@/lib/links'
import { cn } from '@/lib/utils'

function FormError({ error }: { error: unknown }) {
  if (!error) return null
  return (
    <p role="alert" className="rounded-md border border-risk-high/20 bg-risk-high-bg px-3 py-2 text-[13px] text-risk-high">
      {typeof error === 'string' ? error : errorMessage(error)}
    </p>
  )
}

function Field({ label, hint, error, children, id }: { label: string; hint?: ReactNode; error?: string | null; children: ReactNode; id: string }) {
  return (
    <div className="space-y-1.5">
      <Label htmlFor={id}>{label}</Label>
      {children}
      {error ? (
        <p className="text-xs leading-5 text-risk-high">{error}</p>
      ) : (
        hint && <p className="text-xs leading-5 text-muted-foreground">{hint}</p>
      )}
    </div>
  )
}

function CopyButton({ value, label, className }: { value: string; label: string; className?: string }) {
  const [copied, setCopied] = useState(false)
  return (
    <Tooltip content={copied ? 'Скопировано' : label}>
      <Button
        type="button"
        variant="outline"
        size="icon"
        aria-label={label}
        className={className}
        onClick={() => {
          void navigator.clipboard?.writeText(value).then(
            () => {
              setCopied(true)
              window.setTimeout(() => setCopied(false), 2000)
            },
            () => undefined,
          )
        }}
      >
        {copied ? <Check /> : <Copy />}
      </Button>
    </Tooltip>
  )
}

function TokenStatusBadge({ token, now }: { token: ApiToken; now: number }) {
  const soon = expiresSoon(token, now)
  return (
    <span
      className={cn(
        'inline-flex h-6 items-center rounded-full px-2 text-xs font-medium whitespace-nowrap',
        token.status === 'active' && !soon && 'bg-risk-low-bg text-risk-low',
        token.status === 'active' && soon && 'bg-risk-medium-bg text-risk-medium',
        token.status !== 'active' && 'bg-muted text-muted-foreground',
      )}
    >
      {soon ? 'Скоро истечёт' : TOKEN_STATUS_LABEL[token.status]}
    </span>
  )
}

// ——— выпуск токена

function CreateTokenDialog({
  open,
  onOpenChange,
  onCreated,
}: {
  open: boolean
  onOpenChange: (open: boolean) => void
  onCreated: (created: ApiTokenCreated) => void
}) {
  const queryClient = useQueryClient()
  const ids = { name: useId(), ips: useId(), rate: useId(), form: useId() }
  const [name, setName] = useState('')
  const [role, setRole] = useState<ApiTokenRole>('analyst')
  const [expiry, setExpiry] = useState<ExpiryChoice>('90')
  const [ipsText, setIpsText] = useState('')
  const [rateText, setRateText] = useState('')
  const [touched, setTouched] = useState(false)

  const create = useMutation({
    mutationFn: api.createApiToken,
    onSuccess: (data) => {
      void queryClient.invalidateQueries({ queryKey: queryKeys.apiTokens })
      void queryClient.invalidateQueries({ queryKey: queryKeys.audit })
      onCreated(data)
      reset()
      onOpenChange(false)
    },
  })

  const reset = () => {
    setName('')
    setRole('analyst')
    setExpiry('90')
    setIpsText('')
    setRateText('')
    setTouched(false)
    create.reset()
  }

  const nameError = tokenNameError(name)
  const ips = parseIpList(ipsText)
  const ipsError = ips.invalid.length
    ? `Не похоже на IP или подсеть: ${ips.invalid.slice(0, 3).join(', ')}${ips.invalid.length > 3 ? '…' : ''}`
    : ips.ips.length > 50
      ? 'Не больше 50 адресов.'
      : null
  const rate = parseRateLimit(rateText)

  const submit = (event: FormEvent) => {
    event.preventDefault()
    setTouched(true)
    if (nameError || ipsError || rate.error) return
    create.mutate({
      name: name.trim(),
      role,
      expires_in_days: expiryDays(expiry),
      allowed_ips: ips.ips,
      rate_limit_per_min: rate.value,
    })
  }

  return (
    <Dialog
      open={open}
      onOpenChange={(next) => {
        if (!next) reset()
        onOpenChange(next)
      }}
      title="Новый API-токен"
      description="Токен нужен внешней системе: CRM, биллингу, BI. Он ходит в API с правами выбранной роли и виден в журнале под своим именем."
      footer={
        <>
          <Button variant="outline" onClick={() => onOpenChange(false)}>
            Отмена
          </Button>
          <Button type="submit" form={ids.form} disabled={create.isPending}>
            {create.isPending && <Loader2 className="animate-spin" />}
            Выпустить токен
          </Button>
        </>
      }
    >
      <form id={ids.form} className="space-y-4" onSubmit={submit} noValidate>
        <FormError error={create.error} />
        <Field
          id={ids.name}
          label="Имя"
          hint="Латиница, цифры, точка, дефис, подчёркивание. Например, aeza-billing."
          error={touched ? nameError : null}
        >
          <Input
            id={ids.name}
            value={name}
            onChange={(e) => setName(e.target.value)}
            aria-invalid={touched && !!nameError}
            maxLength={64}
            autoComplete="off"
            autoCapitalize="none"
            spellCheck={false}
            autoFocus
          />
        </Field>
        <div className="grid gap-4 sm:grid-cols-2">
          <div className="space-y-1.5">
            <Label>Роль</Label>
            <Select
              value={role}
              onValueChange={(v) => setRole(v as ApiTokenRole)}
              options={TOKEN_ROLE_OPTIONS}
              ariaLabel="Роль"
              className="h-9 w-full"
            />
          </div>
          <div className="space-y-1.5">
            <Label>Срок действия</Label>
            <Select
              value={expiry}
              onValueChange={(v) => setExpiry(v as ExpiryChoice)}
              options={EXPIRY_OPTIONS.map((o) => ({ value: o.value, label: o.label }))}
              ariaLabel="Срок действия"
              className="h-9 w-full"
            />
          </div>
        </div>
        <p className="-mt-2 text-xs leading-5 text-muted-foreground">{TOKEN_ROLE_HINT[role]}</p>
        <Field
          id={ids.ips}
          label="Разрешённые IP"
          hint="По одному в строке или через запятую, можно подсети: 10.0.0.0/8. Пусто — с любого адреса."
          error={touched || ips.invalid.length > 0 ? ipsError : null}
        >
          <Textarea
            id={ids.ips}
            value={ipsText}
            onChange={(e) => setIpsText(e.target.value)}
            rows={3}
            placeholder={'203.0.113.10\n10.0.0.0/8'}
            className="font-mono text-[13px]"
            spellCheck={false}
          />
        </Field>
        <Field
          id={ids.rate}
          label="Лимит запросов в минуту"
          hint="Необязательно. Если пусто, действует общий лимит для токенов."
          error={rate.error}
        >
          <Input
            id={ids.rate}
            value={rateText}
            onChange={(e) => setRateText(e.target.value)}
            inputMode="numeric"
            placeholder="Например, 120"
            className="max-w-40"
            aria-invalid={!!rate.error}
          />
        </Field>
      </form>
    </Dialog>
  )
}

/** Токен показываем один раз: в базе лежит только его хеш. */
function SecretDialog({ created, onClose }: { created: ApiTokenCreated | null; onClose: () => void }) {
  const origin = typeof window === 'undefined' ? '' : window.location.origin
  return (
    <Dialog
      open={created !== null}
      onOpenChange={(open) => !open && onClose()}
      title={created ? `Токен ${created.token.name} готов` : ''}
      description="Скопируйте его сейчас и сохраните в настройках внешней системы. Больше мы его не покажем: если потеряется, выпустите новый, а этот отзовите."
      footer={<Button onClick={onClose}>Я сохранил токен</Button>}
    >
      {created && (
        <div className="space-y-4">
          <div className="flex items-center gap-2">
            <code
              data-testid="token-secret"
              className="min-w-0 flex-1 rounded-md border border-border bg-muted px-3 py-2 font-mono text-[13px] break-all select-all"
            >
              {created.secret}
            </code>
            <CopyButton value={created.secret} label="Скопировать токен" />
          </div>
          <p className="flex gap-2 rounded-md border border-risk-medium/25 bg-risk-medium-bg px-3 py-2 text-[13px] leading-5 text-risk-medium">
            <AlertTriangle className="mt-0.5 size-4 shrink-0" />
            Токен даёт доступ к данным клиентов. Не пересылайте его в открытых чатах.
          </p>
          <div className="space-y-1.5">
            <p className="text-[13px] font-medium">Проверить можно так</p>
            <pre className="overflow-x-auto rounded-md bg-muted px-3 py-2 font-mono text-xs leading-5">
              {curlExample(origin, created.secret)}
            </pre>
          </div>
        </div>
      )}
    </Dialog>
  )
}

function TokensCard() {
  const queryClient = useQueryClient()
  const now = useNow(60_000)
  const [creating, setCreating] = useState(false)
  const [created, setCreated] = useState<ApiTokenCreated | null>(null)
  const tokens = useQuery({ queryKey: queryKeys.apiTokens, queryFn: ({ signal }) => api.apiTokens(signal) })

  const revoke = useMutation({
    mutationFn: (token: ApiToken) => api.revokeApiToken(token.id),
    onSettled: () => {
      void queryClient.invalidateQueries({ queryKey: queryKeys.apiTokens })
      void queryClient.invalidateQueries({ queryKey: queryKeys.audit })
    },
  })

  const items = tokens.data?.items ?? []
  const active = items.filter((t) => t.status === 'active').length

  return (
    <Card>
      <div className="flex flex-wrap items-center gap-3 border-b border-border px-5 py-3.5">
        <div className="min-w-0 flex-1">
          <h2 className="text-[15px] font-semibold tracking-[-0.01em]">API-токены</h2>
          <p className="mt-0.5 text-[13px] text-muted-foreground">
            {tokens.data ? (active ? `Действуют: ${active} из ${items.length}` : 'Действующих токенов нет') : 'Доступ внешних систем к API'}
          </p>
        </div>
        {revoke.isError && <p className="text-[13px] text-risk-high">{errorMessage(revoke.error)}</p>}
        <Button onClick={() => setCreating(true)}>
          <Plus />
          Новый токен
        </Button>
      </div>

      {tokens.isPending ? (
        <div className="space-y-2 p-5">
          {Array.from({ length: 3 }, (_, i) => (
            <Skeleton key={i} className="h-10" />
          ))}
        </div>
      ) : tokens.isError ? (
        <ErrorState error={tokens.error} onRetry={() => void tokens.refetch()} />
      ) : items.length === 0 ? (
        <EmptyState
          icon={<KeyRound />}
          title="Токенов пока нет"
          description="Выпустите токен, чтобы CRM или биллинг забирали риски клиентов сами, без выгрузок вручную."
        />
      ) : (
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>Токен</TableHead>
              <TableHead>Роль</TableHead>
              <TableHead>Откуда можно</TableHead>
              <TableHead>Срок</TableHead>
              <TableHead>Последний раз</TableHead>
              <TableHead>Выпустил</TableHead>
              <TableHead className="w-24">
                <span className="sr-only">Действия</span>
              </TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {items.map((t) => (
              <TableRow key={t.id} data-testid={`token-row-${t.name}`} className={cn(t.status !== 'active' && 'text-muted-foreground')}>
                <TableCell>
                  <div className="min-w-0">
                    <p className="flex items-center gap-2 font-medium">
                      <span className="truncate">{t.name}</span>
                      <TokenStatusBadge token={t} now={now} />
                    </p>
                    <p className="mt-0.5 font-mono text-xs text-muted-foreground">{t.prefix}</p>
                  </div>
                </TableCell>
                <TableCell>
                  <span className="inline-flex items-center gap-1.5 text-[13px] whitespace-nowrap">
                    {t.role === 'admin' && <ShieldCheck className="size-3.5 text-primary" />}
                    {TOKEN_ROLE_LABEL[t.role]}
                  </span>
                </TableCell>
                <TableCell className="max-w-56 text-[13px]">
                  <p className="truncate font-mono text-xs" title={t.allowed_ips.join('\n') || undefined}>
                    {t.allowed_ips.length ? formatAllowedIps(t.allowed_ips) : <span className="font-sans text-[13px] text-muted-foreground">с любого адреса</span>}
                  </p>
                  {t.rate_limit_per_min !== null && (
                    <p className="text-xs text-muted-foreground num">до {t.rate_limit_per_min} запросов в минуту</p>
                  )}
                </TableCell>
                <TableCell className="text-[13px] whitespace-nowrap num">{formatExpiry(t, new Date(now))}</TableCell>
                <TableCell className="text-[13px] whitespace-nowrap num" title={t.last_used_at ? formatDateTime(t.last_used_at) : undefined}>
                  {formatLastUsed(t, now)}
                </TableCell>
                <TableCell className="text-[13px] whitespace-nowrap">
                  <p>{t.created_by}</p>
                  <p className="text-xs text-muted-foreground num">{formatDateTime(t.created_at)}</p>
                </TableCell>
                <TableCell className="text-right">
                  {t.status === 'active' && (
                    <ConfirmDialog
                      trigger={
                        <Button variant="ghost" size="sm" className="text-risk-high hover:text-risk-high" disabled={revoke.isPending}>
                          Отозвать
                        </Button>
                      }
                      title={`Отозвать токен ${t.name}?`}
                      description="Он перестанет работать сразу, у всех систем, которые им пользуются. Вернуть его нельзя, только выпустить новый."
                      confirmLabel="Отозвать токен"
                      destructive
                      onConfirm={() => revoke.mutate(t)}
                    />
                  )}
                </TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      )}

      <CreateTokenDialog open={creating} onOpenChange={setCreating} onCreated={setCreated} />
      <SecretDialog created={created} onClose={() => setCreated(null)} />
    </Card>
  )
}

// ——— как подключить

function ConnectCard() {
  const origin = typeof window === 'undefined' ? '' : window.location.origin
  const example = curlExample(origin)
  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          <TerminalSquare className="size-4 text-muted-foreground" />
          Как подключить
        </CardTitle>
        <CardDescription>
          Передавайте токен в заголовке Authorization. Все методы и ответы описаны в документации API.
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-3">
        <div className="flex items-start gap-2">
          <pre
            data-testid="curl-example"
            className="min-w-0 flex-1 overflow-x-auto rounded-md border border-border bg-muted px-3 py-2.5 font-mono text-xs leading-5"
          >
            {example}
          </pre>
          <CopyButton value={example} label="Скопировать пример" />
        </div>
        <ul className="space-y-1 text-[13px] leading-5 text-muted-foreground">
          <li>Без токена или с отозванным API ответит 401, без нужных прав — 403.</li>
          <li>При превышении лимита придёт 429 с заголовком Retry-After.</li>
        </ul>
        <Button asChild variant="outline" size="sm">
          <a href={SWAGGER_PATH} target="_blank" rel="noopener noreferrer">
            <ExternalLink />
            Документация API
          </a>
        </Button>
      </CardContent>
    </Card>
  )
}

// ——— еженедельный отчёт

function ReportCard() {
  const queryClient = useQueryClient()
  const schedule = useQuery({ queryKey: queryKeys.reportSchedule, queryFn: ({ signal }) => api.reportSchedule(signal) })
  // отправка живёт выше формы: после неё расписание перечитывается, а сообщение «ушло» должно остаться
  const send = useMutation({
    mutationFn: () => api.sendWeeklyReport(),
    onSettled: () => {
      void queryClient.invalidateQueries({ queryKey: queryKeys.reportSchedule })
      void queryClient.invalidateQueries({ queryKey: queryKeys.audit })
    },
  })
  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          <Mail className="size-4 text-muted-foreground" />
          Еженедельный отчёт на почту
        </CardTitle>
        <CardDescription>
          PDF для руководства: ожидаемые потери, клиенты под риском, причины и что сделали за неделю.
        </CardDescription>
      </CardHeader>
      {schedule.isPending ? (
        <CardContent className="space-y-2">
          <Skeleton className="h-9" />
          <Skeleton className="h-20" />
        </CardContent>
      ) : schedule.isError ? (
        <ErrorState error={schedule.error} onRetry={() => void schedule.refetch()} />
      ) : (
        // форма берёт сохранённое расписание как начальные значения; после сохранения монтируется заново
        <ScheduleForm key={schedule.data.updated_at ?? 'new'} saved={schedule.data} send={send} />
      )}
    </Card>
  )
}

type SendMutation = UseMutationResult<WeeklyReportSent, Error, void>

function ScheduleForm({ saved, send }: { saved: ReportSchedule; send: SendMutation }) {
  const queryClient = useQueryClient()
  const ids = { enabled: useId(), time: useId(), recipients: useId(), form: useId() }
  const [enabled, setEnabled] = useState(saved.enabled)
  const [weekday, setWeekday] = useState(String(saved.weekday))
  const [time, setTime] = useState(saved.send_time)
  const [recipientsText, setRecipientsText] = useState(saved.recipients.join('\n'))
  const [submitted, setSubmitted] = useState(false)

  const recipients = parseEmails(recipientsText)
  const timeOk = SEND_TIME_RE.test(time)
  const localError = recipients.invalid.length
    ? `Не похоже на адрес: ${recipients.invalid.slice(0, 3).join(', ')}`
    : recipients.emails.length > 20
      ? 'Не больше 20 получателей.'
      : !timeOk
        ? 'Укажите время в формате ЧЧ:ММ.'
        : enabled && recipients.emails.length === 0
          ? 'Чтобы включить рассылку, добавьте хотя бы один адрес.'
          : null

  const dirty =
    enabled !== saved.enabled ||
    Number(weekday) !== saved.weekday ||
    time !== saved.send_time ||
    recipients.emails.join('\n') !== saved.recipients.join('\n')

  const save = useMutation({
    mutationFn: () =>
      api.putReportSchedule({
        enabled,
        weekday: Number(weekday),
        send_time: time,
        timezone: saved.timezone,
        recipients: recipients.emails,
      }),
    onSuccess: (data) => {
      queryClient.setQueryData(queryKeys.reportSchedule, data)
      void queryClient.invalidateQueries({ queryKey: queryKeys.audit })
    },
  })

  const submit = (event: FormEvent) => {
    event.preventDefault()
    setSubmitted(true)
    if (localError) return
    save.mutate()
  }

  return (
    <form id={ids.form} onSubmit={submit} noValidate>
      <CardContent className="space-y-4">
        <div className="flex items-center justify-between gap-4 rounded-lg border border-border px-3.5 py-3">
          <div>
            <Label htmlFor={ids.enabled}>Отправлять по расписанию</Label>
            <p className="mt-0.5 text-xs text-muted-foreground num">
              {describeSchedule({ enabled, weekday: Number(weekday), send_time: time, timezone: saved.timezone })}
            </p>
          </div>
          <Switch id={ids.enabled} checked={enabled} onCheckedChange={setEnabled} />
        </div>

        <div className="grid gap-4 sm:grid-cols-2">
          <div className="space-y-1.5">
            <Label>День недели</Label>
            <Select
              value={weekday}
              onValueChange={setWeekday}
              options={WEEKDAYS.map((d) => ({ value: String(d.value), label: d.label }))}
              ariaLabel="День недели"
              className="h-9 w-full"
            />
          </div>
          <Field id={ids.time} label="Время" hint={`По часовому поясу ${saved.timezone}`}>
            <Input id={ids.time} type="time" step={60} value={time} onChange={(e) => setTime(e.target.value.slice(0, 5))} required />
          </Field>
        </div>

        <Field id={ids.recipients} label="Получатели" hint="Адреса через запятую или по одному в строке, до 20.">
          <Textarea
            id={ids.recipients}
            value={recipientsText}
            onChange={(e) => setRecipientsText(e.target.value)}
            rows={3}
            placeholder={'ceo@example.com\ncfo@example.com'}
            spellCheck={false}
          />
        </Field>

        {submitted && localError && <FormError error={localError} />}
        <FormError error={save.error} />
        <FormError error={send.error} />
        {send.isSuccess && (
          <p role="status" className="rounded-md border border-risk-low/25 bg-risk-low-bg px-3 py-2 text-[13px] text-risk-low">
            Отчёт ушёл: {send.data.sent_to.join(', ')}
          </p>
        )}

        <dl className="grid gap-x-4 gap-y-1 text-[13px] sm:grid-cols-[auto_1fr]">
          <dt className="text-muted-foreground">Последняя отправка</dt>
          <dd className="num" data-testid="report-last-sent">
            {saved.last_sent_at ? formatDateTime(saved.last_sent_at) : 'ещё не отправляли'}
          </dd>
          {saved.last_error && (
            <>
              <dt className="text-muted-foreground">Ошибка</dt>
              <dd className="text-risk-high">{saved.last_error}</dd>
            </>
          )}
          {saved.updated_by && (
            <>
              <dt className="text-muted-foreground">Расписание менял</dt>
              <dd className="num">
                {saved.updated_by}, {formatDateTime(saved.updated_at)}
              </dd>
            </>
          )}
        </dl>
      </CardContent>
      <div className="flex flex-wrap items-center gap-2 border-t border-border px-5 py-3.5">
        <Button type="submit" disabled={save.isPending || !dirty}>
          {save.isPending && <Loader2 className="animate-spin" />}
          Сохранить
        </Button>
        <Tooltip
          content={
            saved.recipients.length
              ? `На адреса из сохранённого расписания: ${saved.recipients.join(', ')}`
              : 'Сначала сохраните хотя бы одного получателя'
          }
        >
          {/* обёртка нужна, чтобы подсказка показывалась и у выключенной кнопки */}
          <span tabIndex={saved.recipients.length ? -1 : 0} className="inline-flex rounded-md outline-none focus-visible:ring-2 focus-visible:ring-ring">
            <Button
              type="button"
              variant="outline"
              disabled={send.isPending || saved.recipients.length === 0}
              onClick={() => send.mutate()}
            >
              {send.isPending ? <Loader2 className="animate-spin" /> : <Send />}
              Отправить сейчас
            </Button>
          </span>
        </Tooltip>
        <Button asChild variant="ghost" className="ml-auto">
          <a href={WEEKLY_REPORT_PDF_URL} download>
            <Download />
            Скачать PDF
          </a>
        </Button>
      </div>
    </form>
  )
}

export default function IntegrationsPage() {
  return (
    <>
      <PageHeader
        title="Интеграции"
        description="Токены для внешних систем и отчёт для руководства на почту."
      />
      <div className="space-y-4">
        <TokensCard />
        <div className="grid gap-4 lg:grid-cols-2">
          <ConnectCard />
          <ReportCard />
        </div>
      </div>
    </>
  )
}

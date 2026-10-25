import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { AlertTriangle, KeyRound, Loader2, LockKeyhole, ShieldCheck, Siren, UserPlus } from 'lucide-react'
import { useId, useState, type FormEvent, type ReactNode } from 'react'
import { errorMessage } from '@/api/client'
import { api, queryKeys } from '@/api/endpoints'
import type { LoginSettings, LoginSettingsSaved, SsoDefaultRole } from '@/api/types'
import { PageHeader } from '@/components/page-header'
import { ErrorState } from '@/components/states'
import { ConfirmDialog } from '@/components/ui/alert-dialog'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Textarea } from '@/components/ui/dialog'
import { Label } from '@/components/ui/label'
import { Select } from '@/components/ui/select'
import { Skeleton } from '@/components/ui/skeleton'
import { Switch } from '@/components/ui/switch'
import { formatDateTime, plural } from '@/lib/format'
import {
  DEFAULT_ROLE_OPTIONS,
  draftError,
  draftFrom,
  draftWarnings,
  METHOD_LABEL,
  settingsPatch,
  type LoginDraft,
  type SsoMethod,
} from '@/lib/login-settings'
import { cn } from '@/lib/utils'

function Pill({ tone, children }: { tone: 'ok' | 'warn' | 'muted'; children: ReactNode }) {
  return (
    <span
      className={cn(
        'inline-flex h-6 items-center rounded-full px-2 text-xs font-medium whitespace-nowrap',
        tone === 'ok' && 'bg-risk-low-bg text-risk-low',
        tone === 'warn' && 'bg-risk-medium-bg text-risk-medium',
        tone === 'muted' && 'bg-muted text-muted-foreground',
      )}
    >
      {children}
    </span>
  )
}

function MethodRow({
  id,
  icon,
  title,
  description,
  status,
  checked,
  disabled,
  onChange,
  note,
}: {
  id: string
  icon: ReactNode
  title: string
  description: ReactNode
  status: ReactNode
  checked: boolean
  disabled?: boolean
  onChange: (value: boolean) => void
  note?: ReactNode
}) {
  return (
    <div className="flex items-start justify-between gap-4 rounded-lg border border-border px-3.5 py-3">
      <div className="flex min-w-0 gap-3">
        <span className="mt-0.5 text-muted-foreground [&_svg]:size-4">{icon}</span>
        <div className="min-w-0">
          <div className="flex flex-wrap items-center gap-2">
            <Label htmlFor={id}>{title}</Label>
            {status}
          </div>
          <p className="mt-0.5 text-xs leading-5 text-muted-foreground">{description}</p>
          {note && <p className="mt-1.5 text-xs leading-5 text-risk-medium">{note}</p>}
        </div>
      </div>
      <Switch id={id} checked={checked} disabled={disabled} onCheckedChange={onChange} aria-label={title} />
    </div>
  )
}

function ssoStatus(saved: LoginSettings, method: SsoMethod) {
  const state = saved.methods[method]
  if (!state.configured) return <Pill tone="muted">Не настроен на сервере</Pill>
  if (!state.available) return <Pill tone="warn">Провайдер не отвечает</Pill>
  return <Pill tone="ok">Настроен</Pill>
}

function SettingsForm({
  saved,
  result,
  setResult,
}: {
  saved: LoginSettings
  /** итог последнего сохранения живёт выше: после него форма монтируется заново */
  result: LoginSettingsSaved | null
  setResult: (value: LoginSettingsSaved | null) => void
}) {
  const queryClient = useQueryClient()
  const ids = {
    password: useId(),
    oidc: useId(),
    saml: useId(),
    provision: useId(),
    domains: useId(),
  }
  const [draft, setDraft] = useState<LoginDraft>(() => draftFrom(saved))
  const set = <K extends keyof LoginDraft>(key: K, value: LoginDraft[K]) => {
    setResult(null)
    setDraft((d) => ({ ...d, [key]: value }))
  }

  const patch = settingsPatch(saved, draft)
  const dirty = Object.keys(patch).length > 0
  const localError = dirty ? draftError(saved, draft) : null
  const warnings = dirty ? draftWarnings(saved, draft) : []
  const turningPasswordOff = saved.password_enabled && !draft.password

  const save = useMutation({
    mutationFn: () => api.updateLoginSettings(patch),
    onSuccess: (data) => {
      setResult(data)
      queryClient.setQueryData(queryKeys.loginSettings, data)
      void queryClient.invalidateQueries({ queryKey: queryKeys.providers })
      void queryClient.invalidateQueries({ queryKey: queryKeys.audit })
    },
  })

  const submit = (event: FormEvent) => {
    event.preventDefault()
    if (!dirty || localError) return
    save.mutate()
  }

  const passwordNote =
    saved.password_enabled && draft.password && saved.password_disable_blocker
      ? saved.password_disable_blocker
      : null

  return (
    <form onSubmit={submit} noValidate className="space-y-4">
      {saved.break_glass && (
        <p
          role="status"
          className="flex gap-2.5 rounded-lg border border-risk-medium/25 bg-risk-medium-bg px-3.5 py-3 text-[13px] leading-5 text-risk-medium"
        >
          <Siren className="mt-0.5 size-4 shrink-0" />
          <span>
            На сервере включён аварийный ключ <code className="font-mono">AUTH_BREAK_GLASS_PASSWORD_LOGIN</code>:
            администраторы входят по паролю, даже если он здесь выключен. Когда доступ восстановлен, уберите
            переменную и перезапустите auth.
          </span>
        </p>
      )}

      <Card>
        <CardHeader>
          <CardTitle>Способы входа</CardTitle>
          <CardDescription>
            Что видят сотрудники на странице входа. Выключенный способ закрыт и на сервере: старые сессии, выданные
            им, сбрасываются сразу.
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-3">
          <MethodRow
            id={ids.password}
            icon={<LockKeyhole />}
            title={METHOD_LABEL.password}
            description="Учётные записи, заведённые в панели на странице «Пользователи»."
            status={<Pill tone="ok">Встроенный</Pill>}
            checked={draft.password}
            onChange={(v) => set('password', v)}
            note={passwordNote}
          />
          <MethodRow
            id={ids.oidc}
            icon={<KeyRound />}
            title={METHOD_LABEL.oidc}
            description={
              saved.methods.oidc.configured
                ? `Кнопка «${saved.methods.oidc.display_name ?? 'Войти через SSO'}». Роль — по группам у провайдера.`
                : 'Чтобы включить, задайте OIDC_ENABLED и параметры провайдера в окружении auth.'
            }
            status={ssoStatus(saved, 'oidc')}
            checked={draft.oidc}
            disabled={!saved.methods.oidc.configured}
            onChange={(v) => set('oidc', v)}
          />
          <MethodRow
            id={ids.saml}
            icon={<ShieldCheck />}
            title={METHOD_LABEL.saml}
            description={
              saved.methods.saml.configured
                ? `Кнопка «${saved.methods.saml.display_name ?? 'Войти через SSO'}». Роль — по группам у провайдера.`
                : 'Чтобы включить, задайте SAML_ENABLED и метаданные IdP в окружении auth.'
            }
            status={ssoStatus(saved, 'saml')}
            checked={draft.saml}
            disabled={!saved.methods.saml.configured}
            onChange={(v) => set('saml', v)}
          />
          <p className="text-xs leading-5 text-muted-foreground">
            Вы вошли {saved.session_method === 'password' ? 'по паролю' : `через ${saved.session_method.toUpperCase()}`}.
            {' '}
            Администраторов, которые входят через включённый SSO: {saved.sso_admins}.
          </p>
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2">
            <UserPlus className="size-4 text-muted-foreground" />
            Регистрация через SSO
          </CardTitle>
          <CardDescription>
            Что делать, когда через SSO приходит человек, которого в панели ещё нет. Уже заведённых это не касается.
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          <div className="flex items-start justify-between gap-4 rounded-lg border border-border px-3.5 py-3">
            <div>
              <Label htmlFor={ids.provision}>Создавать пользователя при первом входе</Label>
              <p className="mt-0.5 text-xs leading-5 text-muted-foreground">
                {draft.autoProvision
                  ? 'Новый человек из SSO получает учётную запись сам, роль — по группам у провайдера.'
                  : 'Пускаем только тех, кого администратор завёл заранее («только SSO» на странице «Пользователи»).'}
              </p>
            </div>
            <Switch
              id={ids.provision}
              checked={draft.autoProvision}
              onCheckedChange={(v) => set('autoProvision', v)}
              aria-label="Создавать пользователя при первом входе"
            />
          </div>

          <div className="grid gap-4 sm:grid-cols-2">
            <div className="space-y-1.5">
              <Label>Роль, если группы провайдера роли не дали</Label>
              <Select
                value={draft.defaultRole}
                onValueChange={(v) => set('defaultRole', v as SsoDefaultRole)}
                options={DEFAULT_ROLE_OPTIONS}
                ariaLabel="Роль по умолчанию для SSO"
                className="h-9 w-full"
              />
              <p className="text-xs leading-5 text-muted-foreground">
                Администратора автоматически не выдаём: только через группу у провайдера или вручную.
              </p>
            </div>
            <div className="space-y-1.5">
              <Label htmlFor={ids.domains}>Домены почты для автосоздания</Label>
              <Textarea
                id={ids.domains}
                value={draft.domainsText}
                onChange={(e) => set('domainsText', e.target.value)}
                rows={3}
                placeholder={'example.com\ncorp.example.com'}
                spellCheck={false}
                disabled={!draft.autoProvision}
              />
              <p className="text-xs leading-5 text-muted-foreground">
                Пусто — любой домен. Совпадение точное: поддомены перечислите отдельно. Почту берём из профиля у
                провайдера.
              </p>
            </div>
          </div>
        </CardContent>
      </Card>

      {warnings.length > 0 && !localError && (
        <div
          role="status"
          className="flex gap-2.5 rounded-lg border border-risk-medium/25 bg-risk-medium-bg px-3.5 py-3 text-[13px] leading-5 text-risk-medium"
        >
          <AlertTriangle className="mt-0.5 size-4 shrink-0" />
          <ul className="space-y-1">
            {warnings.map((w) => (
              <li key={w}>{w}</li>
            ))}
          </ul>
        </div>
      )}
      {localError && (
        <p role="alert" className="rounded-md border border-risk-high/20 bg-risk-high-bg px-3 py-2 text-[13px] text-risk-high">
          {localError}
        </p>
      )}
      {save.isError && (
        <p role="alert" className="rounded-md border border-risk-high/20 bg-risk-high-bg px-3 py-2 text-[13px] text-risk-high">
          {errorMessage(save.error)}
        </p>
      )}
      {result && (
        <p role="status" className="rounded-md border border-risk-low/25 bg-risk-low-bg px-3 py-2 text-[13px] text-risk-low">
          Сохранено.
          {result.revoked_sessions > 0 &&
            ` Сброшено ${result.revoked_sessions} ${plural(result.revoked_sessions, ['сессия', 'сессии', 'сессий'])}.`}
        </p>
      )}

      <div className="flex flex-wrap items-center gap-2">
        {turningPasswordOff && !localError ? (
          <ConfirmDialog
            trigger={
              <Button type="button" disabled={save.isPending || !dirty}>
                {save.isPending && <Loader2 className="animate-spin" />}
                Сохранить
              </Button>
            }
            title="Выключить вход по паролю?"
            description="Все, кто сейчас в панели по паролю, будут разлогинены, а форма логина и пароля пропадёт со страницы входа. Вернуть пароль можно здесь же или аварийным ключом на сервере."
            confirmLabel="Выключить пароль"
            destructive
            onConfirm={() => save.mutate()}
          />
        ) : (
          <Button type="submit" disabled={save.isPending || !dirty || Boolean(localError)}>
            {save.isPending && <Loader2 className="animate-spin" />}
            Сохранить
          </Button>
        )}
        <Button
          type="button"
          variant="ghost"
          disabled={!dirty || save.isPending}
          onClick={() => {
            setDraft(draftFrom(saved))
            save.reset()
          }}
        >
          Отменить изменения
        </Button>
        {saved.updated_by && (
          <span className="ml-auto text-xs text-muted-foreground num">
            Последнее изменение: {saved.updated_by}, {formatDateTime(saved.updated_at)} · история — в журнале действий
          </span>
        )}
      </div>
    </form>
  )
}

export default function LoginSettingsPage() {
  const [result, setResult] = useState<LoginSettingsSaved | null>(null)
  const settings = useQuery({
    queryKey: queryKeys.loginSettings,
    queryFn: ({ signal }) => api.loginSettings(signal),
  })
  return (
    <>
      <PageHeader
        title="Вход и регистрация"
        description="Какими способами сотрудники входят в панель и кого пускать через SSO."
      />
      {settings.isPending ? (
        <div className="space-y-4">
          <Skeleton className="h-64 rounded-xl" />
          <Skeleton className="h-48 rounded-xl" />
        </div>
      ) : settings.isError ? (
        <ErrorState error={settings.error} onRetry={() => void settings.refetch()} />
      ) : (
        // после сохранения форма монтируется заново от свежих данных
        <SettingsForm
          key={settings.data.updated_at ?? 'new'}
          saved={settings.data}
          result={result}
          setResult={setResult}
        />
      )}
    </>
  )
}

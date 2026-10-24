import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { AlertCircle, ArrowRight, Eye, EyeOff, KeyRound, Loader2, Moon, ShieldCheck, Sun } from 'lucide-react'
import { useEffect, useId, useState, type FormEvent } from 'react'
import { useNavigate, useSearchParams } from 'react-router'
import { z } from 'zod'
import { isApiError } from '@/api/client'
import { api, queryKeys } from '@/api/endpoints'
import { Logo } from '@/components/app-shell'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { plural } from '@/lib/format'
import { safeNext } from '@/lib/url-state'
import { useTheme } from '@/providers/theme'

const loginSchema = z.object({
  username: z.string().trim().min(1, 'Введите логин').max(128, 'Слишком длинный логин'),
  password: z.string().min(1, 'Введите пароль').max(256, 'Слишком длинный пароль'),
})

type FieldErrors = Partial<Record<'username' | 'password', string>>

function loginErrorText(error: unknown, retryLeft: number): string {
  if (!isApiError(error)) return 'Не удалось войти. Попробуйте ещё раз.'
  if (error.status === 401) return 'Неверный логин или пароль. Проверьте раскладку и Caps Lock.'
  if (error.status === 429) {
    return retryLeft > 0
      ? `Слишком много попыток входа. Попробуйте снова через ${retryLeft} ${plural(retryLeft, ['секунду', 'секунды', 'секунд'])}.`
      : 'Слишком много попыток входа. Можно попробовать снова.'
  }
  if (error.status === 0) return error.message
  if (error.status >= 500) return 'Сервис входа временно недоступен. Попробуйте через минуту.'
  return error.message
}

function withNext(url: string, next: string): string {
  const sep = url.includes('?') ? '&' : '?'
  return `${url}${sep}next=${encodeURIComponent(next)}`
}

const SSO_ERRORS: Record<string, string> = {
  oidc_failed: 'Не удалось войти через SSO. Попробуйте ещё раз или войдите по логину и паролю.',
  oidc_denied: 'Вход через SSO отменён.',
  blocked: 'Учётная запись заблокирована в панели. Обратитесь к администратору.',
  forbidden:'У этой учётной записи нет доступа к панели: её нет ни в одной группе ChurnRadar у провайдера входа.',
}

export function LoginPage() {
  const [params] = useSearchParams()
  const next = safeNext(params.get('next'))
  const navigate = useNavigate()
  const queryClient = useQueryClient()
  const { resolved, toggle } = useTheme()
  const usernameId = useId()
  const passwordId = useId()
  const errorId = useId()

  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [showPassword, setShowPassword] = useState(false)
  const [fieldErrors, setFieldErrors] = useState<FieldErrors>({})
  const [retryUntil, setRetryUntil] = useState<number | null>(null)
  const [now, setNow] = useState(() => Date.now())

  const providers = useQuery({ queryKey: queryKeys.providers, queryFn: api.providers, staleTime: 60_000, retry: 1 })

  // уже вошли — сразу дальше
  const session = useQuery({
    queryKey: ['me-probe'],
    queryFn: () => api.me({ allowUnauthorized: true }),
    retry: false,
    staleTime: 0,
  })
  useEffect(() => {
    if (session.data) {
      queryClient.setQueryData(queryKeys.me, session.data)
      navigate(next, { replace: true })
    }
  }, [session.data, navigate, next, queryClient])

  const login = useMutation({
    mutationFn: api.login,
    onSuccess: (user) => {
      queryClient.setQueryData(queryKeys.me, user)
      navigate(next, { replace: true })
    },
    onError: (error) => {
      if (isApiError(error) && error.status === 429) {
        setRetryUntil(Date.now() + (error.retryAfter ?? 30) * 1000)
        setNow(Date.now())
      }
    },
  })

  useEffect(() => {
    if (!retryUntil) return
    const id = window.setInterval(() => {
      const t = Date.now()
      setNow(t)
      if (t >= retryUntil) {
        setRetryUntil(null)
        window.clearInterval(id)
      }
    }, 1000)
    return () => window.clearInterval(id)
  }, [retryUntil])

  const retryLeft = retryUntil ? Math.max(0, Math.ceil((retryUntil - now) / 1000)) : 0
  const locked = retryLeft > 0

  const onSubmit = (event: FormEvent) => {
    event.preventDefault()
    const parsed = loginSchema.safeParse({ username, password })
    if (!parsed.success) {
      const errs: FieldErrors = {}
      for (const issue of parsed.error.issues) {
        const key = issue.path[0]
        if ((key === 'username' || key === 'password') && !errs[key]) errs[key] = issue.message
      }
      setFieldErrors(errs)
      return
    }
    setFieldErrors({})
    login.mutate(parsed.data)
  }

  // кнопка SSO есть, только если провайдер настроен и прямо сейчас отвечает
  const oidc = providers.data?.oidc
  const saml = providers.data?.saml
  const ssoButtons = [
    oidc?.enabled && {
      key: 'oidc',
      url: oidc.login_url || '/api/v1/auth/oidc/login',
      label: oidc.display_name?.trim() || 'Войти через SSO (OIDC)',
    },
    saml?.enabled && {
      key: 'saml',
      url: saml.login_url || '/api/v1/auth/saml/login',
      label: saml.display_name?.trim() || 'Войти через SSO (SAML)',
    },
  ].filter((b): b is { key: string; url: string; label: string } => Boolean(b))
  const ssoError = params.get('error')
  const ssoErrorText = ssoError ? (SSO_ERRORS[ssoError] ?? SSO_ERRORS.oidc_failed) : null
  const formError = login.isError ? loginErrorText(login.error, retryLeft) : ssoErrorText

  return (
    <div className="relative flex min-h-screen flex-col bg-background">
      <div className="absolute top-4 right-4">
        <Button
          variant="ghost"
          size="icon-sm"
          onClick={toggle}
          aria-label={resolved === 'dark' ? 'Включить светлую тему' : 'Включить тёмную тему'}
          className="text-muted-foreground"
        >
          {resolved === 'dark' ? <Sun /> : <Moon />}
        </Button>
      </div>

      <div className="flex flex-1 items-center justify-center px-4 py-12">
        <div className="w-full max-w-[380px]">
          <Logo className="mb-8" />
          <h1 className="text-2xl font-semibold tracking-[-0.02em]">Вход в панель</h1>
          <p className="mt-2 text-sm leading-6 text-muted-foreground">
            Кто из клиентов может уйти, почему и сколько это стоит бизнесу.
          </p>

          {formError && (
            <div
              id={errorId}
              role="alert"
              className="mt-6 flex gap-2.5 rounded-lg border border-risk-high/20 bg-risk-high-bg px-3.5 py-3 text-[13px] leading-5 text-risk-high animate-fade-in"
            >
              <AlertCircle className="mt-0.5 size-4 shrink-0" />
              <span>{formError}</span>
            </div>
          )}

          <form className="mt-6 space-y-4" onSubmit={onSubmit} noValidate aria-describedby={formError ? errorId : undefined}>
            <div className="space-y-1.5">
              <Label htmlFor={usernameId}>Логин</Label>
              <Input
                id={usernameId}
                name="username"
                autoComplete="username"
                autoCapitalize="none"
                spellCheck={false}
                autoFocus
                value={username}
                onChange={(e) => setUsername(e.target.value)}
                aria-invalid={fieldErrors.username ? true : undefined}
                className="h-10"
              />
              {fieldErrors.username && <p className="text-xs text-destructive">{fieldErrors.username}</p>}
            </div>
            <div className="space-y-1.5">
              <Label htmlFor={passwordId}>Пароль</Label>
              <div className="relative">
                <Input
                  id={passwordId}
                  name="password"
                  type={showPassword ? 'text' : 'password'}
                  autoComplete="current-password"
                  value={password}
                  onChange={(e) => setPassword(e.target.value)}
                  aria-invalid={fieldErrors.password ? true : undefined}
                  className="h-10 pr-10"
                />
                <button
                  type="button"
                  onClick={() => setShowPassword((v) => !v)}
                  className="absolute inset-y-0 right-0 flex w-10 items-center justify-center rounded-r-md text-muted-foreground outline-none hover:text-foreground focus-visible:ring-2 focus-visible:ring-ring"
                  aria-label={showPassword ? 'Скрыть пароль' : 'Показать пароль'}
                  aria-pressed={showPassword}
                >
                  {showPassword ? <EyeOff className="size-4" /> : <Eye className="size-4" />}
                </button>
              </div>
              {fieldErrors.password && <p className="text-xs text-destructive">{fieldErrors.password}</p>}
            </div>
            <Button type="submit" size="lg" className="w-full" disabled={login.isPending || locked}>
              {login.isPending ? <Loader2 className="animate-spin" /> : null}
              {login.isPending ? 'Входим…' : locked ? `Подождите ${retryLeft} с` : 'Войти'}
              {!login.isPending && !locked && <ArrowRight />}
            </Button>
          </form>

          {ssoButtons.length > 0 && (
            <>
              <div className="my-6 flex items-center gap-3 text-xs text-subtle-foreground">
                <div className="h-px flex-1 bg-border" />
                или
                <div className="h-px flex-1 bg-border" />
              </div>
              <div className="space-y-2.5">
                {ssoButtons.map((sso) => (
                  <Button key={sso.key} asChild variant="outline" size="lg" className="w-full">
                    <a href={withNext(sso.url, next)}>
                      {sso.key === 'saml' ? <ShieldCheck /> : <KeyRound />}
                      {sso.label}
                    </a>
                  </Button>
                ))}
              </div>
            </>
          )}
        </div>
      </div>

      <p className="pb-6 text-center text-xs text-subtle-foreground">ChurnRadar · аналитика оттока для маркетинга</p>
    </div>
  )
}

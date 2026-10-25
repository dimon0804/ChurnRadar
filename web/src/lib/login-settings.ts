import type { AuthProviders, LoginSettings, LoginSettingsUpdate, SsoDefaultRole } from '@/api/types'

export type SsoMethod = 'oidc' | 'saml'
export const SSO_METHODS: readonly SsoMethod[] = ['oidc', 'saml']

export const METHOD_LABEL: Record<'password' | SsoMethod, string> = {
  password: 'Логин и пароль',
  oidc: 'SSO через OIDC',
  saml: 'SSO через SAML',
}

export const DEFAULT_ROLE_OPTIONS: { value: SsoDefaultRole; label: string }[] = [
  { value: 'none', label: 'Не пускать без группы' },
  { value: 'analyst', label: 'Аналитик' },
  { value: 'marketer', label: 'Маркетолог' },
]

/** Отказы входа через SSO: сервер уводит на /login?error=<код>. */
export const SSO_ERRORS: Record<string, string> = {
  sso: 'Не удалось войти через SSO. Попробуйте ещё раз.',
  oidc_denied: 'Вход через SSO отменён.',
  blocked: 'Учётная запись заблокирована в панели. Обратитесь к администратору.',
  forbidden:
    'У этой учётной записи нет доступа к панели: её нет ни в одной группе ChurnRadar у провайдера входа.',
  not_provisioned:
    'Вас ещё нет в панели, а автоматическое создание пользователей через SSO выключено. Попросите администратора завести вам учётную запись.',
  domain_not_allowed:
    'Почта вашей учётной записи не из разрешённых доменов, поэтому создать вход автоматически нельзя. Обратитесь к администратору.',
  method_disabled: 'Этот способ входа отключён администратором. Выберите другой.',
}

export function ssoErrorText(code: string | null): string | null {
  if (!code) return null
  return SSO_ERRORS[code] ?? 'Не удалось войти через SSO. Попробуйте ещё раз.'
}

export interface SsoButton {
  key: SsoMethod
  url: string
  label: string
}

export interface LoginView {
  /** показывать форму логина и пароля */
  showPasswordForm: boolean
  /** пароль выключен, но сервер пускает администраторов по аварийному ключу */
  adminsOnly: boolean
  ssoButtons: SsoButton[]
  /** только SSO: без полей, крупные кнопки */
  ssoOnly: boolean
  /** ни одного способа прямо сейчас (пароль выключен, IdP не отвечает) */
  nothingAvailable: boolean
}

/** Что показать на /login. Пока ответа нет — форма пароля, как раньше: так страница не прыгает. */
export function loginView(providers: AuthProviders | undefined): LoginView {
  const password = providers?.password
  const showPasswordForm = providers ? Boolean(password?.enabled) : true
  const ssoButtons: SsoButton[] = []
  if (providers?.oidc.enabled) {
    ssoButtons.push({
      key: 'oidc',
      url: providers.oidc.login_url || '/api/v1/auth/oidc/login',
      label: providers.oidc.display_name?.trim() || 'Войти через SSO (OIDC)',
    })
  }
  if (providers?.saml.enabled) {
    ssoButtons.push({
      key: 'saml',
      url: providers.saml.login_url || '/api/v1/auth/saml/login',
      label: providers.saml.display_name?.trim() || 'Войти через SSO (SAML)',
    })
  }
  return {
    showPasswordForm,
    adminsOnly: Boolean(password?.admins_only),
    ssoButtons,
    ssoOnly: !showPasswordForm && ssoButtons.length > 0,
    nothingAvailable: !showPasswordForm && ssoButtons.length === 0,
  }
}

// ---- страница «Вход и регистрация» ------------------------------------------------------

const DOMAIN_RE = /^(?=.{1,253}$)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z0-9-]{2,63}$/

/** Домены через запятую, пробел или по строке; «@Example.com» → «example.com». */
export function parseDomains(text: string): { domains: string[]; invalid: string[] } {
  const domains: string[] = []
  const invalid: string[] = []
  for (const raw of text.split(/[\s,;]+/)) {
    const value = raw.trim()
    if (!value) continue
    const domain = value.toLowerCase().replace(/^@/, '').replace(/\.$/, '')
    if (!DOMAIN_RE.test(domain)) {
      if (!invalid.includes(value)) invalid.push(value)
      continue
    }
    if (!domains.includes(domain)) domains.push(domain)
  }
  return { domains, invalid }
}

export interface LoginDraft {
  password: boolean
  oidc: boolean
  saml: boolean
  autoProvision: boolean
  defaultRole: SsoDefaultRole
  domainsText: string
}

/** Включён ли провайдер на деле: флаг администратора действует, только если провайдер настроен. */
export function methodOn(saved: LoginSettings, method: SsoMethod): boolean {
  return saved.methods[method].configured && saved[`${method}_enabled`]
}

export function draftFrom(saved: LoginSettings): LoginDraft {
  return {
    password: saved.password_enabled,
    oidc: methodOn(saved, 'oidc'),
    saml: methodOn(saved, 'saml'),
    autoProvision: saved.sso_auto_provision,
    defaultRole: saved.sso_default_role,
    domainsText: saved.allowed_email_domains.join('\n'),
  }
}

/** Только то, что поменяли. Ненастроенный провайдер не отправляем никогда — сервер его не переключит. */
export function settingsPatch(saved: LoginSettings, draft: LoginDraft): LoginSettingsUpdate {
  const patch: LoginSettingsUpdate = {}
  if (draft.password !== saved.password_enabled) patch.password_enabled = draft.password
  for (const m of SSO_METHODS) {
    if (saved.methods[m].configured && draft[m] !== methodOn(saved, m)) patch[`${m}_enabled`] = draft[m]
  }
  if (draft.autoProvision !== saved.sso_auto_provision) patch.sso_auto_provision = draft.autoProvision
  if (draft.defaultRole !== saved.sso_default_role) patch.sso_default_role = draft.defaultRole
  const { domains } = parseDomains(draft.domainsText)
  if (domains.join('\n') !== saved.allowed_email_domains.join('\n')) patch.allowed_email_domains = domains
  return patch
}

/** Ошибка, из-за которой сохранять бессмысленно: сервер всё равно откажет. */
export function draftError(saved: LoginSettings, draft: LoginDraft): string | null {
  const { domains, invalid } = parseDomains(draft.domainsText)
  if (invalid.length) return `Не похоже на домен: ${invalid.slice(0, 3).join(', ')}`
  if (domains.length > 50) return 'Доменов не больше 50.'
  const anyOn = draft.password || SSO_METHODS.some((m) => saved.methods[m].configured && draft[m])
  if (!anyOn) return 'Нельзя выключить все способы входа: должен остаться хотя бы один.'
  if (!draft.password) {
    const ssoOn = SSO_METHODS.filter((m) => saved.methods[m].configured && draft[m])
    const current = saved.session_method
    const methodsChanged =
      draft.password !== saved.password_enabled || SSO_METHODS.some((m) => draft[m] !== methodOn(saved, m))
    if (methodsChanged) {
      if (current === 'password') {
        return (
          saved.password_disable_blocker ??
          'Чтобы выключить вход по паролю, войдите в панель через SSO учётной записью администратора.'
        )
      }
      if (!ssoOn.includes(current)) {
        return `Вы вошли через ${current.toUpperCase()}, а этот способ выключается вместе с паролем: так вы потеряете доступ.`
      }
      if (saved.password_disable_blocker && saved.password_enabled) return saved.password_disable_blocker
    }
  }
  return null
}

/** Предупреждения перед сохранением: что случится с людьми, которые сейчас в панели. */
export function draftWarnings(saved: LoginSettings, draft: LoginDraft): string[] {
  const warnings: string[] = []
  if (saved.password_enabled && !draft.password) {
    warnings.push(
      saved.break_glass
        ? 'Все, кто вошёл по паролю, кроме администраторов, будут сразу разлогинены. Дальше — только через SSO (на сервере включён аварийный вход для администраторов).'
        : 'Все, кто вошёл по паролю, будут сразу разлогинены. Дальше в панель — только через SSO.',
    )
  }
  for (const m of SSO_METHODS) {
    if (methodOn(saved, m) && !draft[m]) {
      const self = saved.session_method === m ? ' Включая вас: после сохранения войдите другим способом.' : ''
      warnings.push(`Сессии, открытые через ${m.toUpperCase()}, будут сброшены.${self}`)
    }
  }
  if (saved.sso_auto_provision && !draft.autoProvision) {
    warnings.push('Новых людей из SSO панель больше не создаст сама: заводите их заранее на странице «Пользователи».')
  }
  return warnings
}

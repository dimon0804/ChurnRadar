import { describe, expect, it } from 'vitest'
import type { AuthProviders, LoginSettings } from '@/api/types'
import {
  draftError,
  draftFrom,
  draftWarnings,
  loginView,
  parseDomains,
  settingsPatch,
  ssoErrorText,
} from './login-settings'

const OIDC = { enabled: true, display_name: 'Войти через Authentik (OIDC)', login_url: '/api/v1/auth/oidc/login' }
const SAML = { enabled: true, display_name: 'Войти через Authentik (SAML)', login_url: '/api/v1/auth/saml/login' }

function providers(over: Partial<AuthProviders> = {}): AuthProviders {
  return { password: { enabled: true }, oidc: { enabled: false }, saml: { enabled: false }, ...over }
}

function saved(over: Partial<LoginSettings> = {}): LoginSettings {
  return {
    password_enabled: true,
    oidc_enabled: true,
    saml_enabled: true,
    sso_auto_provision: true,
    sso_default_role: 'none',
    allowed_email_domains: [],
    updated_at: null,
    updated_by: null,
    methods: {
      password: { configured: true, available: true, active: true },
      oidc: { configured: true, available: true, active: true, display_name: 'OIDC' },
      saml: { configured: false, available: false, active: false, display_name: 'SAML' },
    },
    break_glass: false,
    session_method: 'oidc',
    password_disable_blocker: null,
    sso_admins: 1,
    ...over,
  }
}

describe('loginView — что показать на /login', () => {
  it('пароль и SSO: форма и кнопки под ней', () => {
    const view = loginView(providers({ oidc: OIDC, saml: SAML }))
    expect(view.showPasswordForm).toBe(true)
    expect(view.ssoOnly).toBe(false)
    expect(view.ssoButtons.map((b) => b.label)).toEqual(['Войти через Authentik (OIDC)', 'Войти через Authentik (SAML)'])
  })

  it('только SSO: без полей логина и пароля', () => {
    const view = loginView(providers({ password: { enabled: false }, oidc: OIDC }))
    expect(view.showPasswordForm).toBe(false)
    expect(view.ssoOnly).toBe(true)
    expect(view.ssoButtons).toEqual([{ key: 'oidc', url: '/api/v1/auth/oidc/login', label: 'Войти через Authentik (OIDC)' }])
  })

  it('пароль выключен, а IdP не отвечает: честно говорим, что войти нельзя', () => {
    const view = loginView(providers({ password: { enabled: false } }))
    expect(view.nothingAvailable).toBe(true)
    expect(view.ssoOnly).toBe(false)
  })

  it('аварийный ключ: форма есть, но с пометкой «только админам»', () => {
    const view = loginView(providers({ password: { enabled: true, admins_only: true }, oidc: OIDC }))
    expect(view.showPasswordForm).toBe(true)
    expect(view.adminsOnly).toBe(true)
  })

  it('пока ответа нет — форма пароля, как раньше; запасные подписи кнопок', () => {
    expect(loginView(undefined).showPasswordForm).toBe(true)
    const view = loginView(providers({ oidc: { enabled: true }, saml: { enabled: true, display_name: '  ' } }))
    expect(view.ssoButtons.map((b) => [b.url, b.label])).toEqual([
      ['/api/v1/auth/oidc/login', 'Войти через SSO (OIDC)'],
      ['/api/v1/auth/saml/login', 'Войти через SSO (SAML)'],
    ])
  })
})

describe('ssoErrorText', () => {
  it('объясняет новые отказы понятным языком', () => {
    expect(ssoErrorText('not_provisioned')).toContain('автоматическое создание')
    expect(ssoErrorText('domain_not_allowed')).toContain('разрешённых доменов')
    expect(ssoErrorText('method_disabled')).toContain('отключён администратором')
    expect(ssoErrorText('forbidden')).toContain('нет доступа')
    expect(ssoErrorText('something-new')).toContain('Не удалось войти через SSO')
    expect(ssoErrorText(null)).toBeNull()
  })
})

describe('parseDomains', () => {
  it('нормализует и отбрасывает мусор', () => {
    expect(parseDomains('@Example.COM, corp.example.org.\nexample.com')).toEqual({
      domains: ['example.com', 'corp.example.org'],
      invalid: [],
    })
    expect(parseDomains('not_a_domain example').invalid).toEqual(['not_a_domain', 'example'])
    expect(parseDomains('  ')).toEqual({ domains: [], invalid: [] })
  })
})

describe('страница «Вход и регистрация»', () => {
  it('черновик берёт действующие значения: ненастроенный SAML выключен', () => {
    const draft = draftFrom(saved())
    expect(draft).toMatchObject({ password: true, oidc: true, saml: false, autoProvision: true, defaultRole: 'none' })
  })

  it('отправляет только изменённое и никогда — ненастроенный провайдер', () => {
    const s = saved({ allowed_email_domains: ['example.com'] })
    const draft = { ...draftFrom(s), saml: true, autoProvision: false, domainsText: 'example.com' }
    expect(settingsPatch(s, draft)).toEqual({ sso_auto_provision: false })
    expect(settingsPatch(s, { ...draftFrom(s), password: false, domainsText: '@Corp.ru' })).toEqual({
      password_enabled: false,
      allowed_email_domains: ['corp.ru'],
    })
  })

  it('нельзя выключить всё', () => {
    const s = saved()
    expect(draftError(s, { ...draftFrom(s), password: false, oidc: false })).toContain('хотя бы один')
  })

  it('вошёл по паролю — выключить пароль нельзя, текст от сервера', () => {
    const s = saved({ session_method: 'password', password_disable_blocker: 'Войдите через SSO' })
    expect(draftError(s, { ...draftFrom(s), password: false })).toBe('Войдите через SSO')
  })

  it('вошёл через OIDC — можно выключить пароль, но не вместе со своим OIDC', () => {
    const s = saved({ saml_enabled: true, methods: { ...saved().methods, saml: { configured: true, available: true, active: true } } })
    expect(draftError(s, { ...draftFrom(s), password: false })).toBeNull()
    expect(draftError(s, { ...draftFrom(s), password: false, oidc: false })).toContain('потеряете доступ')
  })

  it('неверный домен — ошибка до отправки', () => {
    const s = saved()
    expect(draftError(s, { ...draftFrom(s), domainsText: 'плохой домен' })).toContain('Не похоже на домен')
  })

  it('предупреждает, кого разлогинит', () => {
    const s = saved()
    expect(draftWarnings(s, { ...draftFrom(s), password: false })[0]).toContain('будут сразу разлогинены')
    const glass = saved({ break_glass: true })
    expect(draftWarnings(glass, { ...draftFrom(glass), password: false })[0]).toContain('кроме администраторов')
    const self = draftWarnings(s, { ...draftFrom(s), oidc: false })
    expect(self[0]).toContain('Включая вас')
    expect(draftWarnings(s, { ...draftFrom(s), autoProvision: false })[0]).toContain('заводите их заранее')
    expect(draftWarnings(s, draftFrom(s))).toEqual([])
  })
})

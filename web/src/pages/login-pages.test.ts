// @vitest-environment jsdom
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { act, createElement, type ReactElement } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { MemoryRouter } from 'react-router'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import type { AuthProviders, LoginSettings } from '@/api/types'
import { ThemeProvider } from '@/providers/theme'
import { LoginPage } from './login'
import LoginSettingsPage from './login-settings'

// страница входа и «Вход и регистрация» целиком, в jsdom: что видит человек при разных настройках

declare global {
  var IS_REACT_ACT_ENVIRONMENT: boolean
}

let root: Root | null = null
let host: HTMLDivElement
let responses: Record<string, { status: number; body: unknown }>

function json(status: number, body: unknown) {
  return { status, body }
}

beforeEach(() => {
  globalThis.IS_REACT_ACT_ENVIRONMENT = true
  // jsdom не умеет matchMedia и ResizeObserver: их спрашивают фон страницы входа и переключатели Radix
  vi.stubGlobal(
    'ResizeObserver',
    class {
      observe() {}
      unobserve() {}
      disconnect() {}
    },
  )
  window.matchMedia = vi.fn().mockReturnValue({ matches: true, addEventListener() {}, removeEventListener() {} })
  responses = { '/api/v1/auth/me': json(401, { error: { code: 'unauthorized', message: 'Нужно войти' } }) }
  vi.stubGlobal(
    'fetch',
    vi.fn(async (url: string) => {
      const found = responses[url.split('?')[0] ?? url] ?? json(404, {})
      return new Response(JSON.stringify(found.body), { status: found.status, headers: { 'Content-Type': 'application/json' } })
    }),
  )
  host = document.createElement('div')
  document.body.appendChild(host)
})

afterEach(() => {
  act(() => root?.unmount())
  root = null
  host.remove()
  vi.unstubAllGlobals()
})

async function render(element: ReactElement, path = '/login') {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  root = createRoot(host)
  await act(async () => {
    root!.render(
      createElement(
        QueryClientProvider,
        { client },
        createElement(ThemeProvider, null, createElement(MemoryRouter, { initialEntries: [path] }, element)),
      ),
    )
  })
  // дождаться ответов fetch и перерисовки
  for (let i = 0; i < 5; i++) await act(async () => new Promise((r) => setTimeout(r, 0)))
}

const OIDC = { enabled: true, display_name: 'Войти через Authentik (OIDC)', login_url: '/api/v1/auth/oidc/login' }

function setProviders(p: AuthProviders) {
  responses['/api/v1/auth/providers'] = json(200, p)
}

describe('страница входа', () => {
  it('пароль и SSO: поля логина и пароля плюс кнопка SSO', async () => {
    setProviders({ password: { enabled: true }, oidc: OIDC, saml: { enabled: false } })
    await render(createElement(LoginPage))
    expect(host.querySelector('input[name="username"]')).not.toBeNull()
    expect(host.querySelector('input[name="password"]')).not.toBeNull()
    expect(host.textContent).toContain('Войти через Authentik (OIDC)')
  })

  it('только SSO: без полей, крупная кнопка и объяснение', async () => {
    setProviders({ password: { enabled: false }, oidc: OIDC, saml: { enabled: false } })
    await render(createElement(LoginPage))
    expect(host.querySelector('input[name="username"]')).toBeNull()
    expect(host.querySelector('input[name="password"]')).toBeNull()
    const sso = host.querySelector('[data-testid="sso-only"]')
    expect(sso?.textContent).toContain('Вход по паролю отключён администратором')
    const link = sso?.querySelector('a')
    expect(link?.getAttribute('href')).toBe('/api/v1/auth/oidc/login?next=%2F')
    expect(link?.textContent).toContain('Войти через Authentik (OIDC)')
  })

  it('понятный отказ после SSO: незаведённый пользователь', async () => {
    setProviders({ password: { enabled: false }, oidc: OIDC, saml: { enabled: false } })
    await render(createElement(LoginPage), '/login?error=not_provisioned')
    expect(host.querySelector('[role="alert"]')?.textContent).toContain('автоматическое создание пользователей через SSO выключено')
  })

  it('аварийный ключ: форма с пометкой «только администраторам»', async () => {
    setProviders({ password: { enabled: true, admins_only: true }, oidc: { enabled: false }, saml: { enabled: false } })
    await render(createElement(LoginPage))
    expect(host.querySelector('input[name="password"]')).not.toBeNull()
    expect(host.textContent).toContain('только администраторам по аварийному ключу')
  })
})

function settings(over: Partial<LoginSettings> = {}): LoginSettings {
  return {
    password_enabled: true,
    oidc_enabled: true,
    saml_enabled: true,
    sso_auto_provision: true,
    sso_default_role: 'none',
    allowed_email_domains: [],
    updated_at: '2026-10-25T08:00:00Z',
    updated_by: 'admin',
    methods: {
      password: { configured: true, available: true, active: true },
      oidc: { configured: true, available: true, active: true, display_name: 'Войти через Authentik (OIDC)' },
      saml: { configured: false, available: false, active: false, display_name: 'Войти через Authentik (SAML)' },
    },
    break_glass: false,
    session_method: 'password',
    password_disable_blocker: 'Чтобы выключить вход по паролю, войдите в панель через SSO (OIDC) учётной записью администратора.',
    sso_admins: 0,
    ...over,
  }
}

describe('страница «Вход и регистрация»', () => {
  it('показывает способы, пометку «не настроен» и почему нельзя выключить пароль', async () => {
    responses['/api/v1/auth/login-settings'] = json(200, settings())
    await render(createElement(LoginSettingsPage), '/login-settings')
    const text = host.textContent ?? ''
    expect(text).toContain('Логин и пароль')
    expect(text).toContain('SSO через OIDC')
    expect(text).toContain('Не настроен на сервере')
    expect(text).toContain('войдите в панель через SSO')
    // ненастроенный SAML переключить нельзя
    const switches = [...host.querySelectorAll('button[role="switch"]')]
    const saml = switches.find((s) => s.getAttribute('aria-label') === 'SSO через SAML')
    expect(saml?.hasAttribute('disabled')).toBe(true)
    expect(saml?.getAttribute('aria-checked')).toBe('false')
  })

  it('выключение пароля админом без SSO: кнопка сохранения недоступна, причина видна', async () => {
    responses['/api/v1/auth/login-settings'] = json(200, settings())
    await render(createElement(LoginSettingsPage), '/login-settings')
    const password = [...host.querySelectorAll('button[role="switch"]')].find(
      (s) => s.getAttribute('aria-label') === 'Логин и пароль',
    ) as HTMLButtonElement
    await act(async () => password.click())
    expect(host.querySelector('[role="alert"]')?.textContent).toContain('войдите в панель через SSO')
    const save = [...host.querySelectorAll('button')].find((b) => b.textContent?.trim() === 'Сохранить') as HTMLButtonElement
    expect(save.disabled).toBe(true)
  })

  it('аварийный ключ на сервере — заметное предупреждение', async () => {
    responses['/api/v1/auth/login-settings'] = json(200, settings({ break_glass: true }))
    await render(createElement(LoginSettingsPage), '/login-settings')
    expect(host.textContent).toContain('AUTH_BREAK_GLASS_PASSWORD_LOGIN')
  })
})

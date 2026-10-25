/**
 * Скриншоты для README: снимает панель в тёмной теме 1440×900, пару светлых кадров и мобилку.
 * Только смотрит, ничего на стенде не сохраняет.
 *
 *   BASE_URL=https://... ADMIN_USERNAME=admin ADMIN_PASSWORD=... \
 *   GRAFANA_URL=https://.../grafana GRAFANA_USER=admin GRAFANA_PASSWORD=... \
 *   node --experimental-strip-types --no-warnings screens.ts
 *
 * SHOTS=overview,marketing — снять только эти кадры.
 */
import { mkdirSync } from 'node:fs'
import { dirname, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'
import { chromium, type Browser, type BrowserContext, type Page } from '@playwright/test'

const env = (key: string, fallback = '') => process.env[key] ?? fallback
const trim = (url: string) => url.replace(/\/+$/, '')
const here = dirname(fileURLToPath(import.meta.url))

const BASE = trim(env('BASE_URL', 'http://localhost:8080'))
const ORIGIN = new URL(BASE).origin
const ADMIN = { username: env('ADMIN_USERNAME', 'admin'), password: env('ADMIN_PASSWORD', 'admin') }
const GRAFANA = trim(env('GRAFANA_URL', 'http://localhost:3000'))
const GRAFANA_AUTH = { username: env('GRAFANA_USER', 'admin'), password: env('GRAFANA_PASSWORD', 'admin') }
const OUT = resolve(env('OUT_SCREENS', resolve(here, '../../docs/img/screens')))
const ONLY = env('SHOTS').split(',').map((s) => s.trim()).filter(Boolean)

type Theme = 'dark' | 'light'
interface Shot {
  name: string
  theme?: Theme
  mobile?: boolean
  /** без входа (страница логина, IdP) */
  anonymous?: boolean
  /** снимать всю высоту страницы */
  fullPage?: boolean
  run: (page: Page) => Promise<void>
}

/** Ждём, пока уйдут скелетоны, спиннеры и «Пересчитываем». */
async function settled(page: Page) {
  for (let i = 0; i < 40; i++) {
    const busy = await page.locator('[aria-busy="true"], .animate-pulse').count()
    const recalc = await page.getByText(/Пересчитываем|Считаем, что изменится/).count()
    if (busy === 0 && recalc === 0) break
    await page.waitForTimeout(500)
  }
  await page.waitForLoadState('networkidle', { timeout: 3000 }).catch(() => {})
  // анимации чисел и графиков
  await page.waitForTimeout(1800)
}

async function goto(page: Page, path: string) {
  await page.goto(path.startsWith('http') ? path : `${BASE}${path}`, { waitUntil: 'domcontentloaded' })
  await settled(page)
}

async function scrollTo(page: Page, selector: string, offset = 80) {
  await page.locator(selector).first().evaluate((el, off) => {
    const scroller = el.closest('[role="dialog"]') ? el : null
    if (scroller) el.scrollIntoView({ block: 'start' })
    else window.scrollBy({ top: el.getBoundingClientRect().top - off })
  }, offset)
  await page.waitForTimeout(800)
}

async function topCustomerId(page: Page): Promise<number> {
  const res = await page.request.get(`${BASE}/api/v1/customers`, {
    params: { level: 'high', sort: 'risk_score', order: 'desc', page_size: '1' },
  })
  const body = (await res.json()) as { items: { id: number }[] }
  if (!body.items[0]) throw new Error('нет клиентов высокого риска')
  return body.items[0].id
}

async function demoStep(page: Page, step: RegExp) {
  await goto(page, '/?demo=1')
  const panel = page.getByTestId('demo-panel')
  await panel.waitFor({ timeout: 10_000 })
  await panel.getByRole('tab', { name: 'Сценарий' }).click().catch(() => {})
  await panel.getByRole('button', { name: step }).first().click()
  await page.waitForTimeout(1500)
  await settled(page)
}

const basic = `Basic ${Buffer.from(`${GRAFANA_AUTH.username}:${GRAFANA_AUTH.password}`).toString('base64')}`

async function grafana(page: Page, uid: string) {
  await page.route(`${GRAFANA}/**`, (route) => route.continue({ headers: { ...route.request().headers(), Authorization: basic } }))
  await page.goto(`${GRAFANA}/d/${uid}?orgId=1&kiosk&from=now-6h&to=now`, { waitUntil: 'domcontentloaded' })
  await page.waitForLoadState('networkidle', { timeout: 20_000 }).catch(() => {})
  await page.waitForTimeout(5000)
}

const shots: Shot[] = [
  { name: 'login', anonymous: true, run: (p) => goto(p, '/login') },
  {
    name: 'login-sso',
    anonymous: true,
    run: async (p) => {
      await goto(p, '/login')
      await p.getByRole('link', { name: /Authentik \(OIDC\)/ }).click()
      await p.waitForURL(/\/if\/flow\//, { timeout: 30_000 })
      await p.getByRole('textbox', { name: /Имя пользователя|Username|Email/i }).waitFor({ timeout: 20_000 })
      await p.waitForTimeout(1500)
    },
  },
  { name: 'overview', run: (p) => goto(p, '/') },
  { name: 'overview-light', theme: 'light', run: (p) => goto(p, '/') },
  { name: 'overview-mobile', mobile: true, run: (p) => goto(p, '/') },
  {
    name: 'charts',
    run: async (p) => {
      await goto(p, '/')
      await scrollTo(p, '[data-testid="chart-score-histogram"]', 90)
      await settled(p)
    },
  },
  {
    name: 'reasons',
    run: async (p) => {
      await goto(p, '/')
      await p.locator('main a[data-reason]').first().click()
      await p.getByTestId('reason-panel').waitFor({ timeout: 15_000 })
      await settled(p)
      // в кадре и чип фильтра, и панель причины целиком
      await p.evaluate(() => window.scrollTo(0, 0))
      await p.waitForTimeout(600)
    },
  },
  { name: 'customers', run: (p) => goto(p, '/customers?level=high,medium') },
  {
    name: 'customer',
    run: async (p) => {
      const id = await topCustomerId(p)
      await goto(p, `/customers/${id}?level=high`)
      await p.getByRole('dialog').waitFor({ timeout: 15_000 })
      await settled(p)
    },
  },
  {
    name: 'customer-money',
    run: async (p) => {
      const id = await topCustomerId(p)
      await goto(p, `/customers/${id}?level=high`)
      await p.getByRole('dialog').waitFor({ timeout: 15_000 })
      await settled(p)
      const money = p.getByRole('dialog').locator('section').filter({ has: p.locator('h3', { hasText: /^Деньги/ }) }).first()
      await money.evaluate((el) => el.scrollIntoView({ block: 'start' }))
      await p.waitForTimeout(1200)
    },
  },
  { name: 'model', run: (p) => goto(p, '/model') },
  { name: 'marketing', run: (p) => goto(p, '/marketing') },
  { name: 'quality', run: (p) => goto(p, '/quality') },
  { name: 'playbooks', run: (p) => goto(p, '/playbooks') },
  { name: 'integrations', run: (p) => goto(p, '/integrations') },
  { name: 'audit', run: (p) => goto(p, '/audit') },
  {
    name: 'demo-scenario',
    run: async (p) => {
      await goto(p, '/?demo=1')
      const panel = p.getByTestId('demo-panel')
      await panel.waitFor({ timeout: 10_000 })
      await panel.getByRole('tab', { name: 'Сценарий' }).click().catch(() => {})
      await p.waitForTimeout(800)
    },
  },
  {
    name: 'demo-features',
    run: async (p) => {
      await goto(p, '/?demo=1')
      const panel = p.getByTestId('demo-panel')
      await panel.waitFor({ timeout: 10_000 })
      await panel.getByRole('tab', { name: 'Что реализовано' }).click()
      await p.waitForTimeout(800)
    },
  },
  { name: 'demo-step-overview', run: (p) => demoStep(p, /^Показать: Обзор/) },
  { name: 'demo-step-customer', run: (p) => demoStep(p, /^Показать: Карточка клиента/) },
  {
    name: 'demo-step-model',
    run: async (p) => {
      // шаг включает «Что если» только в браузере, без сохранения
      await demoStep(p, /^Показать: Модель/)
    },
  },
  { name: 'grafana', theme: 'dark', run: (p) => grafana(p, 'churn-radar-churn') },
  { name: 'grafana-platform', theme: 'dark', run: (p) => grafana(p, 'churn-radar-platform') },
]

async function newContext(browser: Browser, shot: Shot): Promise<BrowserContext> {
  const theme = shot.theme ?? 'dark'
  const context = await browser.newContext({
    viewport: shot.mobile ? { width: 390, height: 844 } : { width: 1440, height: 900 },
    deviceScaleFactor: 1,
    isMobile: Boolean(shot.mobile),
    hasTouch: Boolean(shot.mobile),
    locale: 'ru-RU',
    timezoneId: 'Europe/Moscow',
    colorScheme: theme,
    ignoreHTTPSErrors: true,
  })
  await context.addInitScript((t) => {
    try {
      localStorage.setItem('cr-theme', t)
    } catch {
      /* data: и чужие страницы */
    }
  }, theme)
  if (!shot.anonymous) {
    const res = await context.request.post(`${BASE}/api/v1/auth/login`, { data: ADMIN, headers: { Origin: ORIGIN } })
    if (res.status() !== 200) throw new Error(`вход admin: ${res.status()}`)
  }
  return context
}

async function main() {
  mkdirSync(OUT, { recursive: true })
  const browser = await chromium.launch()
  const failed: string[] = []
  for (const shot of shots) {
    if (ONLY.length && !ONLY.includes(shot.name)) continue
    let ok = false
    for (let attempt = 1; attempt <= 2 && !ok; attempt++) {
      const context = await newContext(browser, shot)
      const page = await context.newPage()
      try {
        await shot.run(page)
        await page.mouse.move(0, 0)
        await page.screenshot({ path: resolve(OUT, `${shot.name}.png`), fullPage: Boolean(shot.fullPage) })
        console.log(`✓ ${shot.name}`)
        ok = true
      } catch (e) {
        console.log(`✗ ${shot.name} (попытка ${attempt}): ${e instanceof Error ? e.message.split('\n')[0] : e}`)
      } finally {
        await context.close()
      }
    }
    if (!ok) failed.push(shot.name)
  }
  await browser.close()
  if (failed.length) {
    console.log(`Не сняты: ${failed.join(', ')}`)
    process.exitCode = 1
  }
}

main().catch((e) => {
  console.error(e)
  process.exit(1)
})

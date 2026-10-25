/**
 * Видео-демо ChurnRadar: проходит весь функционал в Chromium и пишет видео с субтитрами,
 * подсветкой и нарисованным курсором. В конце возвращает стенд как был и собирает mp4.
 *
 *   BASE_URL=http://localhost:8080 OUT=out npm run record
 *
 * Сцена, для которой в сборке нет страницы или элемента, пропускается с записью в лог.
 */
import { mkdirSync, writeFileSync } from 'node:fs'
import { dirname, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'
import { chromium, request, type APIRequestContext, type BrowserContext, type Locator, type Page } from '@playwright/test'
import { encode, type Chapter, type Cue } from './encode.ts'
import { installOverlay } from './overlay.ts'
import { architectureSlide, dataUrl, endSlide, terminalSlide, titleSlide } from './slides.ts'

// ---------- параметры ----------

const env = (key: string, fallback = '') => process.env[key] ?? fallback
const trim = (url: string) => url.replace(/\/+$/, '')
const here = dirname(fileURLToPath(import.meta.url))

const BASE = trim(env('BASE_URL', 'http://localhost:8080'))
const ORIGIN = new URL(BASE).origin
const ADMIN = { username: env('ADMIN_USERNAME', 'admin'), password: env('ADMIN_PASSWORD', 'admin') }
const MAILPIT = trim(env('MAILPIT_URL', 'http://localhost:8025'))
const MAILPIT_AUTH = env('MAILPIT_USER') ? { username: env('MAILPIT_USER'), password: env('MAILPIT_PASSWORD') } : null
const GRAFANA = trim(env('GRAFANA_URL', 'http://localhost:3000'))
const GRAFANA_AUTH = { username: env('GRAFANA_USER', 'admin'), password: env('GRAFANA_PASSWORD', 'admin') }
const METRICS_TOKEN = env('METRICS_TOKEN', 'churn-metrics-demo-token')
const SSO_USER = env('SSO_USERNAME', 'marketer')
const SSO_PASSWORD = env('SSO_MARKETER_PASSWORD', env('SSO_PASSWORD', 'marketer'))
const OUT = resolve(env('OUT', resolve(here, 'out')))
const HEADLESS = env('HEADLESS', '1') !== '0'
/** Множитель пауз: 1 — обычный темп, 0.5 — быстрый прогон для отладки. */
const PACE = Number(env('PACE', '1')) || 1
/** Только эти сцены (через запятую, по id) — для отладки отдельных мест. */
const ONLY = env('SCENES').split(',').map((s) => s.trim()).filter(Boolean)

/** Для запросов из Node: localhost в Windows сначала пробует ::1 и может повиснуть, берём IPv4. */
const ipv4 = (url: string) => url.replace(/^(https?:\/\/)localhost(?=[:/]|$)/, '$1127.0.0.1')

const basic = (a: { username: string; password: string }) =>
  `Basic ${Buffer.from(`${a.username}:${a.password}`).toString('base64')}`

// ---------- состояние записи ----------

class SkipScene extends Error {}

let page: Page
let context: BrowserContext
let api: APIRequestContext
let t0 = 0
const cues: Cue[] = []
const chapters: Chapter[] = []
const overlayState = { chapter: '', n: 0, text: '', cursor: null as { x: number; y: number } | null }
let sayUntil = 0
const startedAt = new Date()
const cleanups: { name: string; run: () => Promise<unknown> }[] = []
const report: { id: string; title: string; status: 'ok' | 'skipped' | 'failed'; note?: string; at?: string }[] = []

const now = () => Date.now() - t0
const log = (...args: unknown[]) => console.log(`[${(now() / 1000).toFixed(1).padStart(6)}s]`, ...args)
const pause = (ms: number) => page.waitForTimeout(Math.round(ms * PACE))

function onCleanup(name: string, run: () => Promise<unknown>) {
  cleanups.push({ name, run })
}

// ---------- слой поверх страницы ----------

async function overlay(fn: string, ...args: unknown[]) {
  try {
    await page.evaluate(installOverlay)
    await page.evaluate(({ fn, args }) => {
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      const d = (window as any).__demo
      if (d) d[fn](...args)
    }, { fn, args })
  } catch {
    // страница как раз уходит на другой адрес: состояние вернёт обработчик load
  }
}

async function restoreOverlay() {
  await overlay('restore', overlayState)
}

function readMs(text: string) {
  return Math.max(3800, 1900 + text.length * 62)
}

function closeCue() {
  const last = cues[cues.length - 1]
  if (last && last.end === 0) last.end = now()
}

/** Ждёт, пока зритель дочитает текущую фразу. */
async function settle() {
  const left = sayUntil - Date.now()
  if (left > 0) await page.waitForTimeout(left)
}

/** Показывает фразу. Не блокирует: действия идут, пока её читают; следующая фраза дождётся. */
async function say(text: string) {
  await settle()
  closeCue()
  cues.push({ start: now() + 250, end: 0, text })
  overlayState.text = text
  await overlay('say', text)
  sayUntil = Date.now() + readMs(text) * PACE
}

async function sayAndWait(text: string) {
  await say(text)
  await settle()
}

async function hideSay() {
  await settle()
  closeCue()
  overlayState.text = ''
  await overlay('say', '')
}

async function chapter(title: string, n: number) {
  chapters.push({ start: now(), title })
  overlayState.chapter = title
  overlayState.n = n
  await overlay('chapter', title, n)
}

/** Подсветить элементы: остальное затемняется, вокруг целей рамка. */
async function spot(...locators: Locator[]) {
  const handles = []
  for (const l of locators) {
    const h = await l.first().elementHandle({ timeout: 4000 }).catch(() => null)
    if (h) handles.push(h)
  }
  if (!handles.length) return
  await scrollTo(locators[0]!)
  try {
    await page.evaluate(installOverlay)
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    await page.evaluate((els) => (window as any).__demo?.spot(els), handles)
  } catch {
    /* страница сменилась */
  }
}

async function unspot() {
  await overlay('unspot')
}

/** Прокрутить так, чтобы элемент был в свободной от субтитров части экрана. */
async function scrollTo(locator: Locator) {
  await locator
    .first()
    .evaluate((el) => {
      const inDialog = el.closest('[role="dialog"]')
      if (inDialog) {
        el.scrollIntoView({ block: 'center', behavior: 'smooth' })
        return
      }
      const vh = window.innerHeight
      const free = vh - 200
      const r = el.getBoundingClientRect()
      const top = r.height > free - 90 ? r.top - 84 : r.top - (free - r.height) / 2 - 10
      if (Math.abs(top) > 24) window.scrollBy({ top, behavior: 'smooth' })
    })
    .catch(() => {})
  await page.waitForTimeout(650)
}

// ---------- курсор и действия ----------

async function cursorTo(x: number, y: number, ms?: number) {
  const from = overlayState.cursor ?? { x: 720, y: 450 }
  const dist = Math.hypot(x - from.x, y - from.y)
  const dur = ms ?? Math.round(Math.min(1100, Math.max(380, dist * 0.9)))
  overlayState.cursor = { x, y }
  await overlay('cursorTo', x, y, dur)
  await page.mouse.move(x, y, { steps: Math.max(4, Math.round(dur / 25)) })
  await page.waitForTimeout(dur)
}

async function move(locator: Locator) {
  const l = locator.first()
  await l.waitFor({ state: 'visible', timeout: 8000 })
  await scrollTo(l)
  const box = await l.boundingBox()
  if (!box) return null
  const x = Math.round(box.x + box.width / 2)
  const y = Math.round(box.y + box.height / 2)
  await cursorTo(x, y)
  return { x, y }
}

async function click(locator: Locator, opts: { force?: boolean } = {}) {
  const at = await move(locator)
  if (at) await overlay('ripple', at.x, at.y)
  await locator.first().click({ timeout: 8000, force: opts.force })
  await pause(450)
}

async function type(locator: Locator, text: string, opts: { clear?: boolean } = {}) {
  await click(locator)
  if (opts.clear !== false) await locator.first().fill('')
  await locator.first().pressSequentially(text, { delay: Math.round(85 * PACE) })
  await pause(350)
}

async function need(locator: Locator, what: string, timeout = 10_000) {
  try {
    await locator.first().waitFor({ state: 'visible', timeout })
  } catch {
    throw new SkipScene(`нет элемента: ${what}`)
  }
}

async function visible(locator: Locator, timeout = 2500) {
  return locator.first().waitFor({ state: 'visible', timeout }).then(() => true, () => false)
}

async function goto(path: string) {
  const url = /^(https?:|data:)/.test(path) ? path : `${BASE}${path}`
  await page.goto(url, { waitUntil: 'domcontentloaded', timeout: 45_000 })
  await restoreOverlay()
  await pause(700)
}

/** Переход по пункту меню в шапке, если его нет — прямой адрес. */
async function nav(name: string, path: string) {
  const link = page.getByRole('navigation', { name: 'Разделы' }).getByRole('link', { name, exact: true })
  if (await visible(link, 3000)) {
    await click(link)
    await page.waitForURL((u) => u.pathname.startsWith(path), { timeout: 10_000 }).catch(() => {})
  } else {
    await goto(path)
  }
  await pause(600)
}

const card = (title: string) =>
  page.locator('div.rounded-xl').filter({ has: page.getByText(title, { exact: true }) }).last()

async function json<T>(res: Promise<import('@playwright/test').APIResponse>): Promise<T> {
  const r = await res
  if (!r.ok()) throw new Error(`${r.url()} → ${r.status()}`)
  return (await r.json()) as T
}

async function loginApi(ctx: APIRequestContext) {
  const res = await ctx.post('/api/v1/auth/login', { data: ADMIN, headers: { Origin: ORIGIN } })
  if (res.status() !== 200) throw new Error(`вход admin через API: ${res.status()}`)
}

// ---------- Mailpit и Grafana ----------

type MailpitMessage = { ID: string; Subject: string; To: { Address: string }[]; Created: string }

async function mailpitMessages(): Promise<MailpitMessage[]> {
  const res = await api.get(ipv4(`${MAILPIT}/api/v1/messages`), {
    params: { limit: '100' },
    headers: MAILPIT_AUTH ? { Authorization: basic(MAILPIT_AUTH) } : {},
  })
  if (!res.ok()) return []
  return ((await res.json()) as { messages: MailpitMessage[] }).messages ?? []
}

async function mailpitDelete(ids: string[]) {
  if (!ids.length) return
  await api.delete(ipv4(`${MAILPIT}/api/v1/messages`), {
    data: { IDs: ids },
    headers: MAILPIT_AUTH ? { Authorization: basic(MAILPIT_AUTH) } : {},
  })
}

const fresh = (m: MailpitMessage) => new Date(m.Created).getTime() >= startedAt.getTime() - 5000

// ---------- общие данные сцен ----------

type Weight = { code: string; title: string; weight: number; min: number; max: number; enabled: boolean }
type Settings = { version: number; weights: Weight[] }
type Action = { status: 'emailed' | 'resolved' | 'snoozed'; until: string | null; note: string | null } | null

const DEMO_FEATURE = 'prolong_wait'
let customer: { id: number; email: string } | null = null

async function pickCustomer() {
  if (customer) return customer
  const queue = await json<{ items: { id: number; email: string }[] }>(api.get('/api/v1/contact-today', { params: { limit: '1' } })).catch(() => ({ items: [] }))
  if (queue.items[0]) return (customer = queue.items[0])
  const high = await json<{ items: { id: number; email: string }[] }>(
    api.get('/api/v1/customers', { params: { level: 'high', sort: 'risk_score', order: 'desc', page_size: '1' } }),
  )
  if (high.items[0]) return (customer = high.items[0])
  const any = await json<{ items: { id: number; email: string }[] }>(api.get('/api/v1/customers', { params: { page_size: '1' } }))
  if (!any.items[0]) throw new SkipScene('в данных нет клиентов')
  return (customer = any.items[0])
}

const dialog = () => page.getByRole('dialog').first()
const section = (title: string) =>
  dialog().locator('section').filter({ has: page.locator('h3').filter({ hasText: new RegExp(`^${title}`) }) }).first()

async function openCustomerCard() {
  const c = await pickCustomer()
  if (new URL(page.url()).pathname === `/customers/${c.id}` && (await visible(dialog(), 1000))) return c
  await goto(`/customers/${c.id}?level=high`)
  await need(dialog(), 'карточка клиента')
  return c
}

// ---------- сцены ----------

interface Scene {
  id: string
  title: string
  run: () => Promise<void>
}

const scenes: Scene[] = [
  {
    id: 'intro',
    title: 'ChurnRadar',
    run: async () => {
      await goto(dataUrl(titleSlide(BASE)))
      await sayAndWait('ChurnRadar показывает, кто из клиентов хостинга может уйти, почему и сколько денег это будет стоить.')
      await sayAndWait('Пройдём по всему функционалу так, как им пользуется маркетолог: от входа до того, что под капотом.')
    },
  },
  {
    id: 'login',
    title: 'Вход',
    run: async () => {
      await goto('/login')
      const login = page.getByLabel('Логин')
      await need(login, 'форма входа')
      await cursorTo(720, 470, 400)
      await spot(page.locator('form').first())
      await say('Без входа не открывается ни одна страница и ни один запрос к API. Сессия живёт в httpOnly-cookie, а проверяет её шлюз.')
      const sso = page.getByRole('link', { name: /Войти через/ })
      if (await visible(sso, 1500)) {
        await settle()
        await spot(sso)
        await say('Войти можно по паролю или через корпоративный Authentik, по OIDC и SAML. К SSO вернёмся ближе к концу.')
      }
      await settle()
      await unspot()
      await type(login, ADMIN.username)
      await type(page.getByLabel('Пароль', { exact: true }), ADMIN.password)
      await click(page.getByRole('button', { name: 'Войти', exact: true }))
      await page.waitForURL((u) => !u.pathname.startsWith('/login'), { timeout: 15_000 })
      await restoreOverlay()
      await pause(800)
    },
  },
  {
    id: 'overview',
    title: 'Обзор',
    run: async () => {
      if (new URL(page.url()).pathname !== '/') await goto('/')
      const high = page.getByTestId('kpi-high')
      await need(high, 'KPI на обзоре')
      await pause(1200)
      await spot(high.locator('xpath=..'))
      await say('Обзор начинается с главного: сколько клиентов под высоким риском и сколько денег мы потеряем за 30 дней, если ничего не делать.')
      const loss = page.getByTestId('kpi-expected-loss')
      await settle()
      await spot(loss)
      await say('Ожидаемая потеря — не сумма всех счетов. Цена каждой услуги умножена на вероятность, что её не продлят, а то, что уже покрыто балансом, вычтено.')
      const info = loss.getByRole('button').first()
      if (await visible(info, 1000)) await move(info)
      await settle()
      await cursorTo(1300, 120)

      const today = page.getByTestId('contact-today')
      if (await visible(today, 3000)) {
        await spot(today)
        await say('«С кем связаться сегодня» — с этого маркетолог начинает день. Очередь отсортирована по деньгам, которые можно спасти: сверху тот, на ком потеряем больше всего.')
        const mark = today.getByTestId('contact-today-row').first().getByRole('button', { name: 'Отметить' })
        if (await visible(mark, 1500)) {
          await settle()
          await click(mark)
          await say('Клиента можно отметить: ответил и вопрос решён, или отложить на пару дней. Он уйдёт из очереди, а в журнале останется, кто и когда это сделал.')
          await settle()
          await page.keyboard.press('Escape')
          await pause(400)
        }
        await settle()
      }

      const chart = card('Отток услуг: факт и прогноз')
      if (await visible(chart, 3000)) {
        await spot(chart)
        await say('На графике факт и прогноз вместе: сколько услуг уже ушло по дням, сколько непродлений ждём впереди и сколько услуг сейчас под риском.')
        const plot = chart.locator('svg.recharts-surface').first()
        const box = await plot.boundingBox().catch(() => null)
        if (box) {
          await cursorTo(Math.round(box.x + box.width * 0.62), Math.round(box.y + box.height * 0.5))
          await pause(900)
          await cursorTo(Math.round(box.x + box.width * 0.8), Math.round(box.y + box.height * 0.45))
        }
        await settle()
      }
      const levels = card('Клиенты по уровню риска')
      if (await visible(levels, 2000)) {
        await spot(levels)
        await say('Справа — распределение по уровням риска. Каждая строка ведёт в таблицу клиентов с готовым фильтром.')
        await settle()
      }
      const reasons = card('Главные причины риска')
      if (await visible(reasons, 2000)) {
        await spot(reasons)
        await say('А внизу — главные причины риска по всей базе: что чаще всего толкает клиентов к уходу и сколько денег за этим стоит.')
        await settle()
      }
    },
  },
  {
    id: 'customers',
    title: 'Клиенты: фильтр, сортировка, поиск',
    run: async () => {
      await nav('Клиенты', '/customers')
      const table = page.getByRole('table', { name: 'Клиенты под риском' })
      await need(table, 'таблица клиентов')
      await spot(table.locator('xpath=ancestor::div[contains(@class,"rounded-xl")][1]'))
      await say('Здесь все клиенты: оценка риска, уровень и главная причина в одной строке.')
      await settle()
      const chip = page.getByRole('group', { name: 'Фильтр по уровню риска' }).getByRole('button', { name: /Высокий/ })
      if (await visible(chip)) {
        await unspot()
        await click(chip)
        await spot(page.getByRole('group', { name: 'Фильтр по уровню риска' }))
        await say('Фильтр по уровню оставляет только тех, кем надо заняться в первую очередь.')
        await settle()
      }
      const balance = page.getByRole('columnheader', { name: /^Баланс/ })
      if (await visible(balance)) {
        await unspot()
        await click(balance.getByRole('button'))
        await spot(balance)
        await say('Сортировка — по ID, email, балансу и пополнениям, в обе стороны. Всё это сохраняется в адресе, ссылкой можно поделиться с коллегой.')
        await pause(1500)
        await click(balance.getByRole('button'))
        await settle()
      }
      const search = page.getByLabel('Поиск по email или ID')
      if (await visible(search)) {
        await unspot()
        const first = await table.locator('tbody tr[aria-label]').first().getAttribute('aria-label').catch(() => null)
        const email = (first ?? '').replace(/^Клиент\s+/, '')
        const fragment = email.includes('@') ? email.split('@')[0]!.slice(-4) : '00'
        await say('Поиск — по email или ID клиента, прямо по мере ввода.')
        await type(search, fragment)
        await pause(1600)
        await settle()
        const clear = page.getByRole('button', { name: 'Очистить поиск' })
        if (await visible(clear, 1000)) await click(clear)
      }
      const csv = page.getByTestId('customers-csv')
      if (await visible(csv)) {
        await spot(csv)
        await say('Выгрузка в CSV берёт ровно те же фильтры и сортировку: удобно отдать список в CRM или открыть в Excel.')
        await settle()
      }
    },
  },
  {
    id: 'customer',
    title: 'Карточка клиента',
    run: async () => {
      const c = await pickCustomer()
      await goto('/customers?level=high')
      const row = page.getByRole('row', { name: `Клиент ${c.email}` })
      if (await visible(row, 5000)) {
        await click(row)
      } else {
        await goto(`/customers/${c.id}?level=high`)
      }
      await need(dialog(), 'карточка клиента')
      await pause(1000)
      const why = section('Почему')
      if (await visible(why)) {
        await spot(why)
        await say('Карточка объясняет оценку: почему клиент в риске — обычными словами, без формул.')
        await settle()
      }
      const parts = section('Из чего сложилась оценка')
      if (await visible(parts)) {
        await spot(parts)
        await say('Ниже вклад каждого признака в баллах: видно, что именно перевесило и насколько.')
        await settle()
      }
      const money = section('Деньги')
      if (await visible(money)) {
        await spot(money)
        await say('Деньги на кону: выручка в месяц, ожидаемая потеря, максимум потерь и на сколько дней хватит баланса.')
        await settle()
      }
      const todo = section('Что сделать')
      if (await visible(todo)) {
        await spot(todo)
        await say('И конкретное действие: что сделать с этим клиентом и насколько это снизит ожидаемую потерю.')
        await settle()
      }
      const work = page.getByTestId('customer-action')
      if (await visible(work)) {
        await spot(work)
        await say('Блок «Связь с клиентом»: статус, кнопка письма, отметки и короткий журнал — видно, кто и что уже сделал.')
        await settle()
      }
    },
  },
  {
    id: 'email',
    title: 'Письмо клиенту',
    run: async () => {
      const c = await openCustomerCard()
      const previous = await json<{ action: Action }>(api.get(`/api/v1/customers/${c.id}/action`)).then((r) => r.action).catch(() => null)
      const panel = page.getByTestId('customer-action')
      const write = panel.getByRole('button', { name: 'Написать письмо' })
      await need(write, 'кнопка «Написать письмо»')
      await unspot()
      await click(write)
      const mail = page.getByTestId('email-dialog')
      await need(mail, 'окно письма')
      await pause(900)
      await spot(mail)
      await say('Письмо собирается само: шаблон выбран по главной причине риска, сумма и дата клиента уже подставлены.')
      await settle()
      const template = mail.getByLabel('Шаблон письма')
      if (await visible(template, 1500)) {
        await spot(template)
        await say('Шаблон можно сменить, а тему и текст поправить руками перед отправкой.')
        await move(mail.getByLabel('Тема'))
        await settle()
      }
      const subject = (await mail.getByLabel('Тема').inputValue().catch(() => '')) || ''
      const previewBtn = mail.getByRole('button', { name: 'Как увидит клиент' })
      if (await visible(previewBtn, 1500)) {
        await unspot()
        await click(previewBtn)
        await spot(mail.getByTestId('letter-preview'))
        await say('Так письмо увидит клиент: с вёрсткой, как в почтовом ящике.')
        await settle()
      }
      await unspot()
      onCleanup(`статус клиента #${c.id}`, async () => {
        if (previous === null) {
          await api.delete(`/api/v1/customers/${c.id}/action`, { headers: { Origin: ORIGIN } })
        } else {
          const data: Record<string, unknown> = { status: previous.status }
          if (previous.status === 'snoozed' && previous.until) data.snoozed_until = previous.until
          if (previous.note) data.note = previous.note
          await api.put(`/api/v1/customers/${c.id}/action`, { data, headers: { Origin: ORIGIN } })
        }
      })
      onCleanup('письмо клиенту в Mailpit', async () => {
        const ids = (await mailpitMessages()).filter((m) => fresh(m) && m.To.some((t) => t.Address === c.email)).map((m) => m.ID)
        await mailpitDelete(ids)
      })
      await click(mail.getByRole('button', { name: 'Отправить' }))
      await mail.waitFor({ state: 'detached', timeout: 20_000 }).catch(() => {})
      const badge = panel.getByTestId('action-badge')
      if (await visible(badge, 8000)) {
        await spot(panel)
        await say('Письмо ушло через SMTP. У клиента статус «Письмо отправлено», запись в журнале, и из очереди «С кем связаться» он пропал.')
        await settle()
      }
      // письмо в Mailpit
      let found: MailpitMessage | undefined
      for (let i = 0; i < 20 && !found; i++) {
        found = (await mailpitMessages()).find(
          (m) => fresh(m) && m.To.some((t) => t.Address === c.email) && (!subject || m.Subject === subject),
        )
        if (!found) await page.waitForTimeout(1000)
      }
      if (!found) {
        log('письмо в Mailpit не нашлось — пропускаю показ ящика')
        return
      }
      await unspot()
      await goto(`${MAILPIT}/view/${found.ID}`)
      await pause(1200)
      await cursorTo(760, 420)
      await say('На стенде письма ловит Mailpit. Вот оно глазами клиента: тема, сумма, дата и обратный адрес для ответа.')
      await settle()
      await pause(800)
    },
  },
  {
    id: 'campaign',
    title: 'Массовая рассылка',
    run: async () => {
      await goto('/customers?level=high&sort=expected_loss&order=desc')
      const all = page.getByRole('checkbox', { name: 'Выбрать всех на странице' })
      await need(all, 'выбор клиентов')
      await pause(600)
      await click(all)
      const bar = page.getByTestId('selection-bar')
      await need(bar, 'панель выбранных')
      await spot(bar)
      await say('Можно выбрать сразу несколько клиентов и написать всем — каждому своё письмо по его причине риска.')
      await settle()
      await unspot()
      await click(bar.getByRole('button', { name: 'Написать выбранным' }))
      const campaign = page.getByTestId('campaign-dialog')
      await need(campaign, 'окно рассылки')
      await pause(1200)
      await spot(campaign.getByTestId('campaign-summary'))
      await say('Перед отправкой видно, сколько писем уйдёт и кого пропустим: кому уже писали на этой неделе, второе письмо не придёт.')
      await settle()
      const preview = campaign.getByTestId('letter-preview')
      if (await visible(preview, 1500)) {
        await spot(preview)
        await say('Ниже пример письма для первого клиента из списка.')
        await settle()
      }
      await unspot()
      await click(campaign.getByRole('button', { name: 'Отмена' }))
      await say('Сейчас ничего не отправляем, это только предпросмотр.')
      await settle()
      await goto('/mail')
      const stats = page.getByTestId('mail-stats')
      if (await visible(stats, 6000)) {
        await spot(stats)
        await say('Все рассылки и шаблоны — на странице «Письма»: сколько отправлено, сколько не дошло и какие шаблоны есть.')
        await settle()
        const templates = card('Шаблоны')
        if (await visible(templates, 1500)) {
          await spot(templates)
          await pause(2500)
        }
      }
    },
  },
  {
    id: 'marketing',
    title: 'Маркетинг',
    run: async () => {
      await nav('Маркетинг', '/marketing')
      const mrr = page.getByTestId('mkt-mrr')
      await need(mrr, 'страница «Маркетинг»')
      await pause(1500)
      await spot(mrr, page.getByTestId('mkt-arpu'), page.getByTestId('mkt-ltv'))
      await say('Для маркетолога — привычные метрики по тем же данным: месячная выручка MRR, доход с клиента ARPU и LTV.')
      await settle()
      const info = page.getByTestId('mkt-ltv').getByRole('button', { name: /^Что такое/ })
      if (await visible(info, 1500)) {
        await unspot()
        await move(info)
        await say('У каждой цифры есть значок «i»: формула и пример простыми словами.')
        await settle()
        await cursorTo(1300, 140)
      }
      const churn = page.getByTestId('mkt-customer-churn')
      if (await visible(churn, 1500)) {
        await spot(churn, page.getByTestId('mkt-revenue-churn'))
        await say('Отток клиентов и отток выручки считаются отдельно: уход одного крупного клиента бьёт по деньгам сильнее, чем десятка мелких.')
        await settle()
      }
      const retention = page.getByTestId('mkt-retention')
      if (await visible(retention, 1500)) {
        await spot(retention)
        await say('Главное здесь — связь с риском: видно, на сколько вырастет LTV базы, если удержать клиентов высокого риска.')
        await settle()
      }
      const economics = page.getByTestId('mkt-economics')
      if (await visible(economics, 1500)) {
        await spot(economics)
        await say('Маржу и стоимость привлечения задают руками: в данных хостинга их нет. Отсюда считаются LTV/CAC и окупаемость.')
        await settle()
      }
      const glossary = page.getByTestId('mkt-glossary')
      if (await visible(glossary, 1500)) {
        await spot(glossary)
        await say('Внизу словарь терминов — MRR, ARPPU, Retention, Payback, — чтобы маркетинг и разработка говорили на одном языке.')
        await settle()
      }
    },
  },
  {
    id: 'what-if',
    title: 'Модель и «Что если»',
    run: async () => {
      await nav('Модель', '/model')
      const settings = await json<Settings>(api.get('/api/v1/model/settings'))
      const w = settings.weights.find((x) => x.code === DEMO_FEATURE) ?? settings.weights.find((x) => x.enabled)
      if (!w) throw new SkipScene('в модели нет признаков')
      const sw = page.getByRole('switch', { name: 'Что если' })
      await need(sw, 'переключатель «Что если»')
      const row = page.locator('li').filter({ has: page.getByRole('textbox', { name: `Вес: ${w.title}` }) }).first()
      await need(row, `строка веса «${w.title}»`)
      await spot(row)
      await say(`Модель прозрачная: у каждого признака свой вес. Вот «${w.title}», и настраивает его сам маркетолог, без программиста.`)
      await settle()
      await unspot()
      if ((await sw.getAttribute('aria-checked')) !== 'true') await click(sw)
      await say('Но сначала веса можно примерить в режиме «Что если»: пока не нажали «Применить», в базе ничего не меняется.')
      const slider = row.getByRole('slider').first()
      const preview = page.waitForResponse((r) => r.url().includes('/api/v1/model/settings/preview') && r.ok(), { timeout: 15_000 }).catch(() => null)
      const box = await slider.boundingBox().catch(() => null)
      if (box) {
        await scrollTo(slider)
        const b = (await slider.boundingBox())!
        const x = Math.round(b.x + b.width / 2)
        const y = Math.round(b.y + b.height / 2)
        await cursorTo(x, y)
        await page.mouse.down()
        const toX = w.weight > (w.min + w.max) / 2 ? x - 320 : x + 260
        overlayState.cursor = { x: toX, y }
        await overlay('cursorTo', toX, y, 1400)
        await page.mouse.move(toX, y, { steps: 45 })
        await page.waitForTimeout(400)
        await page.mouse.up()
      }
      let res = await preview
      if (!res) {
        const input = page.getByRole('textbox', { name: `Вес: ${w.title}` })
        await type(input, '0')
        await input.blur()
        res = await page.waitForResponse((r) => r.url().includes('/settings/preview') && r.ok(), { timeout: 15_000 }).catch(() => null)
      }
      await pause(900)
      const whatIf = page.getByTestId('what-if')
      await need(whatIf, 'блок «Что если»')
      await settle()
      await spot(whatIf)
      await say('Сразу видно «было → станет»: ожидаемая потеря, сколько клиентов в высоком риске и кто именно сменит уровень.')
      await settle()
      await unspot()
      await click(whatIf.getByRole('button', { name: 'Отменить' }))
      await spot(row)
      await say('Не понравилось — «Отменить», и всё как было. Ничего не сохранилось, версия модели та же.')
      await settle()
      const after = await json<Settings>(api.get('/api/v1/model/settings'))
      if (after.version !== settings.version) log(`ВНИМАНИЕ: версия модели сменилась ${settings.version} → ${after.version}`)
    },
  },
  {
    id: 'recalc',
    title: 'Живой пересчёт',
    run: async () => {
      if (!new URL(page.url()).pathname.startsWith('/model')) await nav('Модель', '/model')
      const sw = page.getByRole('switch', { name: 'Что если' })
      await need(sw, 'страница модели')
      if ((await sw.getAttribute('aria-checked')) === 'true') {
        const cancel = page.getByTestId('what-if').getByRole('button', { name: 'Отменить' })
        if (await visible(cancel, 1000)) await click(cancel)
      }
      const settings = await json<Settings>(api.get('/api/v1/model/settings'))
      const w = settings.weights.find((x) => x.code === DEMO_FEATURE) ?? settings.weights.find((x) => x.enabled)
      if (!w) throw new SkipScene('в модели нет признаков')
      const original = w.weight
      const target = original > (w.min + w.max) / 2 ? w.min : Math.round(w.min + (w.max - w.min) * 0.8)
      const shown = (v: number) => String(v).replace('.', ',')
      const input = page.getByRole('textbox', { name: `Вес: ${w.title}` })
      await need(input, 'поле веса')
      let restored = false
      onCleanup(`вес «${w.title}» = ${original}`, async () => {
        if (restored) return
        await api.patch('/api/v1/model/settings', { data: { weights: [{ code: w.code, weight: original }] }, headers: { Origin: ORIGIN } })
      })
      const summary = page.getByRole('group', { name: 'Итог по всем клиентам' })
      if (await visible(summary, 2000)) await spot(summary, input)
      await say('Теперь по-настоящему: меняю вес и сохраняю. Пересчёт идёт через шину событий, а страницу никто не перезагружает.')
      await settle()
      const patched = page.waitForResponse((r) => r.request().method() === 'PATCH' && r.url().includes('/api/v1/model/settings'), { timeout: 15_000 }).catch(() => null)
      await click(input)
      await page.keyboard.press('Control+A')
      await input.pressSequentially(shown(target), { delay: 120 })
      await page.keyboard.press('Tab')
      const p = await patched
      log(`PATCH веса: ${p?.status() ?? 'нет ответа'}`)
      const toast = page.getByTestId('recalc-toast')
      const toastShown = await visible(toast, 30_000)
      if (toastShown) await spot(toast, summary)
      else await spot(summary)
      await say('Через пару секунд — новые цифры: оценки пересчитаны, итог по всем клиентам и обзор обновились сами.')
      await settle()
      await pause(1000)
      // возвращаем как было
      await unspot()
      const back = page.waitForResponse((r) => r.request().method() === 'PATCH' && r.url().includes('/api/v1/model/settings'), { timeout: 15_000 }).catch(() => null)
      await click(input)
      await page.keyboard.press('Control+A')
      await input.pressSequentially(shown(original), { delay: 120 })
      await page.keyboard.press('Tab')
      const b = await back
      if (b?.ok()) restored = true
      const history = card('История изменений')
      if (await visible(history, 2000)) await spot(history)
      await say('И возвращаю вес обратно. Каждая правка остаётся в истории изменений: кто, когда и что поменял.')
      await settle()
    },
  },
  {
    id: 'quality',
    title: 'Качество модели',
    run: async () => {
      await nav('Качество модели', '/quality')
      const auc = page.getByTestId('quality-auc')
      await need(auc, 'страница «Качество модели»')
      await pause(1500)
      const lift = page.getByTestId('quality-lift')
      await spot(lift, auc)
      await say('Чтобы не верить модели на слово, мы прогоняем месяц синтетического будущего и смотрим, кто ушёл на самом деле.')
      const liftText = ((await lift.locator('[data-role=value]').innerText().catch(() => '')) || '').trim()
      const aucText = ((await auc.locator('[data-role=value]').innerText().catch(() => '')) || '').trim()
      await say(
        liftText && aucText
          ? `Клиенты высокого риска уходят ${liftText} чаще среднего, ROC AUC — ${aucText}: модель правильно ставит опасных клиентов выше остальных.`
          : 'Lift показывает, во сколько раз чаще уходят клиенты высокого риска, а ROC AUC — насколько верно модель их упорядочивает.',
      )
      await settle()
      const chart = page.getByTestId('quality-lift-chart')
      if (await visible(chart, 1500)) {
        await spot(chart, page.getByTestId('quality-roc'))
        await say('Под цифрами — графики, и у каждого есть таблица с теми же данными.')
        await settle()
      }
      const scenario = page.getByRole('button', { name: 'Продления по сроку' })
      if (await visible(scenario, 1500)) {
        const group = scenario.locator('xpath=..')
        const original = group.locator('button[aria-pressed="true"]').first()
        const originalName = (await original.innerText().catch(() => '')).trim()
        await unspot()
        await click(scenario)
        const baselines = page.getByTestId('quality-baselines')
        if (await visible(baselines, 2000)) await spot(baselines)
        await say('Сценарии проверки переключаются, а сравнение с простыми правилами показывает, насколько модель полезнее правила «ждёт оплаты».')
        await settle()
        if (originalName && originalName !== 'Продления по сроку') {
          await unspot()
          await click(group.getByRole('button', { name: originalName, exact: true }))
        }
      }
      const method = page.getByTestId('quality-method')
      if (await visible(method, 1500)) {
        await spot(method)
        await say('И честно про ограничения: данные синтетические, поэтому на реальной базе эти цифры нужно перемерить.')
        await settle()
      }
    },
  },
  {
    id: 'notifications',
    title: 'Уведомления',
    run: async () => {
      await goto('/notifications')
      await need(page.getByRole('heading', { name: 'Уведомления' }), 'страница «Уведомления»')
      await pause(800)
      const events = card('О чём сообщать')
      if (await visible(events, 3000)) {
        await spot(events)
        await say('Уведомления личные: каждый выбирает, о чём сообщать и с какого порога. Например, крупный клиент ушёл в высокий риск.')
        await settle()
      }
      const where = card('Куда присылать')
      if (await visible(where, 2000)) {
        await spot(where)
        await say('Каналы — панель, почта или Telegram-бот. Есть тихие часы, чтобы не будить ночью, и ежедневная сводка.')
        await settle()
      }
      const testBtn = page.getByRole('button', { name: 'Отправить тестовое уведомление' })
      if ((await visible(testBtn, 2000)) && (await testBtn.isEnabled())) {
        onCleanup('тестовое уведомление в Mailpit', async () => {
          const ids = (await mailpitMessages()).filter((m) => fresh(m) && m.Subject.includes('Тестовое уведомление')).map((m) => m.ID)
          await mailpitDelete(ids)
        })
        await unspot()
        await click(testBtn)
        const results = page.getByTestId('test-results')
        if (await visible(results, 15_000)) await spot(results)
        await say('Тестовое уведомление проверяет, что всё доходит: по каждому каналу видно, отправлено или нет.')
        await settle()
      }
      const bell = page.getByTestId('alerts-bell')
      if (await visible(bell, 2000)) {
        await unspot()
        await click(bell)
        const inbox = page.getByTestId('alerts-inbox')
        if (await visible(inbox, 5000)) await spot(inbox)
        await say('Колокольчик в шапке — личная лента событий. Каждая запись ведёт прямо к нужным клиентам.')
        await settle()
        await unspot()
        await page.keyboard.press('Escape')
        await pause(500)
      }
    },
  },
  {
    id: 'users',
    title: 'Пользователи и роли',
    run: async () => {
      await nav('Пользователи', '/users')
      await need(page.getByRole('heading', { name: 'Пользователи', level: 1 }), 'страница «Пользователи»')
      await pause(800)
      const list = page.locator('table').first()
      if (await visible(list, 3000)) await spot(list)
      await say('Администратор управляет доступом: кто входит в панель, с какой ролью и как.')
      await settle()
      const add = page.getByRole('button', { name: 'Добавить пользователя' })
      if (await visible(add)) {
        await unspot()
        await click(add)
        const d = page.getByRole('dialog', { name: 'Новый пользователь' })
        await need(d, 'окно нового пользователя')
        await type(d.getByLabel('Логин'), 'marina')
        await type(d.getByLabel('Имя'), 'Марина, маркетолог')
        const role = d.getByLabel('Роль')
        if (await visible(role, 1000)) await spot(role)
        await say('Ролей две: администратор настраивает модель и данные, аналитик только смотрит. И этот запрет держит сервер, а не интерфейс.')
        await settle()
        const how = d.getByRole('radiogroup', { name: 'Как будет входить' })
        if (await visible(how, 1000)) {
          await spot(how)
          await say('Пароль можно сгенерировать, задать вручную или пускать сотрудника только через SSO.')
          await settle()
        }
        await unspot()
        await click(d.getByRole('button', { name: 'Отмена' }))
        await pause(400)
      }
      const journal = page.getByRole('heading', { name: 'Журнал действий' })
      if (await visible(journal, 2000)) {
        await spot(journal.locator('xpath=..'))
        await say('Создание, блокировка, смена роли — всё попадает в журнал действий. Заблокированного выкидывает из панели сразу.')
        await settle()
      }
    },
  },
  {
    id: 'sso',
    title: 'Вход через Authentik (SSO)',
    run: async () => {
      const providers = await json<{ oidc?: { enabled: boolean } }>(api.get('/api/v1/auth/providers')).catch(() => null)
      if (!providers?.oidc?.enabled) throw new SkipScene('SSO-провайдеры выключены')
      const users = await json<{ items: { id: string; username: string }[] }>(api.get('/api/v1/auth/users', { params: { q: SSO_USER } })).catch(() => ({ items: [] }))
      const existed = users.items.some((u) => u.username === SSO_USER)
      if (!existed) {
        onCleanup(`учётка ${SSO_USER}, созданная входом через SSO`, async () => {
          const r = await json<{ items: { id: string; username: string }[] }>(api.get('/api/v1/auth/users', { params: { q: SSO_USER } }))
          const u = r.items.find((x) => x.username === SSO_USER)
          if (u) await api.delete(`/api/v1/auth/users/${u.id}`, { headers: { Origin: ORIGIN } })
        })
      }
      await context.clearCookies()
      try {
        await goto('/login')
        const oidc = page.getByRole('link', { name: /Authentik \(OIDC\)/ })
        await need(oidc, 'кнопка входа через OIDC')
        await spot(page.getByRole('link', { name: /Войти через/ }))
        await say('Вход через корпоративный Authentik — по OIDC или SAML. Роль приходит из группы в каталоге, доступ настраивают в одном месте.')
        await settle()
        await unspot()
        await click(oidc)
        await page.waitForURL(/\/if\/flow\//, { timeout: 30_000 })
        await restoreOverlay()
        await say(`Маркетолог входит своей корпоративной учёткой, например ${SSO_USER}.`)
        const loginBox = page.getByRole('textbox', { name: /Имя пользователя|Username|Email/i })
        await need(loginBox, 'форма Authentik', 20_000)
        await type(loginBox, SSO_USER)
        await page.keyboard.press('Enter')
        const pass = page.getByRole('textbox', { name: /Пароль|Password/i })
        await need(pass, 'поле пароля Authentik', 20_000)
        await type(pass, SSO_PASSWORD)
        await page.keyboard.press('Enter')
        const consent = page.getByRole('button', { name: /Continue|Продолжить|Согласиться/i })
        if (await visible(consent, 4000)) await click(consent)
        await page.waitForURL((u) => u.href.startsWith(BASE) && !u.pathname.startsWith('/login'), { timeout: 30_000 })
        await restoreOverlay()
        await pause(1200)
        const menu = page.getByRole('navigation', { name: 'Разделы' })
        if (await visible(menu, 5000)) await spot(menu)
        await say('В панели он аналитик: разделов «Модель» и «Пользователи» в меню нет, а менять настройки не даст сервер.')
        await settle()
        const profile = page.locator('header button[aria-label^="Профиль"]')
        if (await visible(profile, 1500)) {
          await spot(profile)
          await pause(2200)
        }
        await unspot()
      } finally {
        // обратно под администратором, без показа формы
        await context.clearCookies()
        await loginApi(page.request)
        await goto('/')
      }
    },
  },
  {
    id: 'data',
    title: 'Данные и источники',
    run: async () => {
      await goto('/data')
      const upload = card('Загрузка снимка данных')
      await need(upload, 'страница «Данные»')
      await pause(600)
      await spot(upload)
      await say('Данные приходят двумя путями: снимок в формате кейса или поток изменений. В бою это выгрузка из биллинга по расписанию.')
      await settle()
      const live = page.getByRole('region', { name: 'Живой поток изменений' }).or(page.locator('[aria-label="Живой поток изменений"]'))
      if (await visible(live, 2000)) {
        await spot(live)
        await say('Для показа есть симулятор: генерирует тысячи клиентов и живой поток событий, и цифры в панели меняются на глазах.')
        await settle()
      }
    },
  },
  {
    id: 'demo-panel',
    title: 'Демо-пульт (Shift+D)',
    run: async () => {
      await goto('/')
      await need(page.getByTestId('kpi-high'), 'обзор')
      await page.locator('main').click({ position: { x: 5, y: 5 } })
      await page.keyboard.press('Shift+D')
      const panel = page.getByTestId('demo-panel')
      await need(panel, 'демо-пульт', 5000)
      await pause(600)
      await spot(panel)
      await say('Для показа экспертам в панель встроен демо-пульт, он открывается по Shift+D: сценарий по шагам, что сказать и куда смотреть.')
      await settle()
      const features = panel.getByRole('tab', { name: 'Что реализовано' })
      if (await visible(features, 1500)) {
        await unspot()
        await click(features)
        await spot(panel)
        await say('На вкладке «Что реализовано» — требования кейса и то, что сделано сверх них.')
        await settle()
        await unspot()
        await click(panel.getByRole('tab', { name: 'Сценарий' }))
      }
      await unspot()
      await page.keyboard.press('Escape')
      await pause(500)
    },
  },
  {
    id: 'grafana',
    title: 'Grafana: дашборд «Отток»',
    run: async () => {
      const auth = { Authorization: basic(GRAFANA_AUTH) }
      const health = await api.get(ipv4(`${GRAFANA}/api/health`), { timeout: 5000 }).catch(() => null)
      if (!health?.ok()) throw new SkipScene(`Grafana недоступна: ${GRAFANA}`)
      const found = await api
        .get(ipv4(`${GRAFANA}/api/search`), { params: { query: 'Отток', type: 'dash-db' }, headers: auth })
        .then((r) => (r.ok() ? (r.json() as Promise<{ uid: string; title: string }[]>) : []))
        .catch(() => [] as { uid: string; title: string }[])
      const dash = found.find((d) => d.title === 'Отток') ?? found[0]
      const uid = dash?.uid ?? 'churn-radar-churn'
      // авторизация в Grafana заголовком, чтобы не показывать её форму входа
      await page.route(`${GRAFANA}/**`, (route) => route.continue({ headers: { ...route.request().headers(), ...auth } }))
      await goto(`${GRAFANA}/d/${uid}?orgId=1&kiosk&from=now-6h&to=now`)
      if (/\/login/.test(page.url())) {
        await page.getByLabel(/Email or username|username/i).fill(GRAFANA_AUTH.username)
        await page.getByLabel(/Password/i).first().fill(GRAFANA_AUTH.password)
        await page.keyboard.press('Enter')
        await page.waitForURL((u) => !u.pathname.includes('/login'), { timeout: 15_000 })
        const skip = page.getByRole('button', { name: /Skip/i })
        if (await visible(skip, 2000)) await skip.click()
        await goto(`${GRAFANA}/d/${uid}?orgId=1&kiosk&from=now-6h&to=now`)
      }
      await pause(3500)
      await cursorTo(700, 330)
      await say('Те же цифры — в Grafana. Дашборд «Отток» строится из /metrics через Prometheus, на нём же настроены алерты.')
      await settle()
      await say('Отдельный дашборд «Платформа» показывает сами сервисы: задержки, ошибки, очередь событий в NATS.')
      await page.mouse.wheel(0, 500)
      await settle()
      await page.unroute(`${GRAFANA}/**`).catch(() => {})
    },
  },
  {
    id: 'metrics',
    title: '/metrics',
    run: async () => {
      const anon = await request.newContext().then((c) => c.get(ipv4(`${BASE}/metrics`), { headers: { Authorization: 'Bearer wrong' } })).catch(() => null)
      const res = await api.get(ipv4(`${BASE}/metrics`), { headers: { Authorization: `Bearer ${METRICS_TOKEN}` } }).catch(() => null)
      if (!res) throw new SkipScene('/metrics не отвечает')
      let lines: string[]
      if (res.ok()) {
        const text = await res.text()
        const pick = [
          'churn_customers_by_risk_level',
          'churn_expected_loss_eur',
          'churn_exposure_eur',
          'churn_services_at_risk',
          'churn_mrr_eur',
          'churn_arpu_eur',
          'churn_ltv_eur',
          'churn_customer_churn_rate',
          'churn_risk_score_avg',
          'churn_model_version',
        ]
        lines = text.split('\n').filter((l) => pick.some((p) => l.startsWith(p)))
        lines = lines.slice(0, 15)
      } else {
        lines = [`# HTTP ${res.status()}: проверьте METRICS_TOKEN`]
      }
      lines.push(`# без токена: HTTP ${anon?.status() ?? '—'}`)
      const cmd = `curl -s -H "Authorization: Bearer ••••••" ${BASE}/metrics | grep ^churn_`
      await goto(dataUrl(terminalSlide(cmd, lines, 'Те же числа, что на обзоре и на странице «Маркетинг»: это проверяют e2e-тесты.')))
      await pause(1500)
      await say('API отдаёт /metrics для Prometheus: клиенты по уровням риска, ожидаемая потеря, MRR, LTV, версия модели — те же числа, что в панели.')
      await settle()
      await say('Без токена ответ 401: бизнес-показатели наружу не утекают.')
      await settle()
    },
  },
  {
    id: 'architecture',
    title: 'Что под капотом',
    run: async () => {
      await goto(dataUrl(architectureSlide()))
      await pause(1500)
      await sayAndWait('Под капотом — микросервисы на FastAPI и Go за шлюзом Traefik. Вход, сессия и роль проверяются на шлюзе для каждого запроса.')
      await sayAndWait('Сервисы общаются через NATS JetStream, а событие пишется в outbox в одной транзакции с данными: если шина упала, пересчёт всё равно случится, и ровно один раз.')
      await sayAndWait('Для Kubernetes есть Helm-чарт: CI на каждый пул-реквест ставит его в kind. А хаос-скрипт роняет NATS, api и scoring — данные доходят, панель отвечает.')
      await hideSay()
      await overlay('chapter', '')
      await goto(dataUrl(endSlide()))
      await pause(3500)
    },
  },
]

// ---------- запуск ----------

async function main() {
  mkdirSync(OUT, { recursive: true })
  api = await request.newContext({ baseURL: ipv4(BASE), extraHTTPHeaders: { Origin: ORIGIN }, ignoreHTTPSErrors: true })
  await loginApi(api)

  const browser = await chromium.launch({ headless: HEADLESS })
  context = await browser.newContext({
    viewport: { width: 1440, height: 900 },
    deviceScaleFactor: 1,
    locale: 'ru-RU',
    timezoneId: 'Europe/Moscow',
    colorScheme: 'light',
    ignoreHTTPSErrors: true,
    recordVideo: { dir: resolve(OUT, 'raw'), size: { width: 1440, height: 900 } },
    ...(MAILPIT_AUTH ? { httpCredentials: { ...MAILPIT_AUTH, origin: new URL(MAILPIT).origin } } : {}),
  })
  await context.addInitScript(installOverlay)
  page = await context.newPage()
  t0 = Date.now()
  page.on('load', () => void restoreOverlay())

  let n = 0
  try {
    for (const scene of scenes) {
      if (ONLY.length && !ONLY.includes(scene.id) && scene.id !== 'login') continue
      n += 1
      const start = now()
      log(`▶ ${scene.id}: ${scene.title}`)
      await chapter(scene.title, n)
      try {
        await scene.run()
        await settle()
        report.push({ id: scene.id, title: scene.title, status: 'ok', at: (start / 1000).toFixed(1) })
      } catch (e) {
        const skipped = e instanceof SkipScene
        const note = e instanceof Error ? e.message.split('\n')[0] : String(e)
        log(`${skipped ? '⤼ пропуск' : '✗ ошибка'} ${scene.id}: ${note}`)
        report.push({ id: scene.id, title: scene.title, status: skipped ? 'skipped' : 'failed', note })
        // короткий обрывок без содержания главой не считаем
        if (now() - start < 6000) {
          chapters.pop()
          n -= 1
        }
        await page.keyboard.press('Escape').catch(() => {})
      } finally {
        await unspot()
        await hideSay()
        await pause(350)
      }
    }
  } finally {
    const duration = now()
    closeCue()
    const video = page.video()
    await context.close()
    await browser.close()

    log('возвращаю стенд в исходное состояние')
    for (const c of cleanups.reverse()) {
      try {
        await c.run()
        log(`  ✓ ${c.name}`)
      } catch (e) {
        log(`  ✗ ${c.name}: ${e instanceof Error ? e.message : e}`)
      }
    }
    await api.dispose()

    const timeline = { duration, cues, chapters }
    writeFileSync(resolve(OUT, 'timeline.json'), JSON.stringify(timeline, null, 2), 'utf-8')
    writeFileSync(resolve(OUT, 'report.json'), JSON.stringify(report, null, 2), 'utf-8')
    if (video) {
      const raw = resolve(OUT, 'raw.webm')
      await video.saveAs(raw)
      await video.delete().catch(() => {})
      if (env('SKIP_ENCODE') !== '1') encode(OUT, raw, timeline)
    }
    console.log('\nСцены:')
    for (const r of report) console.log(`  ${r.status === 'ok' ? '✓' : r.status === 'skipped' ? '⤼' : '✗'} ${r.title}${r.note ? ` — ${r.note}` : ''}`)
  }
}

main().catch((e) => {
  console.error(e)
  process.exit(1)
})

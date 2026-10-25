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
/** Множитель пауз между действиями (на время чтения субтитров не влияет): 0.7 — бодрый темп. */
const PACE = Number(env('PACE', '0.7')) || 0.7
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
  return Math.max(2800, 1200 + text.length * 50)
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
  sayUntil = Date.now() + readMs(text)
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
      if (r.top >= 70 && r.bottom <= free) return
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
  try {
    await locator.first().click({ timeout: 8000, force: opts.force })
  } catch (e) {
    // кнопку перекрыл соседний элемент (тесная шапка): нажимаем напрямую, иначе сдаёмся
    const ok = await locator.first().evaluate((el) => (el as HTMLElement).click()).then(() => true, () => false)
    if (!ok) throw e
    log('клик через JS: элемент перекрыт')
  }
  await pause(450)
}

async function type(locator: Locator, text: string, opts: { clear?: boolean; secret?: boolean } = {}) {
  await click(locator)
  if (opts.clear !== false) await locator.first().fill('')
  // длинные пароли печатаем быстрее: на видео всё равно точки
  await locator.first().pressSequentially(text, { delay: Math.round((opts.secret ? 35 : 85) * PACE) })
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
  // на слайдах курсор не нужен
  if (url.startsWith('data:')) overlayState.cursor = null
  await restoreOverlay()
  if (url.startsWith('data:')) await overlay('cursorHide')
  await pause(700)
}

/** Переход по пункту меню в шапке, если его нет — прямой адрес. */
async function nav(name: string, path: string) {
  const link = page.getByRole('navigation', { name: 'Разделы' }).getByRole('link', { name, exact: true })
  const admin = page.getByRole('button', { name: /^Администрирование/ })
  if (await visible(link, 2500)) {
    await click(link)
    await page.waitForURL((u) => u.pathname.startsWith(path), { timeout: 10_000 }).catch(() => {})
  } else if (await visible(admin, 1500)) {
    // служебные разделы спрятаны в выпадающее меню «Администрирование»
    await click(admin)
    const item = page.getByRole('menuitem', { name: new RegExp(`^${name}`) })
    if (await visible(item, 2500)) {
      await click(item)
      await page.waitForURL((u) => u.pathname.startsWith(path), { timeout: 10_000 }).catch(() => {})
    } else {
      await page.keyboard.press('Escape')
      await goto(path)
    }
  } else {
    await goto(path)
  }
  await pause(600)
}

const card = (title: string) =>
  page.locator('div.rounded-xl.border').filter({ has: page.getByText(title, { exact: true }) }).last()

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
  dialog().locator('section').filter({ has: page.locator('h3').filter({ hasText: new RegExp(`^${title}`, 'i') }) }).first()

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

/** Ждём, пока таблица клиентов покажет строки, а не скелетон. */
async function tableReady() {
  const rows = page.getByRole('table', { name: 'Клиенты под риском' }).locator('tbody tr[aria-label]')
  await rows.first().waitFor({ state: 'visible', timeout: 15_000 }).catch(() => {})
  await pause(500)
}

async function restoreAction(id: number, previous: Action) {
  if (previous === null) {
    await api.delete(`/api/v1/customers/${id}/action`, { headers: { Origin: ORIGIN } })
  } else {
    const data: Record<string, unknown> = { status: previous.status }
    if (previous.status === 'snoozed' && previous.until) data.snoozed_until = previous.until
    if (previous.note) data.note = previous.note
    await api.put(`/api/v1/customers/${id}/action`, { data, headers: { Origin: ORIGIN } })
  }
}

/** Отправка письма из открытого окна: true — ушло, false — сервер отказал (окно закрываем). */
async function sendLetter(mail: Locator): Promise<boolean> {
  await click(mail.getByRole('button', { name: 'Отправить' }))
  const gone = await mail.waitFor({ state: 'detached', timeout: 20_000 }).then(() => true, () => false)
  if (gone) return true
  const error = mail.getByRole('alert')
  log(`письмо не ушло: ${(await error.innerText().catch(() => '')).trim() || 'окно не закрылось'}`)
  const cancel = mail.getByRole('button', { name: 'Отмена' })
  if (await visible(cancel, 1000)) await click(cancel)
  return false
}

async function showInMailpit(email: string, subject: string) {
  let found: MailpitMessage | undefined
  for (let i = 0; i < 20 && !found; i++) {
    found = (await mailpitMessages()).find((m) => fresh(m) && m.To.some((t) => t.Address === email) && (!subject || m.Subject === subject))
    if (!found) await page.waitForTimeout(1000)
  }
  if (!found) {
    log('письмо в Mailpit не нашлось — ящик не показываю')
    return
  }
  const back = page.url()
  await goto(`${MAILPIT}/view/${found.ID}`)
  await pause(1500)
  await cursorTo(760, 430)
  await say('На стенде письма ловит Mailpit. Вот это письмо глазами клиента: тема, сумма, дата и адрес, куда придёт ответ.')
  await settle()
  await goto(back)
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
      await say('Без входа не открывается ни одна страница и ни один запрос к API: сессию проверяет шлюз перед каждым запросом.')
      const sso = page.getByRole('link', { name: /Войти через/ })
      if (await visible(sso, 1500)) {
        await settle()
        await spot(sso)
        await say('Войти можно по паролю или через корпоративный Authentik, по OIDC и SAML. К SSO вернёмся ближе к концу.')
      }
      await settle()
      await unspot()
      await type(login, ADMIN.username)
      await type(page.getByLabel('Пароль', { exact: true }), ADMIN.password, { secret: true })
      await click(page.getByRole('button', { name: 'Войти', exact: true }))
      await page.waitForURL((u) => !u.pathname.startsWith('/login'), { timeout: 20_000 })
      await restoreOverlay()
      await pause(800)
    },
  },
  {
    id: 'design',
    title: 'Обзор: радар и темы',
    run: async () => {
      if (new URL(page.url()).pathname !== '/') await goto('/')
      const radar = page.getByTestId('radar-backdrop')
      await need(page.getByTestId('kpi-high'), 'обзор')
      await pause(1200)
      const hero = page.getByRole('heading', { level: 1 }).first()
      if (await visible(radar, 1500)) {
        const box = await hero.boundingBox().catch(() => null)
        if (box) await cursorTo(Math.round(box.x + box.width / 2), Math.round(box.y + box.height + 40))
      }
      await say('Обзор открывается радаром: тёмная тема по умолчанию, чтобы панель спокойно висела на большом экране у команды.')
      await settle()
      const light = page.getByRole('button', { name: 'Включить светлую тему' })
      if (await visible(light, 1500)) {
        await unspot()
        await click(light)
        await say('Для работы днём есть светлая тема, выбор запоминается.')
        await pause(2600)
        await settle()
        await click(page.getByRole('button', { name: 'Включить тёмную тему' }))
        await pause(600)
      }
    },
  },
  {
    id: 'overview',
    title: 'Обзор: деньги под риском',
    run: async () => {
      if (new URL(page.url()).pathname !== '/') await goto('/')
      const high = page.getByTestId('kpi-high')
      await need(high, 'KPI на обзоре')
      await spot(high.locator('xpath=..'))
      await say('Сверху главное: сколько клиентов под высоким риском и сколько денег мы потеряем за 30 дней, если ничего не делать.')
      await settle()
      const loss = page.getByTestId('kpi-expected-loss')
      await spot(loss)
      await say('Ожидаемая потеря — не сумма всех счетов. Цена каждой услуги умножена на вероятность, что её не продлят, а то, что покрыто балансом, вычтено.')
      const info = loss.getByRole('button').first()
      if (await visible(info, 1000)) await move(info)
      await settle()
      await cursorTo(1250, 160)
    },
  },
  {
    id: 'contact-today',
    title: 'С кем связаться сегодня: письмо',
    run: async () => {
      if (new URL(page.url()).pathname !== '/') await goto('/')
      const today = page.getByTestId('contact-today')
      await need(today, 'блок «С кем связаться сегодня»')
      await spot(today)
      await say('«С кем связаться сегодня» — с этого маркетолог начинает день. Очередь отсортирована по деньгам, которые можно спасти.')
      await settle()
      const queue = await json<{ items: { id: number; email: string }[] }>(api.get('/api/v1/contact-today', { params: { limit: '5' } }))
      const target = queue.items[0]
      if (!target) throw new SkipScene('очередь пуста')
      const row = today.getByTestId('contact-today-row').filter({ has: page.getByRole('link', { name: target.email, exact: true }) }).first()
      const write = row.getByRole('button', { name: 'Написать', exact: true })
      await need(write, 'кнопка «Написать» в очереди')
      const previous = await json<{ action: Action }>(api.get(`/api/v1/customers/${target.id}/action`)).then((r) => r.action).catch(() => null)
      await unspot()
      await click(write)
      const mail = page.getByTestId('email-dialog')
      await need(mail, 'окно письма')
      await pause(900)
      await spot(mail)
      await say('Письмо собирается само: шаблон выбран по главной причине риска, сумма и дата клиента уже подставлены. Текст можно поправить.')
      await settle()
      const subject = (await mail.getByLabel('Тема').inputValue().catch(() => '')) || ''
      const previewBtn = mail.getByRole('button', { name: 'Как увидит клиент' })
      if (await visible(previewBtn, 1500)) {
        await unspot()
        await click(previewBtn)
        await spot(mail.getByTestId('letter-preview'))
        await say('Кнопка «Как увидит клиент» показывает письмо с вёрсткой, как в почтовом ящике.')
        await settle()
      }
      await unspot()
      onCleanup(`статус клиента #${target.id}`, () => restoreAction(target.id, previous))
      onCleanup('письмо клиенту в Mailpit', async () => {
        const ids = (await mailpitMessages()).filter((m) => fresh(m) && m.To.some((t) => t.Address === target.email)).map((m) => m.ID)
        await mailpitDelete(ids)
      })
      if (!(await sendLetter(mail))) return
      await spot(today)
      await say('Письмо ушло через SMTP: клиент получил статус «Письмо отправлено» и пропал из очереди, на неделю его не тронем.')
      await settle()
      await unspot()
      await showInMailpit(target.email, subject)
    },
  },
  {
    id: 'campaign',
    title: 'Рассылка по очереди',
    run: async () => {
      if (new URL(page.url()).pathname !== '/') await goto('/')
      const mass = page.getByTestId('contact-today-mass')
      await need(mass, 'кнопка рассылки')
      await click(mass)
      const campaign = page.getByTestId('campaign-dialog')
      await need(campaign, 'окно рассылки')
      await pause(1500)
      await spot(campaign.getByTestId('campaign-summary'))
      await say('Можно написать всей очереди сразу. Перед отправкой видно, сколько писем уйдёт и кого пропустим: кому уже писали на неделе, второе письмо не придёт.')
      await settle()
      const preview = campaign.getByTestId('letter-preview')
      if (await visible(preview, 1500)) {
        await spot(preview)
        await say('Каждому — своё письмо под его причину. Ниже пример для первого клиента.')
        await settle()
      }
      await unspot()
      await click(campaign.getByRole('button', { name: 'Отмена' }))
    },
  },
  {
    id: 'charts',
    title: 'Графики и причины',
    run: async () => {
      if (new URL(page.url()).pathname !== '/') await goto('/')
      const chart = card('Отток услуг: факт и прогноз')
      if (await visible(chart, 4000)) {
        await spot(chart)
        await say('На графике факт и прогноз вместе: сколько услуг уже ушло, сколько непродлений ждём и сколько услуг сейчас под риском.')
        const plot = chart.locator('svg.recharts-surface').first()
        const box = await plot.boundingBox().catch(() => null)
        if (box) {
          await cursorTo(Math.round(box.x + box.width * 0.6), Math.round(box.y + box.height * 0.5))
          await pause(900)
          await cursorTo(Math.round(box.x + box.width * 0.82), Math.round(box.y + box.height * 0.45))
        }
        await settle()
      }
      const hist = page.getByTestId('chart-score-histogram')
      if (await visible(hist, 2000)) {
        await spot(hist, page.getByTestId('chart-revenue-by-level'))
        await say('Ниже распределение оценок риска и выручка по уровням. Любой столбец кликабелен и открывает клиентов из этого диапазона.')
        await settle()
      }
      const byReason = page.getByTestId('chart-loss-by-reason')
      if (await visible(byReason, 1500)) {
        await spot(byReason, page.getByTestId('chart-metrics-history'))
        await say('Потеря по причинам и история главных цифр: видно, растёт риск или мы его сбиваем.')
        await settle()
      }
      const reasons = card('Главные причины риска')
      await need(reasons, 'главные причины риска')
      await spot(reasons)
      await say('Главные причины риска по всей базе. Нажимаю на причину — и сразу вижу всех клиентов с ней.')
      await settle()
      const link = page.locator('main a[data-reason]').first()
      await need(link, 'ссылка причины')
      await unspot()
      await click(link)
      await page.waitForURL(/reason=/, { timeout: 10_000 }).catch(() => {})
      await tableReady()
      const panel = page.getByTestId('reason-panel')
      if (await visible(panel, 5000)) {
        await spot(panel)
        await say('Список уже отфильтрован, а панель причины объясняет, что с этими клиентами делать, и даёт написать всем им сразу.')
        await settle()
      }
    },
  },
  {
    id: 'customers',
    title: 'Клиенты: фильтр, сортировка, поиск',
    run: async () => {
      if (!new URL(page.url()).pathname.startsWith('/customers')) await nav('Клиенты', '/customers')
      const chipReason = page.getByTestId('chip-reason')
      if (await visible(chipReason, 1000)) {
        await click(chipReason.getByRole('button'))
      }
      await tableReady()
      const table = page.getByRole('table', { name: 'Клиенты под риском' })
      await need(table, 'таблица клиентов')
      await spot(table.locator('xpath=ancestor::div[contains(@class,"rounded-xl")][1]'))
      await say('Здесь все клиенты: оценка риска, уровень и главная причина в одной строке.')
      await settle()
      const chip = page.getByRole('group', { name: 'Фильтр по уровню риска' }).getByRole('button', { name: /Высокий/ })
      if (await visible(chip)) {
        await unspot()
        await click(chip)
        await tableReady()
        await spot(page.getByRole('group', { name: 'Фильтр по уровню риска' }))
        await say('Фильтр по уровню оставляет тех, кем надо заняться в первую очередь.')
        await settle()
      }
      const balance = page.getByRole('columnheader', { name: /^Баланс/ })
      if (await visible(balance)) {
        await unspot()
        await click(balance.getByRole('button'))
        await spot(balance)
        await say('Сортировка по ID, email, балансу и пополнениям, в обе стороны. Всё сохраняется в адресе, ссылкой можно поделиться.')
        await pause(1400)
        await click(balance.getByRole('button'))
        await settle()
      }
      const search = page.getByLabel('Поиск по email или ID')
      if (await visible(search)) {
        await unspot()
        const first = await table.locator('tbody tr[aria-label]').first().getAttribute('aria-label').catch(() => null)
        const email = (first ?? '').replace(/^Клиент\s+/, '')
        const fragment = email.includes('@') ? email.split('@')[0]!.slice(-4) : '1'
        await say('Поиск — по email или ID, прямо по мере ввода.')
        await type(search, fragment)
        await pause(1500)
        await settle()
        const clear = page.getByRole('button', { name: 'Очистить поиск' })
        if (await visible(clear, 1000)) await click(clear)
      }
      const chartView = page.getByTestId('customers-view-chart')
      if (await visible(chartView, 1500)) {
        await unspot()
        await click(chartView)
        const scatter = page.getByTestId('risk-scatter')
        if (await visible(scatter, 6000)) {
          await pause(800)
          await spot(scatter)
          await say('А можно смотреть картой: риск по одной оси, потеря по другой. Самые опасные и дорогие клиенты — в правом верхнем углу.')
          await settle()
        }
      }
    },
  },
  {
    id: 'customer',
    title: 'Карточка клиента',
    run: async () => {
      const c = await pickCustomer()
      const dot = page.getByTestId('risk-scatter').locator('.recharts-scatter-symbol').first()
      if (new URL(page.url()).searchParams.get('view') === 'chart' && (await visible(dot, 1500))) {
        await unspot()
        await click(dot)
      } else {
        await goto(`/customers/${c.id}?level=high`)
      }
      await need(dialog(), 'карточка клиента')
      await pause(1200)
      const why = section('Почему')
      if (await visible(why)) {
        await spot(why)
        await say('Карточка объясняет оценку обычными словами: почему этот клиент в риске.')
        await settle()
      }
      const waterfall = dialog().getByTestId('chart-score-waterfall')
      if (await visible(waterfall, 2000)) {
        await spot(waterfall)
        await say('Водопад показывает, из чего сложилась оценка: какой признак сколько баллов добавил.')
        await settle()
      }
      const money = section('Деньги')
      if (await visible(money)) {
        await spot(money)
        await say('Деньги на кону: выручка в месяц, ожидаемая потеря и максимум, который можно потерять.')
        await settle()
      }
      const runway = dialog().getByTestId('chart-runway')
      const payments = dialog().getByTestId('chart-payments')
      if (await visible(runway, 1500)) {
        await spot(runway)
        await say('Баланс по дням: когда закончатся деньги и какие продления на это попадут.')
        await settle()
      } else if (await visible(payments, 1500)) {
        await spot(payments)
        await say('История платежей: как клиент пополнял баланс и как это менялось со временем.')
        await settle()
      }
      const todo = section('Что сделать')
      if (await visible(todo)) {
        await spot(todo)
        await say('И конкретное действие: что сделать с клиентом и насколько это снизит ожидаемую потерю.')
        await settle()
      }
      const work = page.getByTestId('customer-action')
      if (await visible(work)) {
        await spot(work)
        await say('Ниже работа с клиентом: статус, письмо, отметки и журнал — видно, кто и что уже сделал.')
        await settle()
      }
      await unspot()
      await page.keyboard.press('Escape')
      await pause(500)
    },
  },
  {
    id: 'report',
    title: 'Отчёт PDF',
    run: async () => {
      await goto('/')
      const link = page.getByRole('link', { name: 'Скачать отчёт' })
      await need(link, 'кнопка «Скачать отчёт»')
      await spot(link)
      await say('Для руководства — PDF-отчёт за неделю: цифры, прогноз и клиенты под риском. Одна кнопка, без выгрузок в Excel.')
      const download = page.waitForEvent('download', { timeout: 30_000 }).catch(() => null)
      await click(link)
      const file = await download
      if (file) {
        const path = resolve(OUT, file.suggestedFilename() || 'report.pdf')
        await file.saveAs(path).catch(() => {})
        log(`отчёт скачан: ${path}`)
        await settle()
        await say(`Файл ${file.suggestedFilename()} скачан. Его же можно получать на почту каждую неделю.`)
      }
      await settle()
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
        await cursorTo(1250, 160)
      }
      const retention = page.getByTestId('mkt-retention')
      if (await visible(retention, 1500)) {
        await spot(retention)
        await say('Главное — связь с риском: на сколько вырастет LTV базы, если удержать клиентов высокого риска.')
        await settle()
      }
      const charts = page.getByTestId('chart-mrr-segments')
      if (await visible(charts, 1500)) {
        await spot(charts, page.getByTestId('chart-ltv-segments'))
        await say('Графики раскладывают выручку и LTV по сегментам риска: видно, где сосредоточены деньги.')
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
      await say('Но сначала вес можно примерить в режиме «Что если»: пока не нажали «Применить», в базе ничего не меняется.')
      const slider = row.getByRole('slider').first()
      const preview = page.waitForResponse((r) => r.url().includes('/api/v1/model/settings/preview') && r.ok(), { timeout: 15_000 }).catch(() => null)
      if (await visible(slider, 1500)) {
        await scrollTo(slider)
        const b = await slider.boundingBox()
        if (b) {
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
      }
      if (!(await preview)) {
        const input = page.getByRole('textbox', { name: `Вес: ${w.title}` })
        await type(input, '0')
        await input.blur()
        await page.waitForResponse((r) => r.url().includes('/settings/preview') && r.ok(), { timeout: 15_000 }).catch(() => null)
      }
      await pause(1200)
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
      onCleanup(`вес «${w.title}» = ${original}`, async () => {
        const now = await json<Settings>(api.get('/api/v1/model/settings'))
        if (now.weights.find((x) => x.code === w.code)?.weight === original) return
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
      if (await visible(toast, 30_000)) await spot(toast, summary)
      else await spot(summary)
      await say('Через пару секунд — новые цифры: оценки пересчитаны, итог по всем клиентам и обзор обновились сами.')
      await settle()
      await pause(800)
      await unspot()
      const back = page.waitForResponse((r) => r.request().method() === 'PATCH' && r.url().includes('/api/v1/model/settings'), { timeout: 15_000 }).catch(() => null)
      await click(input)
      await page.keyboard.press('Control+A')
      await input.pressSequentially(shown(original), { delay: 120 })
      await page.keyboard.press('Tab')
      await back
      const history = page.getByTestId('settings-history')
      if (await visible(history, 3000)) await spot(history)
      await say('И возвращаю вес обратно. Каждая правка остаётся в истории: кто, когда и что поменял, и к любой версии можно откатиться.')
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
          ? `Клиенты высокого риска уходят ${liftText} чаще среднего, ROC AUC — ${aucText}: опасных клиентов модель ставит выше остальных.`
          : 'Lift показывает, во сколько раз чаще уходят клиенты высокого риска, а ROC AUC — насколько верно модель их упорядочивает.',
      )
      await settle()
      const chart = page.getByTestId('quality-lift-chart')
      if (await visible(chart, 1500)) {
        await spot(chart, page.getByTestId('quality-roc'))
        await say('Под цифрами графики lift и ROC-кривая, у каждого есть таблица с теми же данными.')
        await settle()
      }
      const brier = page.getByTestId('quality-brier')
      if (await visible(brier, 1500)) {
        await spot(brier, page.getByTestId('quality-reliability'))
        await say('Вероятности тоже честные: Brier score и кривая надёжности показывают, что «60 %» у нас действительно значит около 60 %.')
        await settle()
      }
      const baselines = page.getByTestId('quality-baselines')
      if (await visible(baselines, 1500)) {
        await spot(baselines)
        await say('А сравнение с простыми правилами и контрольной моделью показывает, где наша модель полезнее.')
        await settle()
      }
      const method = page.getByTestId('quality-method')
      if (await visible(method, 1500)) {
        await spot(method)
        await say('И честно про ограничения: данные синтетические, на реальной базе эти цифры нужно перемерить.')
        await settle()
      }
    },
  },
  {
    id: 'playbooks',
    title: 'Сценарии удержания',
    run: async () => {
      await nav('Сценарии', '/playbooks')
      const presets = page.getByTestId('playbook-presets')
      await need(presets, 'страница «Сценарии»')
      await pause(800)
      await spot(presets)
      await say('Сценарии удержания — правила «если, то»: например, за три дня до списания написать тем, кому не хватит денег. Есть готовые заготовки.')
      await settle()
      const create = page.getByRole('button', { name: 'Новый сценарий' }).first()
      if (await visible(create, 2000)) {
        await unspot()
        await click(create)
        const d = page.getByRole('dialog', { name: 'Новый сценарий' })
        await need(d, 'окно сценария')
        await pause(600)
        const levels = d.getByRole('group', { name: 'Уровень риска' })
        if (await visible(levels, 1500)) await click(levels.getByRole('button', { name: 'Высокий' }))
        await cursorTo(1100, 300)
        const preview = d.getByTestId('playbook-preview')
        await d.getByTestId('preview-matched').waitFor({ state: 'visible', timeout: 8000 }).catch(() => {})
        if (await visible(preview, 1000)) {
          await pause(600)
          await spot(preview)
        }
        await say('Пока сценарий не в боевом режиме, он только симулирует: видно, сколько клиентов попадёт под правило и сколько уйдёт в контрольную группу, чтобы честно измерить эффект.')
        await settle()
        await unspot()
        const cancel = d.getByRole('button', { name: 'Отмена' })
        if (await visible(cancel, 1000)) await click(cancel)
        else await page.keyboard.press('Escape')
        await pause(500)
      }
    },
  },
  {
    id: 'notifications',
    title: 'Уведомления',
    run: async () => {
      await goto('/notifications')
      await need(page.getByRole('heading', { name: 'Уведомления', level: 1 }), 'страница «Уведомления»')
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
        await say('Каналы — панель, почта или Telegram-бот. Есть тихие часы и ежедневная сводка.')
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
        await say('Колокольчик в шапке — личная лента событий. Тестовое уведомление уже здесь, а каждая запись ведёт к нужным клиентам.')
        await settle()
        await unspot()
        await page.keyboard.press('Escape')
        await pause(500)
      }
    },
  },
  {
    id: 'data',
    title: 'Источники данных',
    run: async () => {
      await nav('Данные', '/data')
      const sources = page.getByTestId('sources')
      await need(sources, 'раздел «Источники»')
      await pause(1000)
      await spot(sources)
      await say('Данные приходят из источников: биллинг забираем по расписанию, а события можно присылать и самим, через API.')
      await settle()
      const connector = page.locator('[data-testid^="connector-"]').filter({ has: page.getByTestId('connector-status') }).first()
      if (await visible(connector, 2000)) {
        await spot(connector)
        await say('У каждого коннектора видно статус, сколько записей пришло и историю запусков. Если что-то сломалось, это видно сразу.')
        await settle()
      }
      const metrica = page.getByTestId('metrica-panel')
      if (await visible(metrica, 1500)) {
        await spot(metrica)
        await say('А Яндекс Метрика добавляет поведение: кто смотрел страницу удаления или давно не заходил в панель хостинга.')
        await settle()
      }
    },
  },
  {
    id: 'integrations',
    title: 'Интеграции и журнал действий',
    run: async () => {
      await nav('Интеграции', '/integrations')
      await need(page.getByRole('heading', { name: 'Интеграции', level: 1 }), 'страница «Интеграции»')
      await pause(800)
      const curl = page.getByTestId('curl-example')
      const main = page.locator('main')
      await spot((await visible(curl, 1500)) ? curl : main)
      await say('Для CRM и своих скриптов есть API-токены с ограниченными правами. Токен показывается один раз, а пример запроса — прямо здесь.')
      await settle()
      await unspot()
      await nav('Журнал действий', '/audit')
      const row = page.getByTestId('audit-row')
      await need(row, 'журнал действий')
      await pause(800)
      await spot(row.first().locator('xpath=ancestor::div[contains(@class,"rounded-xl")][1]'))
      await say('А журнал действий помнит всё: кто менял модель, кому писали, кто выпускал токены. Его можно искать и выгрузить в CSV.')
      await settle()
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
        await say('Ролей три: аналитик только смотрит, маркетолог работает с клиентами и письмами, администратор настраивает модель и данные. Запреты держит сервер, а не интерфейс.')
        await settle()
        await unspot()
        await click(d.getByRole('button', { name: 'Отмена' }))
      }
    },
  },
  {
    id: 'sso',
    title: 'Вход через Authentik (SSO)',
    run: async () => {
      const providers = await json<{ oidc?: { enabled: boolean }; saml?: { enabled: boolean } }>(api.get('/api/v1/auth/providers')).catch(() => null)
      const method = providers?.oidc?.enabled ? 'OIDC' : providers?.saml?.enabled ? 'SAML' : null
      if (!method) throw new SkipScene('SSO-провайдеры выключены')
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
        const link = page.getByRole('link', { name: new RegExp(`Authentik \\(${method}\\)`) })
        await need(link, `кнопка входа через ${method}`)
        await spot(page.getByRole('link', { name: /Войти через/ }))
        await say('Вход через корпоративный Authentik, по OIDC или SAML. Роль приходит из группы в каталоге, доступ сотрудника настраивают в одном месте.')
        await settle()
        await unspot()
        await click(link)
        await page.waitForURL(/\/if\/flow\//, { timeout: 30_000 }).catch(() => {})
        await restoreOverlay()
        await say(`Маркетолог входит своей корпоративной учёткой ${SSO_USER}.`)
        const loginBox = page.getByRole('textbox', { name: /Имя пользователя|Username|Email/i })
        const inPanel = () => page.url().startsWith(BASE) && !new URL(page.url()).pathname.startsWith('/login')
        // если Authentik уже помнит сессию, он сразу вернёт в панель — форму тогда не показываем
        if (!inPanel() && (await visible(loginBox, 20_000))) {
          await type(loginBox, SSO_USER)
          await page.keyboard.press('Enter')
          const pass = page.getByRole('textbox', { name: /Пароль|Password/i })
          await need(pass, 'поле пароля Authentik', 20_000)
          await type(pass, SSO_PASSWORD, { secret: true })
          await page.keyboard.press('Enter')
        }
        const consent = page.getByRole('button', { name: /Continue|Продолжить|Согласиться/i })
        if (!inPanel() && (await visible(consent, 4000))) await click(consent)
        await page.waitForURL((u) => u.href.startsWith(BASE) && !u.pathname.startsWith('/login'), { timeout: 30_000 })
        await restoreOverlay()
        await pause(1500)
        const menu = page.getByRole('navigation', { name: 'Разделы' })
        if (await visible(menu, 5000)) await spot(menu)
        await say('Он в панели с ролью из Authentik: разделов «Модель», «Данные» и «Пользователи» в меню нет, а менять настройки не даст сервер.')
        await settle()
        await unspot()
      } finally {
        // обратно под администратором, без показа формы
        await context.clearCookies()
        await page.request.post(`${BASE}/api/v1/auth/login`, { data: ADMIN, headers: { Origin: ORIGIN } })
        await goto('/')
      }
    },
  },
  {
    id: 'demo-panel',
    title: 'Демо-пульт (Shift+D)',
    run: async () => {
      if (new URL(page.url()).pathname !== '/') await goto('/')
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
      const health = await api.get(ipv4(`${GRAFANA}/api/health`), { timeout: 8000 }).catch(() => null)
      if (!health?.ok()) throw new SkipScene(`Grafana недоступна: ${GRAFANA}`)
      const found = await api
        .get(ipv4(`${GRAFANA}/api/search`), { params: { query: 'Отток', type: 'dash-db' }, headers: auth })
        .then((r) => (r.ok() ? (r.json() as Promise<{ uid: string; title: string }[]>) : []))
        .catch(() => [] as { uid: string; title: string }[])
      const dash = found.find((d) => d.title === 'Отток') ?? found[0]
      const uid = dash?.uid ?? 'churn-radar-churn'
      // авторизация в Grafana заголовком, чтобы не показывать её форму входа
      await page.route(`${GRAFANA}/**`, (route) => route.continue({ headers: { ...route.request().headers(), ...auth } }))
      const url = `${GRAFANA}/d/${uid}?orgId=1&kiosk&from=now-6h&to=now`
      await goto(url)
      if (/\/login/.test(page.url())) {
        await page.getByLabel(/Email or username|username/i).fill(GRAFANA_AUTH.username)
        await page.getByLabel(/Password/i).first().fill(GRAFANA_AUTH.password)
        await page.keyboard.press('Enter')
        await page.waitForURL((u) => !u.pathname.includes('/login'), { timeout: 15_000 })
        const skip = page.getByRole('button', { name: /Skip/i })
        if (await visible(skip, 2000)) await skip.click()
        await goto(url)
      }
      // ждём, пока панели дорисуются, а не крутится «Loading»
      await page.locator('.react-grid-item').first().waitFor({ state: 'visible', timeout: 20_000 }).catch(() => {})
      await page.waitForLoadState('networkidle', { timeout: 25_000 }).catch(() => {})
      await pause(2500)
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
      const res = await api.get(ipv4(`${BASE}/metrics`), { headers: { Authorization: `Bearer ${METRICS_TOKEN}` } }).catch(() => null)
      if (!res?.ok()) throw new SkipScene(`/metrics с токеном: ${res?.status() ?? 'нет ответа'} (задайте METRICS_TOKEN)`)
      const anon = await request.newContext().then((c) => c.get(ipv4(`${BASE}/metrics`), { headers: { Authorization: 'Bearer wrong' } })).catch(() => null)
      const text = await res.text()
      const pick = ['churn_customers_by_risk_level', 'churn_expected_loss_eur', 'churn_exposure_eur', 'churn_mrr_eur', 'churn_arpu_eur', 'churn_ltv_eur', 'churn_risk_score_avg', 'churn_model_version']
      const lines = text.split('\n').filter((l) => pick.some((p) => l.startsWith(p))).slice(0, 14)
      lines.push(`# без токена: HTTP ${anon?.status() ?? '—'}`)
      const cmd = `curl -s -H "Authorization: Bearer ••••••" ${BASE}/metrics | grep ^churn_`
      await goto(dataUrl(terminalSlide(cmd, lines, 'Те же числа, что на обзоре и на странице «Маркетинг»: это проверяют e2e-тесты.')))
      await pause(1500)
      await say('API отдаёт /metrics для Prometheus: клиенты по уровням риска, ожидаемая потеря, MRR, LTV — те же числа, что в панели. Без токена ответ 401.')
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
    colorScheme: 'dark',
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
    const raw = resolve(OUT, 'raw.webm')
    if (video) await video.saveAs(raw).catch((e) => log(`видео не сохранилось: ${e}`))
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

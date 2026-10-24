import type { ModelSettings, SimulatorCommand, SimulatorState } from '@/api/types'
import type { Target } from './spotlight'

export const CASE_REFERENCE_TIME = '2026-10-19T15:00:00Z'
export const CASE_DATA_URL = '/demo/case-data.json'
export const LIVE_ACCOUNTS = 5000
export const LIVE_SEED = 42
export const LIVE_INTERVAL_MS = 1500
export const DEMO_FEATURE = 'prolong_wait'

export const GRAFANA_URL = 'http://localhost:3000'
export const PROMETHEUS_URL = 'http://localhost:9090'
export const SWAGGER_URL = '/api/v1/docs'
export const REPO_URL = 'https://github.com/dimon0804/ChurnRadar'

export interface TopCustomer {
  id: number
  email: string
  nextAction: string | null
}

/** Всё, что шагам нужно от приложения. В тестах подменяется заглушкой. */
export interface DemoApi {
  topHighRisk: () => Promise<TopCustomer | null>
  settings: () => Promise<ModelSettings>
  patchWeight: (code: string, weight: number) => Promise<ModelSettings>
  resetSettings: () => Promise<unknown>
  simulator: (command: SimulatorCommand) => Promise<SimulatorState>
  restoreCaseData: () => Promise<{ accounts: number }>
  logout: () => Promise<unknown>
}

export interface DemoContext {
  isAdmin: boolean
  api: DemoApi
  navigate: (to: string) => void
  spotlight: (targets: Target[]) => void
  waitFor: (target: Target) => Promise<unknown>
  wait: (ms: number) => Promise<void>
  redirect: (url: string) => void
}

/** Строка, которую пульт покажет после действия. */
export type DemoResult = string | void

export interface DemoAction {
  id: string
  label: string
  adminOnly?: boolean
  run: (ctx: DemoContext) => Promise<DemoResult>
}

export interface DemoLink {
  label: string
  href: string
  hint: string
}

export type Readout = 'summary' | 'simulator'

export interface DemoStep {
  id: string
  title: string
  /** Что сказать эксперту: одна-две фразы. */
  say: string
  /** Куда смотреть на экране. */
  show: string
  adminOnly?: boolean
  readout?: Readout
  links?: DemoLink[]
  run?: (ctx: DemoContext) => Promise<DemoResult>
  actions?: DemoAction[]
}

export const ADMIN_ONLY_HINT = 'Нужна роль администратора. Аналитик может смотреть панель, но не менять модель и данные.'

const KPI_TARGETS: Target[] = [{ selector: '[data-testid="kpi-high"]' }, { selector: '[data-testid="kpi-expected-loss"]' }]
const CHART_TARGET: Target = { selector: 'h2', text: 'Отток услуг: факт и прогноз', closest: '.rounded-xl' }
const FILTER_TARGET: Target = { selector: '[aria-label="Фильтр по уровню риска"]' }
const TABLE_TARGET: Target = { selector: 'table[aria-label="Клиенты под риском"]', closest: '.rounded-xl' }
const LIVE_TARGET: Target = { selector: 'header [role="status"]' }
const PROFILE_TARGET: Target = { selector: 'header button[aria-label^="Профиль"]' }

export const sortHeader = (label: string): Target => ({ selector: 'thead th', text: label })
export const sheetSection = (title: string): Target => ({ selector: '[role="dialog"] section h3', text: title, closest: 'section' })
export const weightRow = (title: string): Target => ({ selector: 'li p', text: title, closest: 'li' })

/** Новый вес, заметно отличающийся от текущего, чтобы оценки точно сдвинулись. */
export function demoWeight(current: number, min: number, max: number): number {
  const next = current >= (min + max) / 2 ? current - (max - min) * 0.65 : current + (max - min) * 0.5
  return Math.round(Math.min(max, Math.max(min, next)))
}

function formatWeight(value: number): string {
  return Number.isInteger(value) ? String(value) : value.toFixed(1).replace('.', ',')
}

const showSort = (sort: string, label: string, header: string): DemoAction => ({
  id: `sort-${sort}`,
  label,
  run: async (ctx) => {
    ctx.navigate(sort === 'risk_score' ? '/customers?level=high' : `/customers?sort=${sort}&order=desc`)
    ctx.spotlight([sortHeader(header)])
  },
})

export const restoreCaseAction: DemoAction = {
  id: 'restore-case',
  label: 'Вернуть данные кейса',
  adminOnly: true,
  run: async (ctx) => {
    await ctx.api.simulator({ running: false })
    const { accounts } = await ctx.api.restoreCaseData()
    ctx.navigate('/')
    ctx.spotlight(KPI_TARGETS)
    return `Поток остановлен, загружены данные кейса: ${accounts} аккаунтов на 19 октября 2026, 15:00 UTC.`
  },
}

export const stopStreamAction: DemoAction = {
  id: 'stop-stream',
  label: 'Остановить поток',
  adminOnly: true,
  run: async (ctx) => {
    const state = await ctx.api.simulator({ running: false })
    return `Поток остановлен. За показ пришло событий: ${state.events_emitted}.`
  },
}

export const resetSettingsAction: DemoAction = {
  id: 'reset-settings',
  label: 'Сбросить коэффициенты',
  adminOnly: true,
  run: async (ctx) => {
    await ctx.api.resetSettings()
    return 'Коэффициенты вернулись к значениям по умолчанию, оценки пересчитываются.'
  },
}

export const DEMO_STEPS: DemoStep[] = [
  {
    id: 'overview',
    title: 'Обзор: кто уходит и сколько это стоит',
    say:
      'Маркетолог открывает панель и сразу видит, сколько клиентов под высоким риском и сколько денег мы потеряем за 30 дней, если ничего не делать. Это не сумма всех счетов: цена каждой услуги умножена на вероятность, что её не продлят, а то, что уже покрыто балансом, вычтено.',
    show: 'Две первые карточки сверху, у каждой есть пояснение по значку «i».',
    readout: 'summary',
    run: async (ctx) => {
      ctx.navigate('/')
      ctx.spotlight(KPI_TARGETS)
    },
  },
  {
    id: 'chart',
    title: 'Факт и прогноз на одном графике',
    say:
      'Слева факт: сколько услуг уже удалено по дням. Справа прогноз: сколько непродлений ждём в горизонте модели и на какую сумму, так видно, когда ударит отток.',
    show: 'График «Отток услуг: факт и прогноз». Наведите на график, чтобы увидеть цифры за день.',
    run: async (ctx) => {
      ctx.navigate('/')
      ctx.spotlight([CHART_TARGET])
    },
  },
  {
    id: 'customers',
    title: 'Таблица клиентов: фильтр и сортировка',
    say:
      'Здесь рейтинг клиентов по риску: score, уровень и главная причина в одной строке. Фильтр оставляет только высокий риск, а сортировать можно по ID, email, балансу и пополнениям.',
    show: 'Фильтр «Высокий» уже включён. Кнопками ниже переключите сортировку.',
    run: async (ctx) => {
      ctx.navigate('/customers?level=high')
      ctx.spotlight([FILTER_TARGET, sortHeader('Риск')])
    },
    actions: [
      showSort('risk_score', 'По риску', 'Риск'),
      showSort('balance', 'По балансу', 'Баланс'),
      showSort('total_replenished', 'По пополнениям', 'Пополнено всего'),
    ],
  },
  {
    id: 'customer',
    title: 'Карточка клиента высокого риска',
    say:
      'Открываем самого рискованного клиента. Видно, какие признаки сколько добавили к оценке, сколько денег на кону и что конкретно сделать маркетологу.',
    show: 'Блоки «Почему» и «Что сделать», ниже — вклад признаков и деньги.',
    run: async (ctx) => {
      const top = await ctx.api.topHighRisk()
      if (!top) {
        ctx.navigate('/customers')
        ctx.spotlight([TABLE_TARGET])
        return 'Сейчас нет клиентов высокого риска. Откройте любого из таблицы.'
      }
      ctx.navigate(`/customers/${top.id}?level=high`)
      ctx.spotlight([sheetSection('Почему'), sheetSection('Что сделать')])
      return top.nextAction ? `Главное действие: ${top.nextAction}` : `Клиент #${top.id}, ${top.email}`
    },
  },
  {
    id: 'model',
    title: 'Меняем коэффициент, оценки пересчитываются сами',
    say:
      'Веса признаков настраиваются без программиста и сохраняются в базе, перезапуск их не сбрасывает. Меняю вес «Ждёт оплаты», и через несколько секунд цифры ниже обновятся сами, страницу никто не перезагружает.',
    show: 'Строку признака на странице модели и сводку в этой карточке.',
    adminOnly: true,
    readout: 'summary',
    run: async (ctx) => {
      const settings = await ctx.api.settings()
      const weight = settings.weights.find((w) => w.code === DEMO_FEATURE) ?? settings.weights.find((w) => w.enabled)
      if (!weight) return 'В модели нет признаков, менять нечего.'
      ctx.navigate('/model')
      const row = weightRow(weight.title)
      await ctx.waitFor(row)
      ctx.spotlight([row])
      await ctx.wait(1200)
      const next = demoWeight(weight.weight, weight.min, weight.max)
      await ctx.api.patchWeight(weight.code, next)
      return `«${weight.title}»: вес ${formatWeight(weight.weight)} → ${formatWeight(next)}. Следите за сводкой ниже.`
    },
    actions: [resetSettingsAction],
  },
  {
    id: 'live',
    title: 'Живые данные: большой набор и поток изменений',
    say:
      'Загружаем 5 000 синтетических клиентов и включаем поток: балансы, продления и статусы меняются раз в полторы секунды. Первый полный пересчёт занимает около 20 секунд, дальше цифры и строки таблицы меняются на глазах.',
    show: 'Карточки сверху и строки таблицы: изменённые клиенты подсвечиваются.',
    adminOnly: true,
    readout: 'simulator',
    run: async (ctx) => {
      ctx.navigate('/')
      ctx.spotlight([...KPI_TARGETS, LIVE_TARGET])
      await ctx.api.simulator({ generate: { accounts: LIVE_ACCOUNTS, seed: LIVE_SEED } })
      await ctx.api.simulator({ running: true, interval_ms: LIVE_INTERVAL_MS })
      return 'Набор на 5 000 клиентов загружен, поток включён. Цифры обновятся, когда закончится первый пересчёт.'
    },
    actions: [stopStreamAction, restoreCaseAction],
  },
  {
    id: 'ops',
    title: 'Метрики, дашборды и API',
    say:
      'Каждый сервис отдаёт метрики в Prometheus, в Grafana готовые дашборды и алерты. Весь API описан в OpenAPI, его можно потрогать в Swagger.',
    show: 'Откройте ссылки ниже в новой вкладке. /metrics закрыт токеном, его читает только сборщик.',
    links: [
      { label: 'Grafana', href: GRAFANA_URL, hint: 'дашборды и алерты, вход admin / admin' },
      { label: 'Prometheus', href: `${PROMETHEUS_URL}/targets`, hint: 'кого и как часто опрашиваем' },
      { label: 'Swagger', href: SWAGGER_URL, hint: 'весь API, под текущим входом' },
    ],
  },
  {
    id: 'roles',
    title: 'Безопасность и роли',
    say:
      'Без входа не открывается ни страница, ни API. У аналитика доступ только на чтение: менять модель и загружать данные ему нельзя, и запрет держит сервер, а не только интерфейс.',
    show: 'Войдите как analyst / analyst: разделов «Модель» и «Данные» в меню нет, а прямая ссылка покажет отказ.',
    run: async (ctx) => {
      ctx.spotlight([PROFILE_TARGET])
    },
    actions: [
      {
        id: 'login-analyst',
        label: 'Выйти и войти аналитиком',
        run: async (ctx) => {
          await ctx.api.logout()
          ctx.redirect(`/login?next=${encodeURIComponent('/model?demo=1')}`)
        },
      },
    ],
  },
]

export function isLocked(item: { adminOnly?: boolean }, isAdmin: boolean): boolean {
  return Boolean(item.adminOnly) && !isAdmin
}

export function stepIndex(id: string | null | undefined): number {
  return DEMO_STEPS.findIndex((s) => s.id === id)
}

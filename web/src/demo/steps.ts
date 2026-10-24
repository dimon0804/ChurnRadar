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
  /** Нажимает на элемент, если он уже есть на странице. Возвращает, нашёлся ли он. */
  click: (target: Target) => Promise<boolean>
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
const ALERTS_TARGET: Target = { selector: 'header button[aria-label^="Уведомления"]' }
export const MARKETING_TARGETS: Target[] = [
  { selector: '[data-testid="mkt-ltv"]' },
  { selector: '[data-testid="mkt-ltv-cac"]' },
  { selector: '[data-testid="mkt-retention"]' },
]
const CONTACT_TODAY_TARGET: Target = { selector: '[data-testid="contact-today"]' }
const CONTACT_TODAY_MASS_TARGET: Target = { selector: '[data-testid="contact-today-mass"]' }
export const PLAYBOOKS_TARGET: Target = { selector: '[data-testid="playbooks-list"]' }
export const PLAYBOOK_EFFECT_TARGET: Target = { selector: '[data-testid="playbook-effect"]' }
const QUALITY_LIFT_TARGET: Target = { selector: '[data-testid="quality-lift"]' }
const QUALITY_AUC_TARGET: Target = { selector: '[data-testid="quality-auc"]' }
export const SOURCES_TARGET: Target = { selector: '[data-testid="sources"]' }
export const WHAT_IF_SWITCH: Target = { selector: 'label:has(button[role="switch"])', text: 'Что если' }
/** Тот же переключатель, но только выключенный: повторный запуск шага не выключит режим обратно. */
export const WHAT_IF_OFF: Target = { selector: 'label:has(button[role="switch"][aria-checked="false"])', text: 'Что если' }
export const WHAT_IF_CANCEL: Target = { selector: '[data-testid="what-if"] button', text: 'Отменить' }

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

const REASON_PANEL_TARGET: Target = { selector: '[data-testid="reason-panel"]' }
const REASON_CHIP_TARGET: Target = { selector: '[data-testid="chip-reason"]' }
const SCATTER_TARGET: Target = { selector: '[data-testid="risk-scatter"]', closest: '.rounded-xl' }
const HISTOGRAM_TARGET: Target = { selector: '[data-testid="chart-score-histogram"]' }
const LOSS_BY_REASON_TARGET: Target = { selector: '[data-testid="chart-loss-by-reason"]' }

/** Клик по причине: список клиентов с ней и панель причины с рассылкой всем. */
const showReasonAction: DemoAction = {
  id: 'show-reason',
  label: 'Клиенты с причиной',
  run: async (ctx) => {
    ctx.navigate(`/customers?reason=${DEMO_FEATURE}`)
    await ctx.waitFor(REASON_PANEL_TARGET)
    ctx.spotlight([REASON_CHIP_TARGET, REASON_PANEL_TARGET])
    return 'Так же открывается клик по причине на обзоре, в таблице, в карточке и на маркетинге.'
  },
}

const showScatterAction: DemoAction = {
  id: 'show-scatter',
  label: 'Риск × потеря',
  run: async (ctx) => {
    ctx.navigate('/customers?view=chart')
    await ctx.waitFor(SCATTER_TARGET)
    ctx.spotlight([SCATTER_TARGET])
    return 'Точка — клиент, размер — выручка в месяц. Нажмите на точку, откроется карточка.'
  },
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

export const applyWeightAction: DemoAction = {
  id: 'apply-weight',
  label: 'Сменить вес и пересчитать',
  adminOnly: true,
  run: async (ctx) => {
    const settings = await ctx.api.settings()
    const weight = settings.weights.find((w) => w.code === DEMO_FEATURE) ?? settings.weights.find((w) => w.enabled)
    if (!weight) return 'В модели нет признаков, менять нечего.'
    ctx.navigate('/model')
    const row = weightRow(weight.title)
    await ctx.waitFor(row)
    // черновик «Что если» закрываем, иначе страница покажет его, а не сохранённый вес
    await ctx.click(WHAT_IF_CANCEL)
    ctx.spotlight([row])
    const next = demoWeight(weight.weight, weight.min, weight.max)
    await ctx.api.patchWeight(weight.code, next)
    return `«${weight.title}»: вес ${formatWeight(weight.weight)} → ${formatWeight(next)} сохранён. Следите за сводкой ниже.`
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
    id: 'contact-today',
    title: 'С кем связаться сегодня',
    say:
      'С этого маркетолог начинает день: список, кому написать, отсортирован по деньгам, которые можно спасти, сверху тот, на ком мы потеряем больше всего. Нажимаем «Написать», и письмо уже готово под причину риска, с датой и суммой клиента, а кнопкой «Написать всем» уходит рассылка по всей очереди, причём тем, кому писали на этой неделе, второй раз письмо не придёт.',
    show: 'Блок «С кем связаться сегодня» на обзоре: у каждого клиента кнопка «Написать», сверху «Написать всем».',
    run: async (ctx) => {
      ctx.navigate('/')
      await ctx.waitFor(CONTACT_TODAY_TARGET)
      ctx.spotlight([CONTACT_TODAY_TARGET, CONTACT_TODAY_MASS_TARGET])
    },
  },
  {
    id: 'chart',
    title: 'Факт и прогноз на одном графике',
    say:
      'На графике три линии: сколько услуг уже удалено по дням, сколько непродлений ждём впереди и сколько услуг прямо сейчас под риском. В данных кейса удалений за прошлые дни нет, поэтому слева пусто, и подпись на самом графике это объясняет.',
    show: 'График «Отток услуг: факт и прогноз». Наведите курсор, чтобы увидеть цифры за день, линия «Под риском» идёт по правой шкале. Ниже ещё графики: распределение score (столбец кликабелен), деньги по причинам, выручка по уровням и как менялись цифры.',
    run: async (ctx) => {
      ctx.navigate('/')
      ctx.spotlight([CHART_TARGET])
    },
    actions: [
      {
        id: 'show-charts',
        label: 'Графики ниже',
        run: async (ctx) => {
          ctx.navigate('/')
          await ctx.waitFor(HISTOGRAM_TARGET)
          ctx.spotlight([HISTOGRAM_TARGET, LOSS_BY_REASON_TARGET])
          return 'Клик по столбцу открывает клиентов этого диапазона, клик по причине — клиентов с ней.'
        },
      },
    ],
  },
  {
    id: 'customers',
    title: 'Таблица клиентов: фильтр и сортировка',
    say:
      'Здесь рейтинг клиентов по риску: score, уровень и главная причина в одной строке, и причина кликабельна, нажатие оставит только клиентов с ней. Фильтр оставляет только высокий риск, сортировать можно по ID, email, балансу и пополнениям, а переключатель «График» показывает тех же клиентов точками: риск против денег.',
    show: 'Фильтр «Высокий» уже включён. Кнопками ниже переключите сортировку, откройте клиентов с причиной или график.',
    run: async (ctx) => {
      ctx.navigate('/customers?level=high')
      ctx.spotlight([FILTER_TARGET, sortHeader('Риск')])
    },
    actions: [
      showSort('risk_score', 'По риску', 'Риск'),
      showSort('balance', 'По балансу', 'Баланс'),
      showSort('total_replenished', 'По пополнениям', 'Пополнено всего'),
      showReasonAction,
      showScatterAction,
    ],
  },
  {
    id: 'customer',
    title: 'Карточка клиента высокого риска',
    say:
      'Открываем самого рискованного клиента: видно, какие признаки сколько добавили к оценке, сколько денег на кону и что конкретно сделать. Тут же блок «Связь с клиентом»: кнопка «Написать письмо» с готовым текстом, и после отправки клиент пропадёт из списка на обзоре.',
    show: 'Блоки «Почему» и «Что сделать», ниже водопад вклада причин в score, деньги, график «На сколько хватит баланса» и платежи по месяцам. Причина в «Почему» кликабельна.',
    run: async (ctx) => {
      const top = await ctx.api.topHighRisk()
      if (!top) {
        ctx.navigate('/customers')
        ctx.spotlight([TABLE_TARGET])
        return 'Сейчас нет клиентов высокого риска. Откройте любого из таблицы.'
      }
      ctx.navigate(`/customers/${top.id}?level=high`)
      ctx.spotlight([sheetSection('Почему'), sheetSection('Что сделать'), sheetSection('Работа с клиентом')])
      return top.nextAction ? `Главное действие: ${top.nextAction}` : `Клиент #${top.id}, ${top.email}`
    },
  },
  {
    id: 'playbooks',
    title: 'Сценарии: удержание на автопилоте и честный эффект',
    say:
      'Рутину маркетолог отдаёт сценариям: «если риск стал высоким и давно не писали — письмо», и после каждого пересчёта оно срабатывает само, с лимитом писем в день и сначала в режиме симуляции. Пятую часть клиентов сценарий не трогает, это контрольная группа, и карточка эффекта сравнивает их с остальными, так что на вопрос «а письма вообще помогают?» мы отвечаем цифрой с интервалом, а при малой выборке так и пишем: мало данных.',
    show: 'Страница «Сценарии»: готовые шаблоны сверху, у выбранного сценария карточка «Помогает ли сценарий» с графиком «затронутые против контроля».',
    run: async (ctx) => {
      ctx.navigate('/playbooks')
      await ctx.waitFor(PLAYBOOKS_TARGET)
      ctx.spotlight([PLAYBOOKS_TARGET, PLAYBOOK_EFFECT_TARGET])
    },
  },
  {
    id: 'marketing',
    title: 'Маркетинг: MRR, LTV и что даст удержание',
    say:
      'Для маркетолога отдельная страница с привычными метриками: месячная выручка, доход с клиента, LTV, отток клиентов и выручки, а у каждой цифры по значку «i» формула и пример простыми словами. Главное здесь связь с риском: видно, на сколько вырастет LTV базы, если удержать клиентов высокого риска, а стоимость привлечения задаётся руками, потому что в данных хостинга её нет.',
    show: 'Карточки LTV и LTV/CAC сверху и блок «Что даст удержание». Ниже сегменты по риску, графики MRR и LTV по сегментам, воронка риска, выручка по периодам оплаты и «Где теряем деньги» с кликабельными причинами.',
    run: async (ctx) => {
      ctx.navigate('/marketing')
      await ctx.waitFor(MARKETING_TARGETS[2]!)
      ctx.spotlight(MARKETING_TARGETS)
    },
  },
  {
    id: 'model',
    title: 'Модель: примеряем в «Что если», потом применяем',
    say:
      'Веса признаков настраивает сам маркетолог, без программиста, но сначала их можно примерить: включаю «Что если», двигаю ползунок и вижу, что было и что станет, и кто из клиентов сменит уровень риска. Не понравилось, жму «Отменить», понравилось, жму «Применить», и оценки пересчитаются за секунды, страницу никто не перезагружает.',
    show: 'Переключатель «Что если» уже включён. Подвиньте ползунок подсвеченного признака: сверху появятся «было → станет», справа график уровней «Было и станет» и клиенты, которые сменят уровень.',
    adminOnly: true,
    readout: 'summary',
    run: async (ctx) => {
      const settings = await ctx.api.settings()
      const weight = settings.weights.find((w) => w.code === DEMO_FEATURE) ?? settings.weights.find((w) => w.enabled)
      ctx.navigate('/model')
      await ctx.waitFor(WHAT_IF_SWITCH)
      // только предпросмотр: пока не нажали «Применить», в базе ничего не меняется
      await ctx.click(WHAT_IF_OFF)
      if (!weight) {
        ctx.spotlight([WHAT_IF_SWITCH])
        return 'Режим «Что если» включён, но в модели нет признаков.'
      }
      const row = weightRow(weight.title)
      await ctx.waitFor(row)
      ctx.spotlight([WHAT_IF_SWITCH, row])
      return `Режим «Что если» включён. Подвиньте вес «${weight.title}» (сейчас ${formatWeight(weight.weight)}), до «Применить» ничего не сохранится.`
    },
    actions: [applyWeightAction, resetSettingsAction],
  },
  {
    id: 'quality',
    title: 'Качество модели: проверяем, что она не гадает',
    say:
      'Чтобы не верить модели на слово, мы прогоняем месяц синтетического будущего и смотрим, кто ушёл на самом деле: клиенты высокого риска уходят в 3,3–5,5 раза чаще среднего, ROC AUC от 0,77 до 0,93. Честно скажу, данные синтетические, так что мы проверили порядок, кто опаснее кого, а на реальной базе эти цифры нужно будет перемерить.',
    show: 'Страница «Качество модели»: сверху lift и ROC AUC, ниже ROC-кривая, сравнение с простыми правилами и устойчивость.',
    run: async (ctx) => {
      ctx.navigate('/quality')
      await ctx.waitFor(QUALITY_LIFT_TARGET)
      ctx.spotlight([QUALITY_LIFT_TARGET, QUALITY_AUC_TARGET])
    },
  },
  {
    id: 'live',
    title: 'Живые данные и уведомления',
    say:
      'Загружаем 5 000 синтетических клиентов и включаем поток изменений: первый пересчёт около 20 секунд, дальше цифры и строки таблицы меняются на глазах. Колокольчик в шапке — личные уведомления от отдельного сервиса, только о критическом (выросла ожидаемая потеря, крупный клиент ушёл в высокий риск), а пороги и каналы — панель, почта или Telegram-бот — каждый выбирает сам.',
    show: 'Колокольчик в шапке, затем «Настроить»: страница «Уведомления» с порогами, каналами, тихими часами и кнопкой тестового уведомления.',
    adminOnly: true,
    readout: 'simulator',
    run: async (ctx) => {
      ctx.navigate('/')
      ctx.spotlight([...KPI_TARGETS, LIVE_TARGET, ALERTS_TARGET])
      await ctx.api.simulator({ generate: { accounts: LIVE_ACCOUNTS, seed: LIVE_SEED } })
      await ctx.api.simulator({ running: true, interval_ms: LIVE_INTERVAL_MS })
      return 'Набор на 5 000 клиентов загружен, поток включён. Цифры обновятся, когда закончится первый пересчёт.'
    },
    actions: [stopStreamAction, restoreCaseAction],
  },
  {
    id: 'sources',
    title: 'Источники: система сама ходит за данными',
    say:
      'Данные не только приходят к нам, система сама ходит за ними: забирает изменения из биллинга по API и визиты из Яндекс Метрики, по расписанию или по кнопке «Запустить сейчас». Из Метрики модель узнаёт, кто давно не заходил в панель и кто смотрел страницу удаления услуги, а токены лежат только в окружении сервиса, в интерфейсе их не видно.',
    show: 'Раздел «Источники» внизу страницы «Данные»: статус каждого коннектора, расписание, «Запустить сейчас» и история запусков.',
    adminOnly: true,
    run: async (ctx) => {
      ctx.navigate('/data')
      await ctx.waitFor(SOURCES_TARGET)
      ctx.spotlight([SOURCES_TARGET])
    },
  },
  {
    id: 'ops',
    title: 'Метрики, надёжность и Kubernetes',
    say:
      'Каждый сервис отдаёт метрики в Prometheus, в Grafana готовые дашборды и алерты, весь API описан в OpenAPI. Надёжность проверяем по-честному: хаос-скрипт роняет NATS, а данные всё равно доходят, и на каждый пул-реквест CI ставит наш Helm-чарт в настоящий Kubernetes через kind.',
    show: 'Ссылки открываются в новой вкладке. Хаос-проверка: bash scripts/chaos.sh nats, после неё верните стенд кнопкой внизу пульта.',
    links: [
      { label: 'Grafana', href: GRAFANA_URL, hint: 'дашборды и алерты, вход admin / admin' },
      { label: 'Prometheus', href: `${PROMETHEUS_URL}/targets`, hint: 'кого и как часто опрашиваем' },
      { label: 'Swagger', href: SWAGGER_URL, hint: 'весь API, под текущим входом' },
      { label: 'Хаос-проверка', href: `${REPO_URL}/blob/main/scripts/chaos.sh`, hint: 'роняем NATS, API и scoring' },
      { label: 'CI с kind', href: `${REPO_URL}/blob/main/.github/workflows/ci.yml`, hint: 'джоба «Helm install in kind»' },
    ],
  },
  {
    id: 'roles',
    title: 'Безопасность и роли',
    say:
      'Ролей три: администратор настраивает систему, маркетолог работает с клиентами — пишет, рассылает, ставит отметки, а аналитик только смотрит и выгружает CSV, и эти запреты держит сервер, а не интерфейс. Роль может прийти и из группы в корпоративном Authentik по OIDC или SAML, так что доступ сотрудника настраивают в одном месте.',
    show: 'Войдите как marketer / marketer: письмо клиенту отправляется, а разделов «Модель», «Данные» и «Пользователи» нет. Под analyst / analyst пропадают и кнопки «Написать», сервер на такую попытку отвечает 403. Если поднят профиль sso, те же роли приходят из групп Authentik, outsider без группы получит отказ.',
    run: async (ctx) => {
      ctx.spotlight([PROFILE_TARGET])
    },
    actions: [
      {
        id: 'login-marketer',
        label: 'Выйти и войти маркетологом',
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

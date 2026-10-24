import type { Target } from './spotlight'
import { GRAFANA_URL, PROMETHEUS_URL, REPO_URL, SWAGGER_URL } from './steps'

export type FeatureTarget =
  | { kind: 'step'; step: string }
  | { kind: 'route'; to: string; targets?: Target[]; adminOnly?: boolean }
  | { kind: 'url'; href: string }

export interface Feature {
  id: string
  title: string
  detail: string
  extra?: boolean
  optional?: boolean
  target: FeatureTarget
}

const repo = (path: string) => `${REPO_URL}/${path}`

export const FEATURES: Feature[] = [
  {
    id: 'panel',
    title: 'Веб-панель с оценкой риска',
    detail: 'Score, уровень и главная причина в таблице, лояльность — в карточке клиента.',
    target: { kind: 'route', to: '/customers', targets: [{ selector: 'table[aria-label="Клиенты под риском"]', closest: '.rounded-xl' }] },
  },
  {
    id: 'sorting',
    title: 'Сортировка по ID, email, балансу и пополнениям',
    detail: 'В обе стороны, состояние таблицы живёт в адресе и переживает перезагрузку.',
    target: { kind: 'step', step: 'customers' },
  },
  {
    id: 'card',
    title: 'Карточка клиента с причинами',
    detail: 'Причины с вкладом в баллах, вклад каждого признака, услуги и транзакции.',
    target: { kind: 'step', step: 'customer' },
  },
  {
    id: 'money',
    title: 'Денежная оценка с пояснением',
    detail: 'Ожидаемая потеря и худший сценарий за горизонт прогноза, расчёт объяснён словами.',
    target: { kind: 'route', to: '/', targets: [{ selector: '[data-testid="kpi-expected-loss"]' }] },
  },
  {
    id: 'weights',
    title: 'Редактируемые коэффициенты',
    detail: 'Веса, пороги и горизонт хранятся в PostgreSQL и не сбрасываются при перезапуске. Есть история изменений.',
    target: { kind: 'step', step: 'model' },
  },
  {
    id: 'live',
    title: 'Обновление без перезагрузки',
    detail: 'Сервер сам присылает события через SSE, панель подтягивает новые оценки.',
    target: { kind: 'route', to: '/', targets: [{ selector: 'header [role="status"]' }] },
  },
  {
    id: 'metrics',
    title: '/metrics для сборщика метрик',
    detail: 'Формат Prometheus, доступ по токену. Prometheus на стенде уже опрашивает все сервисы.',
    target: { kind: 'url', href: `${PROMETHEUS_URL}/targets` },
  },
  {
    id: 'auth',
    title: 'Вход по логину и паролю',
    detail: 'Роли администратор и аналитик. Вход через Keycloak (OIDC) включается отдельным профилем.',
    target: { kind: 'step', step: 'roles' },
  },
  {
    id: 'compose',
    title: 'Запуск одной командой',
    detail: 'docker compose up -d --build --wait поднимает весь стенд с данными кейса.',
    target: { kind: 'url', href: repo('blob/main/README.md') },
  },
  {
    id: 'swagger',
    title: 'Swagger и OpenAPI',
    detail: 'Контракт API в contracts/openapi.yaml, интерактивная документация в панели.',
    target: { kind: 'url', href: SWAGGER_URL },
  },
  {
    id: 'nats',
    title: 'Микросервисы на NATS JetStream',
    extra: true,
    detail: 'Приём данных, расчёт, API, вход и уведомления — отдельные сервисы, общаются событиями.',
    target: { kind: 'url', href: repo('blob/main/docs/architecture.md') },
  },
  {
    id: 'k8s',
    title: 'Kubernetes: Helm, kind и HPA',
    extra: true,
    detail: 'Helm-чарт с автомасштабированием, локальный кластер поднимается скриптом.',
    target: { kind: 'url', href: repo('tree/main/deploy/helm/churn-radar') },
  },
  {
    id: 'grafana',
    title: 'Prometheus и Grafana с алертами',
    extra: true,
    detail: 'Готовые дашборды по бизнесу и платформе, правила алертов покрыты тестами.',
    target: { kind: 'url', href: GRAFANA_URL },
  },
  {
    id: 'ci',
    title: 'CI со сканированием Trivy',
    extra: true,
    detail: 'Линтеры, тесты и проверка образов на уязвимости на каждый пуш и пул-реквест.',
    target: { kind: 'url', href: repo('blob/main/.github/workflows/ci.yml') },
  },
  {
    id: 'e2e',
    title: 'Сквозные тесты на Playwright',
    extra: true,
    detail: 'Сценарий приёмки кейса прогоняется в настоящем браузере.',
    target: { kind: 'url', href: repo('tree/main/tests/e2e') },
  },
  {
    id: 'k6',
    title: 'Нагрузочные тесты k6',
    extra: true,
    detail: 'Таблица клиентов, приём данных и поток SSE под нагрузкой.',
    target: { kind: 'url', href: repo('tree/main/tests/load') },
  },
  {
    id: 'simulator',
    title: 'Симулятор живых данных',
    extra: true,
    detail: 'Генерирует большой набор клиентов и поток изменений для показа.',
    target: { kind: 'step', step: 'live' },
  },
  {
    id: 'recommendations',
    title: 'Рекомендации маркетологу',
    extra: true,
    detail: 'Для каждого клиента под риском — главное действие с пояснением.',
    target: { kind: 'step', step: 'customer' },
  },
  {
    id: 'calibration',
    title: 'Проверка качества модели',
    extra: true,
    detail: 'Прогоняем 30 дней синтетического будущего и смотрим, правда ли клиенты высокого риска уходят чаще.',
    target: { kind: 'url', href: repo('blob/main/docs/calibration.md') },
  },
]

export const isExternal = (href: string) => /^https?:\/\//.test(href)

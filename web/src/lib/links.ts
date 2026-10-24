/** Ссылки на соседние сервисы стенда: документация API и мониторинг. */

export const SWAGGER_PATH = '/api/v1/docs'
const GRAFANA_DEFAULT_PORT = '3000'

/**
 * Адрес Grafana: из VITE_GRAFANA_URL при сборке, иначе тот же хост, что у панели, порт 3000
 * (так Grafana публикуется в docker-compose, GRAFANA_PORT по умолчанию).
 */
export function grafanaUrl(
  configured: string | undefined = import.meta.env.VITE_GRAFANA_URL as string | undefined,
  location: Pick<Location, 'protocol' | 'hostname'> = window.location,
): string {
  const fromEnv = configured?.trim()
  if (fromEnv) return fromEnv
  const host = location.hostname.includes(':') ? `[${location.hostname}]` : location.hostname
  return `${location.protocol}//${host}:${GRAFANA_DEFAULT_PORT}`
}

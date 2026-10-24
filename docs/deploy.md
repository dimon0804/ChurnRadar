# Развёртывание

## Docker Compose (демо и разработка)

Нужен Docker с Compose v2.24+. Файл `.env` не обязателен.

```bash
git clone https://github.com/dimon0804/ChurnRadar.git && cd ChurnRadar
docker compose up -d --wait
```

| Адрес | Что | Вход |
|---|---|---|
| http://localhost:8080 | панель, API, Swagger (`/api/v1/docs`) | `admin` / `admin` (админ), `analyst` / `analyst` (просмотр) |
| http://localhost:3000 | Grafana | `admin` / `admin` |
| http://localhost:9090 | Prometheus | — |
| http://localhost:8081 | Keycloak (профиль `oidc`) | `admin` / `admin` в консоли |

Пароли по умолчанию только для демо: перед любым другим запуском задайте свои в `.env`
(образец — `.env.example`).

### Профили и варианты

```bash
docker compose --profile oidc up -d --wait     # + Keycloak, кнопка «Войти через SSO»
HTTP_PORT=18080 docker compose up -d --wait     # если 8080 занят
docker compose down -v                          # остановить и стереть данные
```

В realm Keycloak заведены пользователи `marketer` / `marketer` (роль analyst) и
`admin` / `admin` (роль admin). Вход по паролю работает и без Keycloak.

Пока сервисы ядра не влиты в ветку, стенд поднимается с заменителем:

```bash
docker compose -f docker-compose.yml -f deploy/compose/docker-compose.mock.yml up -d --wait
```

`make up` выбирает вариант сам.

### Что делает старт

1. Поднимаются PostgreSQL, Redis, NATS (JetStream, данные в volume).
2. `nats-init` создаёт стрим `CHURN`, `migrate` применяет миграции схемы `auth`.
3. Стартуют сервисы; у каждого healthcheck, `depends_on` ждёт готовности зависимостей.
4. Traefik открывает `:8080`, когда готовы auth, web и notifier.

Контейнеры работают от непривилегированного пользователя, с read-only корневой ФС,
без capabilities и с лимитами CPU/памяти.

### Проверка

```bash
curl -i localhost:8080/api/v1/customers                    # 401 без входа
curl -i -H "Authorization: Bearer churn-metrics-demo-token" localhost:8080/metrics
make e2e                                                    # сценарий приёмки
bash scripts/acceptance.sh                                  # то же на чистом клоне
```

## Kubernetes (kind + Helm)

Нужны Docker, kind, kubectl и helm.

```bash
make k8s-up        # кластер из трёх узлов, сборка образов, установка чарта
make k8s-demo      # масштабирование, HPA под нагрузкой, удаление пода
make k8s-down
```

Чарт `deploy/helm/churn-radar` подходит и для настоящего кластера: образы берутся из
`ghcr.io/dimon0804/churn-radar-*`, внешние PostgreSQL/Redis/NATS подключаются через values,
секреты передаются через `existingSecret`. Все параметры с комментариями в `deploy/helm/churn-radar/values.yaml`.

## CI/CD

`.github/workflows/ci.yml` на каждый push и PR: линтеры, тесты сервисов, сборка образов,
Trivy, `helm lint` + kubeconform и e2e в compose. По тегу `v*` `release.yml` публикует
образы в GHCR.

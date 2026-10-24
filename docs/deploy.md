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
| http://localhost:9000 | Authentik (профиль `sso`) | `akadmin` / `authentik` в админке |

Пароли по умолчанию только для демо: перед любым другим запуском задайте свои в `.env`
(образец — `.env.example`).

### Профили и варианты

```bash
docker compose --profile sso up -d --wait      # + Authentik, кнопки входа по OIDC и SAML
HTTP_PORT=18080 docker compose up -d --wait     # если 8080 занят
docker compose down -v                          # остановить и стереть данные
```

Blueprint Authentik заводит пользователей `marketer` / `marketer` (группа `churn-analysts`,
роль analyst), `admin` / `admin` (группа `churn-admins`, роль admin) и `outsider` / `outsider`
без групп (панель его не пускает). Вход по паролю работает и без Authentik. Первый старт
Authentik занимает минуту-две, памяти он берёт около 800 МБ на сервер и воркер вместе.

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

### SSO в Kubernetes

IdP чарт не ставит: Authentik (или Keycloak, Entra ID, Okta) — отдельная система, обычно она у
компании уже есть. Для Authentik есть официальный чарт `authentik/authentik`, а blueprint из
`deploy/authentik/blueprints/` подходит и для него (смонтируйте его в `/blueprints/churn-radar`
и задайте переменные `CHURN_RADAR_*`, как в `docker-compose.yml`).

Панель подключается блоком `sso`:

```yaml
sso:
  existingSecret: churn-radar-sso        # ключ OIDC_CLIENT_SECRET
  oidc:
    enabled: true
    issuer: https://auth.company.ru/application/o/churn-radar/
    internalIssuer: http://authentik-server.authentik.svc:9000/application/o/churn-radar/  # необязательно
    scopes: openid profile email churn-radar
  saml:
    enabled: true
    idpMetadataUrl: http://authentik-server.authentik.svc:9000/application/saml/churn-radar-saml/metadata/
    idpPublicUrl: https://auth.company.ru
  egress:                                # NetworkPolicy auth -> IdP внутри кластера
    - to:
        - namespaceSelector:
            matchLabels: {kubernetes.io/metadata.name: authentik}
      ports:
        - {port: 9000, protocol: TCP}
```

`auth.env.PUBLIC_URL` должен совпадать с адресом панели: из него собираются redirect URI для
OIDC, ACS и entityID для SAML. По умолчанию `sso.egress` разрешает HTTPS наружу (кроме
приватных сетей) — этого хватает для облачного IdP.

## CI/CD

`.github/workflows/ci.yml` на каждый push и PR: линтеры, тесты сервисов, сборка образов,
Trivy, `helm lint` + kubeconform и e2e в compose. По тегу `v*` `release.yml` публикует
образы в GHCR.

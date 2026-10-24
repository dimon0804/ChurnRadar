# ChurnRadar в Kubernetes

Развёртывание описано одним Helm-чартом `deploy/helm/churn-radar`. Отдельные
манифесты не поддерживаются: всё, что нужно для кластера, генерирует чарт.

## Состав чарта

| Компонент | Объекты |
|---|---|
| auth, notifier, web, api, ingest, scoring, simulator | Deployment, Service, ConfigMap, PodDisruptionBudget |
| api, scoring | HorizontalPodAutoscaler (CPU), для scoring опционально KEDA ScaledObject по лагу консьюмера JetStream |
| mock-core (`mockCore.enabled`) | заменяет api + ingest + simulator, пока нет образов ядра |
| PostgreSQL 16, Redis 7, NATS 2.10 (JetStream) | StatefulSet + PVC, Service; Job создания потока `CHURN` |
| Миграции | Job на каждую запись `migrations:` (хук `pre-install,pre-upgrade`) |
| Шлюз | Traefik `IngressRoute` + `Middleware` (ForwardAuth в auth) |
| Безопасность | NetworkPolicy (default deny), securityContext (UID 10001, read-only FS, drop ALL, seccomp) |
| Мониторинг | ServiceMonitor (`serviceMonitor.enabled`, нужен prometheus-operator) |

Маршруты шлюза совпадают с compose: публичные `/api/v1/auth/login`, `/api/v1/auth/providers`,
`/api/v1/auth/oidc/*`, `/login`, `/assets/*`, `/favicon.svg`; остальное идёт через
ForwardAuth `auth:/internal/verify` (для страниц панели — `?redirect=1`), `/metrics` —
через `auth:/internal/verify-metrics`.

## Локально (kind)

```bash
make k8s-up            # = bash deploy/kind/up.sh
bash deploy/kind/demo.sh
bash deploy/kind/down.sh
```

`up.sh` создаёт кластер (1 control-plane + 2 worker), ставит metrics-server и Traefik,
собирает образы, загружает их через `kind load` и выполняет `helm upgrade --install`.
Если Dockerfile сервисов ядра ещё нет, автоматически включается `mockCore`.
Порт панели на хосте — `KIND_HTTP_PORT` (по умолчанию 8080).

## Production-кластер

Требования: Traefik v3 с CRD `traefik.io/v1alpha1`, metrics-server (для HPA),
CNI с поддержкой NetworkPolicy, StorageClass по умолчанию.

```bash
helm upgrade --install churn-radar deploy/helm/churn-radar \
  --namespace churn-radar --create-namespace \
  --set global.image.tag=1.0.0 \
  --set secrets.existingSecret=churn-radar-secrets \
  --set gateway.host=churn.example.com \
  --wait
```

Секрет `secrets.existingSecret` должен содержать ключи `DATABASE_URL`, `REDIS_URL`,
`METRICS_TOKEN`, `ADMIN_PASSWORD`, `ANALYST_PASSWORD` и `POSTGRES_PASSWORD`
(последний — только для встроенного Postgres).

Управляемые БД: `postgresql.enabled=false` + `postgresql.external.url`, аналогично
`redis.*` и `nats.*`. Для доступа к внешним адресам добавьте правило в
`networkPolicy.extraAppEgress`.

Миграции сервисов ядра добавляются в список `migrations:`:

```yaml
migrations:
  - name: auth
    app: auth
    command: ["alembic", "upgrade", "head"]
  - name: api
    app: api
    command: ["alembic", "upgrade", "head"]
```

## Проверка чарта

```bash
bash deploy/helm/lint.sh   # helm lint + helm template | kubeconform -strict
```

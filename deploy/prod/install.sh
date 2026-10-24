#!/usr/bin/env bash
# Разворачивает или обновляет ChurnRadar на чистом Ubuntu-сервере под root.
#   DOMAIN=churnradar.example.com EMAIL=admin@example.com bash deploy/prod/install.sh
# Повторный запуск безопасен: секреты в .env не перегенерируются, код подтягивается из git.
set -euo pipefail

DOMAIN="${DOMAIN:?укажите DOMAIN}"
EMAIL="${EMAIL:-}"
REPO_URL="${REPO_URL:-https://github.com/dimon0804/ChurnRadar.git}"
BRANCH="${BRANCH:-main}"
APP_DIR="${APP_DIR:-/opt/churnradar}"
SSO="${SSO:-1}"

log() { printf '\n\033[1;34m==> %s\033[0m\n' "$*"; }

log "Пакеты"
export DEBIAN_FRONTEND=noninteractive
if ! command -v docker >/dev/null; then
  apt-get update -q
  apt-get install -yq ca-certificates curl git nginx certbot python3-certbot-nginx apache2-utils ufw
  curl -fsSL https://get.docker.com | sh
fi
systemctl enable --now docker nginx

log "Swap: стенду с Authentik тесно в 4 ГБ"
if ! swapon --show | grep -q /swapfile; then
  fallocate -l 4G /swapfile && chmod 600 /swapfile && mkswap /swapfile && swapon /swapfile
  grep -q '^/swapfile' /etc/fstab || echo '/swapfile none swap sw 0 0' >> /etc/fstab
  sysctl -w vm.swappiness=10 >/dev/null
fi

log "Файрвол: наружу только SSH и веб"
ufw allow OpenSSH >/dev/null
ufw allow 'Nginx Full' >/dev/null
ufw --force enable >/dev/null

log "Код"
if [[ -d "$APP_DIR/.git" ]]; then
  git -C "$APP_DIR" fetch -q origin "$BRANCH"
  git -C "$APP_DIR" checkout -q "$BRANCH"
  git -C "$APP_DIR" reset -q --hard "origin/$BRANCH"
else
  git clone -q --branch "$BRANCH" "$REPO_URL" "$APP_DIR"
fi
cd "$APP_DIR"

secret() { python3 -c 'import secrets; print(secrets.token_urlsafe(24))'; }

if [[ ! -f .env ]]; then
  log "Секреты (.env создаётся один раз, только для root)"
  umask 077
  cat > .env <<ENV
PUBLIC_URL=https://$DOMAIN
COOKIE_SECURE=true
POSTGRES_PASSWORD=$(secret)
METRICS_TOKEN=$(secret)
ADMIN_PASSWORD=$(secret)
ANALYST_PASSWORD=$(secret)
GRAFANA_ADMIN_PASSWORD=$(secret)
OIDC_CLIENT_SECRET=$(secret)
OIDC_PUBLIC_ISSUER=https://$DOMAIN/sso/application/o/churn-radar/
OIDC_INTERNAL_ISSUER=http://authentik-server:9000/sso/application/o/churn-radar/
SAML_IDP_METADATA_URL=http://authentik-server:9000/sso/application/saml/churn-radar-saml/metadata/
SAML_IDP_PUBLIC_URL=https://$DOMAIN/sso
AUTHENTIK_SECRET_KEY=$(secret)$(secret)
AUTHENTIK_DB_PASSWORD=$(secret)
AUTHENTIK_ADMIN_PASSWORD=$(secret)
SSO_DEMO_ADMIN_PASSWORD=$(secret)
SSO_DEMO_MARKETER_PASSWORD=$(secret)
SSO_DEMO_OUTSIDER_PASSWORD=$(secret)
MAILPIT_PASSWORD=$(secret)
# на 2 ядрах хватает по одной реплике; HPA и реплики показывает Kubernetes
API_REPLICAS=2
SCORING_REPLICAS=1
NOTIFIER_REPLICAS=1
ALERTS_REPLICAS=1
ENV
  umask 022
fi

log "Mailpit под паролем"
htpasswd -bc /etc/nginx/churnradar-mailpit.htpasswd mail "$(grep '^MAILPIT_PASSWORD=' .env | cut -d= -f2)" 2>/dev/null

log "nginx"
sed "s/churnradar.clv-digital.tech/$DOMAIN/" deploy/prod/nginx.conf > /etc/nginx/sites-available/churnradar
ln -sf /etc/nginx/sites-available/churnradar /etc/nginx/sites-enabled/churnradar
rm -f /etc/nginx/sites-enabled/default
nginx -t && systemctl reload nginx

log "Стенд"
COMPOSE=(docker compose -f docker-compose.yml -f deploy/prod/compose.prod.yml)
[[ "$SSO" == "1" ]] && COMPOSE+=(--profile sso)
"${COMPOSE[@]}" up -d --build --wait --remove-orphans

log "HTTPS"
if [[ ! -d "/etc/letsencrypt/live/$DOMAIN" ]]; then
  certbot --nginx -d "$DOMAIN" --non-interactive --agree-tos --redirect \
    ${EMAIL:+-m "$EMAIL"} ${EMAIL:---register-unsafely-without-email}
fi
systemctl reload nginx

log "Готово: https://$DOMAIN"
grep -E '^(ADMIN_PASSWORD|ANALYST_PASSWORD|GRAFANA_ADMIN_PASSWORD|MAILPIT_PASSWORD|AUTHENTIK_ADMIN_PASSWORD|SSO_DEMO_[A-Z]+_PASSWORD)=' .env \
  | sed 's/^/  /'

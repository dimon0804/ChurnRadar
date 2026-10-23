# syntax=docker/dockerfile:1.7
# Собирается из корня репозитория: docker build -f web/Dockerfile .
# (типы API генерируются из contracts/openapi.yaml)

FROM node:22-alpine AS build
WORKDIR /src/web
ENV npm_config_update_notifier=false \
    npm_config_fund=false \
    npm_config_audit=false

COPY contracts/openapi.yaml /src/contracts/openapi.yaml
COPY web/package.json web/package-lock.json ./
RUN --mount=type=cache,target=/root/.npm npm ci

COPY web/ ./
RUN npm run build


FROM nginxinc/nginx-unprivileged:1.27-alpine AS runtime

LABEL org.opencontainers.image.title="churn-radar-web" \
      org.opencontainers.image.description="ChurnRadar: веб-панель маркетолога" \
      org.opencontainers.image.source="https://github.com/dimon0804/churn-radar"

COPY web/nginx/nginx.conf /etc/nginx/nginx.conf
COPY web/nginx/security-headers.conf /etc/nginx/security-headers.conf
COPY --from=build /src/web/dist /usr/share/nginx/html

# nginx-unprivileged работает от uid 101; корневая ФС может быть read-only,
# всё временное (pid, кэши тела запроса) лежит в /tmp
USER 101
EXPOSE 8080

HEALTHCHECK --interval=15s --timeout=3s --start-period=5s --retries=3 \
  CMD wget -q --spider http://127.0.0.1:8080/healthz || exit 1

CMD ["nginx", "-g", "daemon off;"]

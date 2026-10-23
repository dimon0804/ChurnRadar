SHELL := /bin/bash
COMPOSE ?= docker compose
# while services/api is not in the tree the stack runs with the stand-in backend
ifeq ($(wildcard services/api/Dockerfile),)
COMPOSE_FILES := -f docker-compose.yml -f deploy/compose/docker-compose.mock.yml
else
COMPOSE_FILES := -f docker-compose.yml
endif
DC := $(COMPOSE) $(COMPOSE_FILES)

.PHONY: help up up-oidc down clean logs ps build test test-auth test-notifier test-web e2e acceptance \
        k8s-up k8s-down k8s-demo helm-lint load

help:
	@grep -E '^[a-z0-9-]+:.*##' $(MAKEFILE_LIST) | awk -F':.*## ' '{printf "  %-14s %s\n", $$1, $$2}'

up: ## поднять всё: http://localhost:8080
	$(DC) up -d --build --wait

up-oidc: ## то же плюс Keycloak на :8081
	$(DC) --profile oidc up -d --build --wait

down: ## остановить
	$(DC) --profile oidc down

clean: ## остановить и удалить данные
	$(DC) --profile oidc down -v --remove-orphans

logs: ## логи всех сервисов
	$(DC) logs -f --tail=100

ps:
	$(DC) ps

build:
	$(DC) build

test: test-auth test-notifier test-web ## unit-тесты платформенных сервисов

test-auth:
	docker build --target test services/auth

test-notifier:
	docker build --target build services/notifier

test-web:
	cd web && npm ci && npm test -- --run

e2e: ## e2e-сценарий приёмки против запущенного стенда
	cd tests/e2e && npm ci && npx playwright install --with-deps chromium && npx playwright test

acceptance: ## чистый клон → up → e2e → down
	bash scripts/acceptance.sh

k8s-up: ## kind-кластер + Helm-чарт, http://localhost:8080
	bash deploy/kind/up.sh

k8s-down:
	bash deploy/kind/down.sh

k8s-demo: ## масштабирование, HPA, убийство пода
	bash deploy/kind/demo.sh

helm-lint:
	bash deploy/helm/lint.sh

load: ## нагрузочные сценарии k6
	bash tests/load/run.sh

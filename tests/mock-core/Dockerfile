# syntax=docker/dockerfile:1.7
# Сборка из корня репозитория:
#   docker build -f tests/mock-core/Dockerfile -t churn-radar-mock-core:dev .

FROM python:3.12-slim AS build
ENV PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1
COPY tests/mock-core/requirements.txt /tmp/requirements.txt
RUN python -m venv /opt/venv \
 && /opt/venv/bin/pip install --no-cache-dir -r /tmp/requirements.txt
COPY tests/mock-core/app /app/app
RUN /opt/venv/bin/python -m compileall -q /app/app

FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH=/opt/venv/bin:$PATH \
    SNAPSHOT_PATH=/app/data.json \
    CONTRACT_PATH=/app/openapi.yaml \
    SETTINGS_PATH=/data/settings.json \
    NATS_URL=nats://nats:4222
RUN groupadd --gid 10001 app \
 && useradd --uid 10001 --gid 10001 --no-create-home --shell /usr/sbin/nologin app \
 && mkdir -p /data \
 && chown 10001:10001 /data
COPY --from=build /opt/venv /opt/venv
COPY --from=build /app/app /app/app
COPY data/sample/data.json /app/data.json
COPY contracts/openapi.yaml /app/openapi.yaml
WORKDIR /app
USER 10001:10001
EXPOSE 8000
VOLUME ["/data"]
HEALTHCHECK --interval=10s --timeout=3s --start-period=15s --retries=3 \
  CMD ["python", "-c", "import sys, urllib.request; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/healthz', timeout=2).status == 200 else 1)"]
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers", "--forwarded-allow-ips", "*"]

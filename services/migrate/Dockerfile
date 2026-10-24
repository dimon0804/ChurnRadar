# Сборка из корня репозитория: docker build -f services/migrate/Dockerfile .
FROM python:3.12-slim AS builder
ENV PIP_DISABLE_PIP_VERSION_CHECK=1
RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"
COPY services/migrate/requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt

FROM python:3.12-slim
ENV PATH="/opt/venv/bin:$PATH" \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    MIGRATIONS_DIRS=/migrations/core,/migrations/auth
RUN groupadd --system --gid 10001 app && useradd --system --uid 10001 --gid app --no-create-home app
COPY --from=builder /opt/venv /opt/venv
COPY db/ /migrations/
COPY services/migrate/migrate.py /app/migrate.py
WORKDIR /app
USER app
# одноразовая задача: завершается с кодом 0 после применения миграций
CMD ["python", "/app/migrate.py"]

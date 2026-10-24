# Сборка из корня репозитория: docker build -f services/api/Dockerfile .
# Внутри контейнера сервис слушает HTTP_PORT (по умолчанию 8000); корневая ФС может быть read-only,
# писать нужно только в /tmp.
FROM python:3.12-slim AS builder
ENV PIP_DISABLE_PIP_VERSION_CHECK=1
RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"
COPY services/api/requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt
COPY libs/riskcore /tmp/riskcore
COPY libs/svckit /tmp/svckit
RUN pip install --no-cache-dir /tmp/riskcore /tmp/svckit

FROM python:3.12-slim
ENV PATH="/opt/venv/bin:$PATH" \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    HTTP_PORT=8000 \
    OPENAPI_PATH=/app/contracts/openapi.yaml
RUN groupadd --system --gid 10001 app && useradd --system --uid 10001 --gid app --no-create-home app
COPY --from=builder /opt/venv /opt/venv
WORKDIR /app
COPY --chown=app:app services/api/app /app/app
COPY --chown=app:app contracts/openapi.yaml /app/contracts/openapi.yaml
USER app
EXPOSE 8000
HEALTHCHECK --interval=10s --timeout=3s --start-period=20s --retries=5 \
  CMD python -c "import os,sys,urllib.request; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:%s/readyz' % os.environ.get('HTTP_PORT', '8000'), timeout=2).status == 200 else 1)"
CMD ["sh", "-c", "exec uvicorn app.main:app --host 0.0.0.0 --port \"$HTTP_PORT\" --no-access-log --proxy-headers"]

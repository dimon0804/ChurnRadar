# Сборка из корня репозитория: docker build -f services/simulator/Dockerfile .
FROM python:3.12-slim AS builder
ENV PIP_DISABLE_PIP_VERSION_CHECK=1
RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"
COPY services/simulator/requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt
COPY libs/riskcore /tmp/riskcore
RUN pip install --no-cache-dir /tmp/riskcore

FROM python:3.12-slim
ENV PATH="/opt/venv/bin:$PATH" \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    HTTP_PORT=8003
RUN groupadd --system --gid 10001 app && useradd --system --uid 10001 --gid app --no-create-home app
COPY --from=builder /opt/venv /opt/venv
WORKDIR /app
COPY --chown=app:app services/simulator/app /app/app
USER app
EXPOSE 8003
HEALTHCHECK --interval=10s --timeout=3s --start-period=15s --retries=5 \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8003/healthz', timeout=2).status == 200 else 1)"
CMD ["python", "-m", "app.main"]

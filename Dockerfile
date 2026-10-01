FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    RESEARCHKB_CONFIG=/app/config/config.json

WORKDIR /app

COPY app/ /app/

# The service runs as root inside its own container so it can write into the
# bind-mounted vault/catalog/state directories without chmod/chown on the host.
EXPOSE 8765

HEALTHCHECK --interval=30s --timeout=10s --start-period=20s --retries=3 \
    CMD python -c "import urllib.request;urllib.request.urlopen('http://127.0.0.1:8765/healthz').read()"

CMD ["python", "-m", "library", "serve", "--config", "/app/config/config.json"]

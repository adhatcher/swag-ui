FROM node:22-alpine AS frontend-build
WORKDIR /frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

FROM python:3.12-slim AS python-base
COPY --from=ghcr.io/astral-sh/uv:0.11.7 /uv /usr/local/bin/uv
WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project
COPY backend/ ./backend/
COPY helper/ ./helper/
RUN uv sync --frozen --no-dev
ENV PATH="/app/.venv/bin:$PATH" PYTHONUNBUFFERED=1

FROM python-base AS helper
RUN mkdir -p /backups /run/swag-ui && chmod 700 /backups
COPY scripts/helper-entrypoint.sh /usr/local/bin/helper-entrypoint
RUN chmod +x /usr/local/bin/helper-entrypoint
ENV SWAG_UI_BACKUP_DIR=/backups
ENTRYPOINT ["helper-entrypoint"]

FROM python-base AS web
COPY --from=frontend-build /frontend/dist/ ./frontend/dist/
RUN groupadd --gid 10001 app && useradd --uid 10001 --gid 10001 --home-dir /app --no-create-home app \
    && mkdir -p /data && chown -R app:app /data /app
USER app
EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/healthz', timeout=3)"
CMD ["uvicorn", "backend.app.main:create_app", "--factory", "--host", "0.0.0.0", "--port", "8080", "--workers", "1"]

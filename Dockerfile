# syntax=docker/dockerfile:1
FROM node:22-bookworm-slim AS frontend
WORKDIR /build
COPY package.json package-lock.json ./
RUN --mount=type=cache,target=/root/.npm npm ci
COPY index.html tsconfig.json tsconfig.node.json vite.config.ts ./
COPY src ./src
ENV VITE_API_BASE=/api
RUN npm run build

FROM python:3.12-slim-bookworm AS runtime
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    DJANGO_DEBUG=false \
    DJANGO_ALLOWED_HOSTS=localhost,127.0.0.1 \
    DJANGO_CORS_ALLOWED_ORIGINS= \
    DJANGO_SECURE_COOKIES=false \
    DJANGO_SECRET_KEY_FILE=/data/secrets/django.key \
    CREDENTIAL_ENCRYPTION_KEY_FILE=/data/secrets/credential.key \
    DATABASE_URL=sqlite:////data/db.sqlite3 \
    REVIEW_EXECUTE_INLINE=false
RUN apt-get update \
    && apt-get install -y --no-install-recommends nginx git ca-certificates \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --gid 10001 app \
    && useradd --uid 10001 --gid app --create-home app
WORKDIR /app/backend
COPY backend/requirements.txt /tmp/requirements.txt
COPY docker/requirements.txt /tmp/container-requirements.txt
RUN --mount=type=cache,target=/root/.cache/pip \
    pip install -r /tmp/requirements.txt -r /tmp/container-requirements.txt
COPY backend ./
COPY docker /app/docker
COPY scripts /app/scripts
COPY --from=frontend /build/dist /app/frontend
RUN mkdir -p /data/secrets /data/runtime \
    && ln -s /data/runtime /app/backend/.runtime \
    && chown -R app:app /data \
    && chmod 700 /data/secrets \
    && chmod +x /app/docker/entrypoint.sh
ARG SOURCE_URL=https://github.com/bennie-asi/cw-cr
ARG VERSION=dev
ARG REVISION=unknown
LABEL org.opencontainers.image.title="cw-cr · 智审台" \
    org.opencontainers.image.description="Self-hosted GitLab AI code review platform" \
    org.opencontainers.image.source=$SOURCE_URL \
    org.opencontainers.image.version=$VERSION \
    org.opencontainers.image.revision=$REVISION \
    org.opencontainers.image.licenses=MIT
USER app
EXPOSE 8080
VOLUME ["/data"]
HEALTHCHECK --interval=30s --timeout=10s --start-period=90s --retries=3 \
    CMD ["python", "/app/docker/healthcheck.py"]
ENTRYPOINT ["/app/docker/entrypoint.sh"]
CMD ["serve"]

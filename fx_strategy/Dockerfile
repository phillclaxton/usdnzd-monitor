# syntax=docker/dockerfile:1
# ---------------------------------------------------------------------------
# Stage 1: build the frontend bundle.
# ---------------------------------------------------------------------------
FROM node:22-bookworm-slim AS frontend

WORKDIR /build
COPY rootfs/app/frontend/package.json rootfs/app/frontend/package-lock.json* ./
RUN npm ci --no-audit --no-fund
COPY rootfs/app/frontend/ ./
RUN npm run build

# ---------------------------------------------------------------------------
# Stage 2: the add-on image itself.
# ---------------------------------------------------------------------------
ARG BUILD_FROM=ghcr.io/home-assistant/amd64-base-debian:trixie
FROM ${BUILD_FROM}

ARG BUILD_ARCH=amd64
ARG BUILD_VERSION=0.0.0

ENV LANG=C.UTF-8 \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    VIRTUAL_ENV=/opt/venv \
    PATH=/opt/venv/bin:$PATH \
    FX_APP_VERSION=${BUILD_VERSION} \
    FX_BUILD_ARCH=${BUILD_ARCH} \
    FX_STATIC_DIR=/app/frontend

# Debian trixie provides Python 3.13 as `python3`.
RUN set -eux; \
    apt-get update; \
    apt-get install -y --no-install-recommends \
        python3 \
        python3-venv \
        ca-certificates \
        tzdata; \
    python3 -m venv "${VIRTUAL_ENV}"; \
    rm -rf /var/lib/apt/lists/*

COPY rootfs/app/backend/requirements.txt /tmp/requirements.txt
RUN set -eux; \
    apt-get update; \
    apt-get install -y --no-install-recommends build-essential python3-dev; \
    pip install --no-cache-dir -r /tmp/requirements.txt; \
    apt-get purge -y --auto-remove build-essential python3-dev; \
    rm -rf /var/lib/apt/lists/* /tmp/requirements.txt

COPY rootfs/app/backend /app/backend
COPY --from=frontend /build/dist /app/frontend
COPY rootfs/etc /etc

RUN chmod a+x /etc/services.d/fx-strategy/run /etc/services.d/fx-strategy/finish

WORKDIR /app/backend

LABEL \
    io.hass.name="FX Strategy Manager" \
    io.hass.description="Monitor and manage staged foreign exchange conversion strategies" \
    io.hass.arch="${BUILD_ARCH}" \
    io.hass.type="addon" \
    io.hass.version="${BUILD_VERSION}"

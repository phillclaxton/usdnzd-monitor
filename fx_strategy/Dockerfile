# syntax=docker/dockerfile:1

# ---------------------------------------------------------------------------
# Build arguments, declared in the global scope.
#
# Only an ARG declared before the *first* FROM can be used in a FROM
# instruction. Declaring BUILD_FROM further down scopes it to whichever stage
# precedes it, and the base image name then resolves to an empty string:
#
#   ERROR: failed to solve: base name (${BUILD_FROM}) should not be blank
#
# The Supervisor always supplies BUILD_ARCH and BUILD_VERSION, and supplies
# BUILD_FROM from build.yaml. The default below is only a fallback for a plain
# `docker build` on a development machine; it is the multi-platform manifest,
# which covers amd64 and arm64 but not armv7. armv7 comes from build.yaml.
# ---------------------------------------------------------------------------
ARG BUILD_FROM=ghcr.io/home-assistant/base-debian:trixie
ARG BUILD_ARCH=amd64
ARG BUILD_VERSION=0.0.0

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
# Stage 2: the app image itself.
# ---------------------------------------------------------------------------
FROM ${BUILD_FROM}

# A global ARG is not inherited by a build stage: re-declaring it without a
# value pulls in the global value (or its default).
ARG BUILD_ARCH
ARG BUILD_VERSION

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

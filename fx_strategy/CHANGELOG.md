# Changelog

All notable changes to this app are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project uses
[semantic versioning](https://semver.org/spec/v2.0.0.html).

## [0.1.0] - 2026-08-01

App shell and foundations.

### Added

- Home Assistant app packaging: manifest, multi-architecture build definition,
  Dockerfile and s6 service, with Ingress and no external port.
- FastAPI backend on Python 3.13 with structured JSON logging that scrubs
  credential-shaped values from every record.
- SQLite storage in WAL mode with Alembic migrations, and Decimal-safe column
  types so money and rates never pass through binary floating point.
- Settings document with the defaults from the product specification, and an
  append-only audit trail covering every settings change.
- Health, readiness and liveness endpoints.
- React 18 + TypeScript + Vite frontend that works under a dynamic Ingress path,
  with light and dark themes derived from Home Assistant's own CSS variables.
- Content Security Policy, security headers, cross-origin guard and rate
  limiting on sensitive endpoints.
- Backend and frontend test suites, including exact-decimal round-trip tests and
  Ingress base-path tests.

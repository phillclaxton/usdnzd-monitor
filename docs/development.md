# Development guide

## Layout

```
fx_strategy/
  config.yaml build.yaml Dockerfile      Home Assistant app packaging
  rootfs/etc/services.d/fx-strategy/     s6 service scripts
  rootfs/app/backend/                    FastAPI + SQLAlchemy + Alembic
  rootfs/app/frontend/                   React + TypeScript + Vite
docs/                                    this documentation
scripts/generate_brand_assets.py         regenerates icon.png and logo.png
```

## Backend

```bash
cd fx_strategy/rootfs/app/backend
uv venv --python 3.13 .venv
uv pip install --python .venv/bin/python -r requirements.txt
uv pip install --python .venv/bin/python pytest pytest-asyncio pytest-cov ruff mypy

.venv/bin/python -m pytest                 # tests
.venv/bin/ruff check . && .venv/bin/ruff format .
.venv/bin/mypy app
FX_DATA_DIR=/tmp/fx .venv/bin/python -m uvicorn app.main:app --port 8099
```

Each test gets its own temporary `/data` and a database built by running the
real Alembic chain, so migrations are exercised on every test run rather than in
one dedicated test.

### Adding a migration

```bash
FX_DATA_DIR=/tmp/fx .venv/bin/alembic revision --autogenerate -m "what changed"
```

Then read the generated file. Autogenerate renders custom column types as
`app.database.RateText(length=34)`; both accept a `length` argument so the
round-trip works, but check it. Always verify both directions:

```bash
FX_DATA_DIR=/tmp/fx .venv/bin/alembic upgrade head
FX_DATA_DIR=/tmp/fx .venv/bin/alembic downgrade base
```

## Frontend

```bash
cd fx_strategy/rootfs/app/frontend
npm install
npm test           # Vitest
npm run typecheck
npm run build
npm run dev        # proxies /api to http://127.0.0.1:8099
```

### End-to-end

```bash
npm run build      # the e2e server serves dist/
npx playwright test
```

The suite starts the real backend behind a proxy that mimics Home Assistant
Ingress, so base-path regressions fail the tests. If your environment has a
pre-installed Chromium whose build differs from the pinned Playwright version:

```bash
FX_CHROMIUM_PATH=/opt/pw-browsers/chromium-1194/chrome-linux/chrome npx playwright test
```

## Conventions

- **Never use a float for money.** Not in Python, not in TypeScript. The one
  float column is documented in `models/rate.py` and never reaches a displayed
  figure.
- **`None` means "not calculable", never zero.** The UI shows a dash and, for
  fees, "Fee not included".
- **Financial rules live in `services/calculations.py`** as pure functions. If a
  rule needs the database, the rule still goes there and the caller supplies the
  data.
- **A failure is reported.** No stale substitution, no invented sample, no
  swallowed provider error.
- **New entities go in `home_assistant/entities.py`**, once. Discovery, state
  publication and the REST fallback all read that table.

## Coverage

CI enforces 85% overall and 95% on `app.services.calculations` and `app.money`.
The second bar exists because those modules decide what a user believes about
their own money.

## Building the image

```bash
docker build fx_strategy \
  --build-arg BUILD_FROM=ghcr.io/home-assistant/amd64-base-debian:trixie \
  --build-arg BUILD_ARCH=amd64 \
  --build-arg BUILD_VERSION=0.8.0
```

CI builds `linux/amd64` and `linux/arm64` on every pull request.

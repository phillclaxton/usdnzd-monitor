# FX Strategy Manager

**FX Strategy Manager is a self-hosted decision-support tool. It does not provide
financial advice and does not automatically transfer or convert money.**

A Home Assistant app (formerly "add-on") for watching a large balance you are
converting from one currency into another — built for USD → NZD through Wise,
but configurable for any supported pair.

It answers one question: **what is my FX position, and has anything important
changed?** It watches the rate, values what you still hold, keeps a permanent
record of what you converted and what that gained against a baseline, and says
something when the market moves enough to be worth hearing about.

It does not answer "what should I convert next?". Your provider executes; this
records and monitors. It is not a trading platform, an investment adviser or a
forecasting system.

---

## What it does

- Monitors the exchange rate from a provider you choose, storing full history.
- Holds your **position**: what is still unconverted, and the baseline rate you
  measure everything against.
- Shows what that balance is worth right now, what it has gained on paper, and
  what your recorded conversions actually realised.
- Records the conversions you performed, with what each one gained against the
  baseline and a running total — and never presents a reconstructed amount as a
  confirmed one.
- Tracks your **mortgage offset shortfall** and what waiting on it costs a day
  at your floating rate.
- Notifies you through Home Assistant when the rate moves enough to matter, with
  priming, hysteresis and a minimum movement since it last spoke, so it does not
  fill your phone as the rate wobbles.
- Publishes sensors to Home Assistant via MQTT discovery, and still works
  without MQTT.
- Runs entirely on your own hardware. Data leaves the machine only when talking
  to the rate provider or the Wise API you configured.

## What it deliberately does not do

- It never executes a conversion. There is no code path that moves money.
- It never tells you what to convert, or when. No message it sends contains a
  recommendation, and there is a test that fails if one does.
- It does not store your Wise password or automate the Wise website.
- It does not forecast rates or present a prediction as certain.
- It exposes no external port and requires no cloud service.

## Installation

1. In Home Assistant, open **Settings → Apps → App Store**.
2. Open the three-dot menu, choose **Repositories**, and add:

   ```text
   https://github.com/phillclaxton/usdnzd-monitor
   ```

3. Install **FX Strategy Manager** from the list, then start it.
4. Open it from the Home Assistant sidebar.

The app runs behind Home Assistant Ingress, so it uses your existing Home
Assistant login and needs no port forwarding.

## Documentation

| Document | Contents |
| --- | --- |
| [App documentation](fx_strategy/DOCS.md) | Installation, configuration, first-run setup |
| [Installation](docs/installation.md) | Adding the repository, the app options, upgrading |
| [First-run setup](docs/setup.md) | The four-step wizard and the position form |
| [Rate providers](docs/rate-providers.md) | Choosing providers, fallback order, manual rates |
| [Wise](docs/wise.md) | Read-only credentials, reconciliation, why nothing executes |
| [Home Assistant entities](docs/mqtt.md) | MQTT discovery, every entity published, the REST fallback |
| [Backup and restore](docs/backup-restore.md) | What a backup contains, what it deliberately omits |
| [CSV formats](docs/csv-formats.md) | Import and export column definitions |
| [Troubleshooting](docs/troubleshooting.md) | Common problems and the diagnostics bundle |
| [Architecture](docs/architecture.md) | How the pieces fit together |
| [API reference](docs/api.md) | The internal HTTP API |
| [Security model](docs/security.md) | Threat model, secret handling, what is trusted |
| [Development](docs/development.md) | Running the stack locally, tests, coverage bars |
| [Release process](docs/release-process.md) | Versioning, the multi-arch build, publishing |
| [Upstream notes](docs/upstream-notes.md) | Where this build differs from the original specification |

## Development

```bash
# Backend
cd fx_strategy/rootfs/app/backend
uv venv --python 3.13 .venv
uv pip install --python .venv/bin/python -r requirements.txt
uv pip install --python .venv/bin/python pytest pytest-asyncio pytest-cov ruff mypy
.venv/bin/python -m pytest

# Frontend
cd fx_strategy/rootfs/app/frontend
npm install
npm test
npm run build
```

See [docs/development.md](docs/development.md) for the full workflow.

## Licence

MIT — see [LICENSE](LICENSE).

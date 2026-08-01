# FX Strategy Manager

**FX Strategy Manager is a self-hosted decision-support tool. It does not provide
financial advice and does not automatically transfer or convert money.**

A Home Assistant app (formerly "add-on") for managing the staged conversion of a
large balance from one currency into another — built for USD → NZD through Wise,
but configurable for any supported pair.

It watches the exchange rate, tracks how much is still unconverted, calculates
what a conversion would actually produce after fees, tells you when one of your
target rates is reached, and keeps a permanent record of what you converted and
at what blended rate.

It is not a trading platform, an investment adviser or a forecasting system.

---

## What it does

- Monitors the exchange rate from a provider you choose, storing full history.
- Holds a **tranche ladder**: convert 15% at 1.7200, 20% at 1.7400, and so on.
- Shows what you would receive right now, gross, minus estimated fees, net.
- Puts the dollar consequence next to every rate movement — at USD 800,000, one
  cent is NZD 8,000.
- Notifies you through Home Assistant when a target is reached, once, with
  hysteresis and cooldowns so it does not spam you as the rate wobbles.
- Records the conversions you actually performed and recalculates your blended
  effective rate.
- Publishes sensors to Home Assistant via MQTT discovery, and still works
  without MQTT.
- Runs entirely on your own hardware. Data leaves the machine only when talking
  to the rate provider or the Wise API you configured.

## What it deliberately does not do

- It never executes a conversion. There is no code path that moves money.
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
| [Architecture](docs/architecture.md) | How the pieces fit together |
| [API reference](docs/api.md) | The internal HTTP API |
| [Security model](docs/security.md) | Threat model, secret handling, what is trusted |
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

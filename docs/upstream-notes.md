# Upstream notes

Differences between the product specification and what the current Home
Assistant and Wise platforms actually offer, checked before implementation and
kept here so the reasoning is not lost.

## Home Assistant app manifest

| Specification | Reality checked at build time | Action taken |
| --- | --- | --- |
| `map: - addon_config:rw` | The directory is now called `app_config`. Official add-ons use either `all_app_configs:rw` (short form) or `type: app_config` / `read_only: false` (long form). | Used the long form with `type: app_config`. `homeassistant: 2024.4.0` is declared so an older Supervisor refuses to install rather than mis-parsing the key. |
| Terminology "add-on" | Developer documentation now says "app", but the manifest keys, `io.hass.type=addon` label and Supervisor API are unchanged. | Product text says "app"; manifest keys and labels are left at their real values. |
| `arch: amd64, aarch64` (+ armv7 where practical) | `ghcr.io/home-assistant/{arch}-base-debian:trixie` exists for all three. Debian trixie ships Python 3.13, which the specification requires; no Home Assistant `base-python` image publishes 3.13 yet (latest is 3.12-alpine). | Built from `base-debian:trixie` and installed Python 3.13 from the distribution. The floating `trixie` tag is used because pinned date tags are not published uniformly across architectures. |
| `startup: application`, `boot: auto`, `init: false`, `ingress: true` | All still current. | Used as specified, plus `ingress_stream: true` so the WebSocket event stream passes through Ingress cleanly. |
| MQTT | `services: - mqtt:want` lets the Supervisor hand over broker credentials when a broker exists, without making one mandatory. | Declared `mqtt:want`; the app degrades to REST-only entity publication when no broker is present. |

## Building the image

The Supervisor has moved app builds to Docker BuildKit and now logs
`uses build.yaml which is deprecated. Move build parameters into the Dockerfile
directly.` Checked against the Supervisor source (`supervisor/apps/build.py`),
the current behaviour is:

- `BUILD_VERSION` and `BUILD_ARCH` are **always** passed as build arguments.
- `BUILD_FROM` is passed **only** when `build.yaml` supplies it. Without
  `build.yaml`, `base_image` is `None` and the Dockerfile's own default is what
  resolves.

`build.yaml` is kept for now, because it is the only thing that selects
`ghcr.io/home-assistant/armv7-base-debian:trixie`: the new multi-platform
manifest `ghcr.io/home-assistant/base-debian:trixie` publishes linux/amd64 and
linux/arm64 only. Dropping `build.yaml` would silently drop armv7, which
`config.yaml` declares. That multi-platform manifest is used as the Dockerfile's
default so a plain `docker build` still works on a development machine.

Build arguments are declared **before the first `FROM`**. Only an `ARG` in that
global scope can be used in a `FROM` instruction; one declared lower down
belongs to the stage above it, and the base image name then resolves to an empty
string. A global `ARG` is not inherited by a stage either, so `BUILD_ARCH` and
`BUILD_VERSION` are re-declared without values inside the stage that uses them.

## Ingress

The Supervisor forwards the mount prefix in the `X-Ingress-Path` header. The
frontend is built with Vite `base: './'` so every asset URL is relative, and the
backend injects a matching `<base href>` into `index.html` on each request. The
router derives its `basename` from `document.baseURI`, and API and WebSocket
URLs are resolved against the same base. There is no hard-coded origin and no
assumption of `/` anywhere in the bundle.

## Wise API

| Specification | Reality | Action taken |
| --- | --- | --- |
| Wise rate endpoint | `GET /v1/rates?source=&target=` on `https://api.transferwise.com` (sandbox: `https://api.wise-sandbox.com`), returning `[{rate, source, target, time}]`. Historical data uses the same endpoint with `from`, `to` and `group` (`day`/`hour`/`minute`). | Implemented against those parameters, with the response shape validated defensively rather than trusted. |
| Authentication | Personal API tokens use `Authorization: Bearer <token>`. Affiliate integrations use Basic auth with a client ID and secret. Which one works depends on the account type. | Bearer is the default; the connection test reports precisely which call failed so a user on the wrong credential type finds out immediately rather than seeing an empty rate. |
| Quotes | Creating a quote requires an authenticated profile and returns a rate that is only executable inside Wise. | Quotes are used for fee estimation only, and are labelled "estimate" everywhere. An unauthenticated quote is never described as executable. |
| Auto Conversions | Wise's scheduled conversion feature is created in Wise, not through this API. | The app calculates the instructions and the user enters them in Wise. This app never creates or executes one. |

## Deviations from the specification worth flagging

- **`Numeric` on SQLite.** SQLAlchemy's `Numeric` type degrades to binary
  floating point on SQLite, which would defeat the Decimal requirement. Money and
  rates are stored through a custom `DecimalText` column that keeps a canonical
  fixed-scale string. An unindexed float companion column exists on rate samples
  purely so SQL `MIN`/`MAX` over a year of history stays fast; it is never used
  for a displayed figure.
- **Python version.** The specification asks for Python 3.13. The container gets
  it from Debian trixie; the test suite is also run on 3.13.

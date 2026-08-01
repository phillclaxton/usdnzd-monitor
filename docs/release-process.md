# Release process

## Versioning

Semantic versioning. The version appears in five places, and CI fails the
publish if they disagree with the tag:

| File | Field |
| --- | --- |
| `fx_strategy/config.yaml` | `version` |
| `fx_strategy/rootfs/app/backend/pyproject.toml` | `version` |
| `fx_strategy/rootfs/app/backend/app/__init__.py` | `__version__` |
| `fx_strategy/rootfs/app/backend/app/config.py` | `app_version` default |
| `fx_strategy/rootfs/app/frontend/package.json` | `version` |

## Steps

1. Confirm `main` is green: backend tests, frontend tests, e2e, multi-arch build.
2. Bump the five version fields.
3. Write the `fx_strategy/CHANGELOG.md` entry. Say what changed for a *user*,
   and note anything that changes a displayed figure.
4. Verify the migration chain applies cleanly from an older database, not only
   from empty.
5. Commit, tag `vX.Y.Z`, push.
6. Publish the GitHub release. The publish workflow builds and pushes
   `ghcr.io/<owner>/{arch}-addon-fx-strategy:X.Y.Z`.

## Before calling it stable

The specification's own bar, restated as a checklist:

- [ ] Migrations tested on an upgrade, not just a clean install.
- [ ] A backup restored into a fresh installation and the figures verified.
- [ ] Target alerts exercised under a fluctuating rate, including a dip past the
      hysteresis and a re-cross.
- [ ] Ingress verified on desktop and on a phone.
- [ ] No secret in any log, diagnostics bundle or backup.
- [ ] Coverage bars met: 85% overall, 95% on the financial modules.

## Prebuilt images

`config.yaml` has no `image:` key, so the Supervisor builds locally on install.
That keeps installation working without a published registry. To switch to
prebuilt images, add:

```yaml
image: ghcr.io/<owner>/{arch}-addon-fx-strategy
```

Only do this once the publish workflow has pushed images for every architecture
listed in `arch:`, or installation will fail for the missing ones.

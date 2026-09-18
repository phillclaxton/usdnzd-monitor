#!/usr/bin/env python3
"""Fail if the five version fields disagree with each other.

`docs/release-process.md` names five places the version appears, and says CI
fails the publish when they disagree. Only `config.yaml` was ever checked, so
four of the five could drift and the first sign of it would be a running
installation reporting a version it is not.
"""

from __future__ import annotations

import json
import re
import sys
import tomllib
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "fx_strategy/rootfs/app/backend"


def _search(path: Path, pattern: str) -> str:
    match = re.search(pattern, path.read_text())
    if match is None:
        raise SystemExit(f"{path}: no version found with {pattern!r}")
    return match.group(1)


def versions() -> dict[str, str]:
    return {
        "fx_strategy/config.yaml": yaml.safe_load(
            (ROOT / "fx_strategy/config.yaml").read_text()
        )["version"],
        "backend/pyproject.toml": tomllib.loads(
            (BACKEND / "pyproject.toml").read_text()
        )["project"]["version"],
        "backend/app/__init__.py": _search(
            BACKEND / "app/__init__.py", r'__version__ = "([^"]+)"'
        ),
        "backend/app/config.py": _search(
            BACKEND / "app/config.py", r'app_version: str = "([^"]+)"'
        ),
        "frontend/package.json": json.loads(
            (ROOT / "fx_strategy/rootfs/app/frontend/package.json").read_text()
        )["version"],
    }


def main(expected: str | None) -> int:
    found = versions()
    for where, version in found.items():
        print(f"{version}  {where}")

    distinct = set(found.values())
    if len(distinct) > 1:
        print(f"\nThe version fields disagree: {sorted(distinct)}", file=sys.stderr)
        return 1

    version = distinct.pop()
    if expected is not None and version != expected.removeprefix("v"):
        print(
            f"\nThe files say {version} but the release is {expected}", file=sys.stderr
        )
        return 1

    print(f"\nAll five agree on {version}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1] if len(sys.argv) > 1 else None))

"""Minimal .env loader (no python-dotenv dependency needed for two variables)."""
from __future__ import annotations

import os

_values: dict[str, str] = {}
_loaded = False


def load(path: str) -> None:
    global _loaded
    if _loaded:
        return
    _loaded = True

    if not os.path.isfile(path):
        return

    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            _values[key.strip()] = value.strip()


def get(key: str, default: str | None = None) -> str | None:
    if key in _values:
        return _values[key]
    return os.environ.get(key, default)

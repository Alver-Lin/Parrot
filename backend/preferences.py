"""Persisted app preferences under the data directory."""

from __future__ import annotations

import json
from pathlib import Path

from .config import DATA, MODEL_IDLE_RELEASE_SECONDS

PREFS_PATH = DATA / "preferences.json"
MIN_IDLE_MINUTES = 1
MAX_IDLE_MINUTES = 120
DEFAULT_IDLE_MINUTES = max(MIN_IDLE_MINUTES, MODEL_IDLE_RELEASE_SECONDS // 60)


def _clamp_minutes(value) -> int:
    try:
        minutes = int(value)
    except (TypeError, ValueError):
        minutes = DEFAULT_IDLE_MINUTES
    return max(MIN_IDLE_MINUTES, min(MAX_IDLE_MINUTES, minutes))


def load() -> dict:
    defaults = {"model_idle_minutes": DEFAULT_IDLE_MINUTES}
    try:
        raw = json.loads(PREFS_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return defaults
    if not isinstance(raw, dict):
        return defaults
    return {
        "model_idle_minutes": _clamp_minutes(raw.get("model_idle_minutes", DEFAULT_IDLE_MINUTES)),
    }


def save(prefs: dict) -> dict:
    current = load()
    if "model_idle_minutes" in prefs:
        current["model_idle_minutes"] = _clamp_minutes(prefs["model_idle_minutes"])
    PREFS_PATH.parent.mkdir(parents=True, exist_ok=True)
    PREFS_PATH.write_text(json.dumps(current, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return current


def model_idle_minutes() -> int:
    return load()["model_idle_minutes"]


def model_idle_seconds() -> int:
    return model_idle_minutes() * 60

"""User configuration: load/save/validate, with sane defaults for every field.

A malformed or missing optional field must never crash startup (spec
section 27) -- `load_config` always deep-merges onto DEFAULT_CONFIG and
logs a warning for anything it has to discard, rather than raising.
"""

from __future__ import annotations

import logging
import tomllib
from pathlib import Path
from typing import Any

import tomli_w

logger = logging.getLogger("qwerty_instrument.config")

APP_DIR_NAME = "qwerty_instrument"


def user_data_dir() -> Path:
    home = Path.home()
    return home / ".qwerty_instrument"


def default_config_path() -> Path:
    return user_data_dir() / "config.toml"


DEFAULT_CONFIG: dict[str, Any] = {
    "audio": {
        "sample_rate": 48000,
        "block_size": 256,
        "device_index": None,
        "use_wasapi_exclusive": False,
    },
    "keyboard": {
        "base_note": 48,
        "block2_offset": 12,
        "note_overrides": {},
        "control_overrides": {},
        "sustain_mode": "hold",
        "base_velocity": 0.85,
        "humanize_amount": 0.04,
    },
    "knob": {
        "mode": "pitch_bend",
        "bend_step_cents": 25.0,
        "max_bend_cents": 200.0,
        "expression_step": 0.08,
        "spring_return": True,
        "return_delay_s": 0.16,
        "return_duration_s": 0.22,
        "short_press_max_s": 0.5,
    },
    "instruments": {
        "default": "synth_lead",
        "cycle_order": ["synth_lead", "electric_piano", "guitar_lead"],
        "polyphony": {"synth_lead": 16, "electric_piano": 16, "guitar_lead": 6},
    },
    "practice": {
        "default_speed_percent": 100,
        "timing_window_perfect_ms": 40,
        "timing_window_good_ms": 90,
        "timing_window_late_ms": 150,
        "count_in_bars": 1,
        "metronome_volume": 0.5,
    },
    "ui": {
        "note_label_mode": "qwerty_and_note",  # qwerty_only | note_only | qwerty_and_note
        "theme": "dark",
    },
    "paths": {
        "songs_dir": "songs",
        "presets_dir": "presets",
        "exports_dir": "exports",
        "recordings_dir": "recordings",
        "sessions_db": "sessions/practice_sessions.sqlite3",
    },
}


def _deep_merge(base: dict, override: dict, path: str = "") -> dict:
    result = dict(base)
    for key, value in override.items():
        full_path = f"{path}.{key}" if path else key
        if key not in base:
            logger.warning("config: unknown key %r ignored", full_path)
            continue
        if isinstance(base[key], dict) and isinstance(value, dict):
            result[key] = _deep_merge(base[key], value, full_path)
        elif base[key] is not None and value is not None and not isinstance(value, type(base[key])):
            # allow int -> float widening and vice versa; otherwise reject silently
            if isinstance(value, (int, float)) and isinstance(base[key], (int, float)):
                result[key] = value
            else:
                logger.warning("config: %r has wrong type (%s), keeping default", full_path, type(value).__name__)
        else:
            result[key] = value
    return result


def load_config(path: Path | None = None) -> dict[str, Any]:
    path = path or default_config_path()
    if not path.exists():
        logger.info("no config file at %s, using defaults", path)
        return _deep_copy(DEFAULT_CONFIG)
    try:
        with open(path, "rb") as f:
            raw = tomllib.load(f)
    except Exception as exc:
        logger.warning("failed to parse config %s (%s); using defaults", path, exc)
        return _deep_copy(DEFAULT_CONFIG)
    try:
        return _deep_merge(DEFAULT_CONFIG, raw)
    except Exception as exc:
        logger.warning("failed to merge config %s (%s); using defaults", path, exc)
        return _deep_copy(DEFAULT_CONFIG)


def _strip_none(d: dict) -> dict:
    """TOML has no null literal, so a field left at its Python `None`
    default (e.g. audio.device_index meaning "use system default") would
    make tomli_w raise. Omitting it from the written file is equivalent:
    load_config() deep-merges onto DEFAULT_CONFIG, which restores the same
    None for any field absent from the file.
    """
    out = {}
    for k, v in d.items():
        if v is None:
            continue
        out[k] = _strip_none(v) if isinstance(v, dict) else v
    return out


def save_config(data: dict[str, Any], path: Path | None = None) -> None:
    path = path or default_config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "wb") as f:
        tomli_w.dump(_strip_none(data), f)
    logger.info("config saved to %s", path)


def _deep_copy(d: dict) -> dict:
    out = {}
    for k, v in d.items():
        out[k] = _deep_copy(v) if isinstance(v, dict) else (list(v) if isinstance(v, list) else v)
    return out

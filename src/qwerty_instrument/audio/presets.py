"""Load instrument presets (presets/*.json) and apply them to a running
InstrumentBackend via its existing set_param() interface -- no separate
patch-application code path to keep in sync.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path

from .voices import InstrumentBackend

logger = logging.getLogger("qwerty_instrument.audio.presets")


@dataclass
class Preset:
    name: str
    instrument: str
    description: str
    params: dict


def load_preset(path: Path) -> Preset | None:
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        return Preset(name=data["name"], instrument=data["instrument"], description=data.get("description", ""), params=data.get("params", {}))
    except Exception as exc:
        logger.warning("failed to load preset %s: %s", path, exc)
        return None


def list_presets(presets_dir: Path) -> list[Preset]:
    presets_dir = Path(presets_dir)
    if not presets_dir.exists():
        return []
    out = []
    for p in sorted(presets_dir.glob("*.json")):
        preset = load_preset(p)
        if preset is not None:
            out.append(preset)
    return out


def apply_preset(backend: InstrumentBackend, preset: Preset) -> bool:
    if backend.name != preset.instrument:
        logger.warning("preset %r targets %r, not %r; not applied", preset.name, preset.instrument, backend.name)
        return False
    for key, value in preset.params.items():
        backend.set_param(key, value)
    return True

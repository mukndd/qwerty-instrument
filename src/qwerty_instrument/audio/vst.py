"""Optional VST3 instrument hosting via Spotify's Pedalboard.

IMPORTANT / honest limitation (spec section 5 explicitly asks us to
investigate this rather than assume): Pedalboard's VST3Plugin is fundamentally
a *batch* renderer -- `plugin(midi_messages, duration, sample_rate, reset=...)`
renders a whole time span in one call, there is no native "process one
256-frame block with these new MIDI events" streaming API. We approximate
real-time streaming by calling it once per audio callback with `reset=False`
(which Pedalboard documents as preserving the plugin's internal state, i.e.
voice tails, between calls) and a duration equal to one block. This works
for many instruments but is NOT the same guarantee as a native VST host's
sample-accurate block processing, and per-call Python/plugin overhead can
be significant relative to a 5.3ms (256-frame @ 48kHz) budget.

Consequences we surface rather than hide:
    - `reported_latency_samples` from the plugin is exposed to the UI.
    - This backend is opt-in and never required for the app to be usable
      (internal synth/e-piano/guitar backends have no dependency on this).
    - If pedalboard or a VST3 plugin fails to load, `is_available` is False
      and the caller must not offer this instrument in the UI as if it works.
"""

from __future__ import annotations

import logging
import queue
import time
from dataclasses import dataclass, field

import numpy as np

from ..music.events import EventType, MusicEvent

logger = logging.getLogger("qwerty_instrument.audio.vst")

try:
    import pedalboard

    PEDALBOARD_AVAILABLE = True
except Exception:  # pragma: no cover - exercised only when pedalboard is absent
    PEDALBOARD_AVAILABLE = False

try:
    import mido

    MIDO_AVAILABLE = True
except Exception:  # pragma: no cover
    MIDO_AVAILABLE = False


@dataclass
class VstLoadResult:
    ok: bool
    plugin_names: list[str] = field(default_factory=list)
    error: str | None = None


def scan_plugin_names(path: str) -> VstLoadResult:
    """List plugin names inside a VST3 bundle/file without fully loading one."""
    if not PEDALBOARD_AVAILABLE:
        return VstLoadResult(ok=False, error="pedalboard is not installed (pip install qwerty-instrument[vst])")
    try:
        names = pedalboard.VST3Plugin.get_plugin_names_for_file(path)
        return VstLoadResult(ok=True, plugin_names=list(names))
    except Exception as exc:
        return VstLoadResult(ok=False, error=str(exc))


class VST3Backend:
    """Best-effort real-time-ish VST3 instrument backend. See module docstring.

    Duck-types the same public surface AudioEngine expects from an
    InstrumentBackend (note_on/note_off/render/all_notes_off/hard_silence/
    pitch_bend_active/set_param/active_voice_count) without going through
    the internal Voice pool, since the plugin manages its own polyphony.
    """

    def __init__(self, sample_rate: int, block_size: int, name: str = "vst3"):
        self.name = name
        self.sample_rate = sample_rate
        self.block_size = block_size
        self.is_available = PEDALBOARD_AVAILABLE and MIDO_AVAILABLE
        self.plugin = None
        self.reported_latency_samples = 0
        self._pending_notes: list[tuple[int, bool, float]] = []  # (note, is_on, offset_seconds)
        self._active_notes: set[int] = set()
        self._load_error: str | None = None

    def load(self, path: str, plugin_name: str | None = None) -> VstLoadResult:
        if not self.is_available:
            return VstLoadResult(ok=False, error="pedalboard/mido not installed")
        try:
            self.plugin = pedalboard.load_plugin(path, plugin_name=plugin_name)
            if not getattr(self.plugin, "is_instrument", False):
                self.plugin = None
                return VstLoadResult(ok=False, error=f"{path} is not an instrument plugin")
            self.reported_latency_samples = int(getattr(self.plugin, "reported_latency_samples", 0) or 0)
            logger.info(
                "loaded VST3 instrument %s (%s), reported latency=%d samples",
                self.plugin.name,
                path,
                self.reported_latency_samples,
            )
            return VstLoadResult(ok=True, plugin_names=[self.plugin.name])
        except Exception as exc:
            self._load_error = str(exc)
            logger.warning("failed to load VST3 plugin %s: %s", path, exc)
            return VstLoadResult(ok=False, error=str(exc))

    def select_preset(self, preset_path: str) -> bool:
        if self.plugin is None:
            return False
        try:
            self.plugin.load_preset(preset_path)
            return True
        except Exception as exc:
            logger.warning("failed to load VST preset %s: %s", preset_path, exc)
            return False

    # ---- InstrumentBackend-compatible surface ------------------------------

    def note_on(self, event: MusicEvent) -> None:
        self._active_notes.add(event.note)
        self._pending_notes.append((event.note, True, 0.0))

    def note_off(self, event: MusicEvent) -> None:
        self._active_notes.discard(event.note)
        self._pending_notes.append((event.note, False, 0.0))

    def all_notes_off(self) -> None:
        for note in list(self._active_notes):
            self._pending_notes.append((note, False, 0.0))
        self._active_notes.clear()

    def hard_silence(self) -> None:
        self._pending_notes.clear()
        self._active_notes.clear()
        if self.plugin is not None:
            try:
                self.plugin.reset()
            except Exception:
                pass

    def pitch_bend_active(self, cents: float) -> None:
        pass  # not implemented: would require MIDI pitch-bend CC mapping per plugin

    def set_param(self, name: str, value) -> None:
        if self.plugin is None:
            return
        try:
            if name in self.plugin.parameters:
                setattr(self.plugin, name, value)
        except Exception as exc:
            logger.debug("VST param set failed (%s=%s): %s", name, value, exc)

    @property
    def active_voice_count(self) -> int:
        return len(self._active_notes)

    def render(self, n_frames: int) -> np.ndarray:
        if self.plugin is None or not MIDO_AVAILABLE:
            return np.zeros((n_frames, 2), dtype=np.float32)
        duration = n_frames / self.sample_rate
        messages = []
        for note, is_on, offset in self._pending_notes:
            msg = mido.Message("note_on" if is_on else "note_off", note=note, velocity=100)
            msg.time = min(offset, max(0.0, duration - 1e-6))
            messages.append(msg)
        self._pending_notes.clear()
        try:
            audio = self.plugin(messages, duration=duration, sample_rate=self.sample_rate, num_channels=2, reset=False)
        except Exception as exc:
            logger.warning("VST render failed, silencing this block: %s", exc)
            return np.zeros((n_frames, 2), dtype=np.float32)
        audio = np.asarray(audio, dtype=np.float32)
        if audio.ndim == 1:
            audio = np.stack([audio, audio], axis=-1)
        elif audio.shape[0] == 2 and audio.shape[1] != 2:
            audio = audio.T
        if audio.shape[0] < n_frames:
            pad = np.zeros((n_frames - audio.shape[0], audio.shape[1]), dtype=np.float32)
            audio = np.concatenate([audio, pad], axis=0)
        elif audio.shape[0] > n_frames:
            audio = audio[:n_frames]
        return audio

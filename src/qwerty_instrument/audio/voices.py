"""Voice lifecycle management shared by all instrument backends.

This is where the "no stuck notes" guarantee (spec section 37) actually
lives: every active voice is owned by the exact (source, channel, key_id)
identity of the event that started it (`MusicEvent.voice_id`), so a
NOTE_OFF can only release the voice it actually belongs to -- not some
other voice that happens to share the same pitch. `all_notes_off()` is the
unconditional escape hatch used by panic, mode switches, instrument
switches, and shutdown.
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod

import numpy as np

from ..music.events import MusicEvent
from ..music.notes import midi_to_freq

logger = logging.getLogger("qwerty_instrument.audio")


class Voice(ABC):
    """One sounding note. Subclassed per instrument (synth/eplano/guitar)."""

    def __init__(self, sample_rate: int, block_size: int):
        self.sample_rate = sample_rate
        self.block_size = block_size
        self.note = 0
        self.velocity = 1.0
        self.bend_cents = 0.0

    @abstractmethod
    def start(self, note: int, velocity: float) -> None: ...

    @abstractmethod
    def release(self) -> None: ...

    def panic_release(self) -> None:
        """Fast, unconditional release for panic/mode-switch/shutdown.

        Default is the same as a normal release(); voices whose natural
        release can take multiple seconds (e.g. a lightly-damped plucked
        string) should override this with something that reaches silence
        in well under a second, since panic is meant to be an emergency
        "stop the noise" control, not a musical release.
        """
        self.release()

    @abstractmethod
    def render(self, n_frames: int) -> np.ndarray:
        """Return a mono float32 buffer of length n_frames."""

    @property
    @abstractmethod
    def finished(self) -> bool:
        """True once the voice has fully decayed and its slot can be reused."""

    def set_bend_cents(self, cents: float) -> None:
        self.bend_cents = cents

    def current_freq(self) -> float:
        return midi_to_freq(self.note, self.bend_cents)


class InstrumentBackend(ABC):
    """Common note-on/off/panic bookkeeping over a fixed voice pool.

    Subclasses implement `_create_voice()` and `_mix(voices, n_frames)`.
    """

    name = "abstract"

    def __init__(self, sample_rate: int, block_size: int, polyphony: int = 16):
        self.sample_rate = sample_rate
        self.block_size = block_size
        self.polyphony = polyphony
        self._free: list[Voice] = [self._create_voice() for _ in range(polyphony)]
        self._active: list[Voice] = []
        self._owned: dict[tuple, Voice] = {}
        self._voice_order: list[Voice] = []  # oldest-first, for voice stealing

    @abstractmethod
    def _create_voice(self) -> Voice: ...

    def note_on(self, event: MusicEvent) -> None:
        vid = event.voice_id
        if vid in self._owned:
            return  # defensive: input layer already suppresses OS key-repeat
        if self._free:
            voice = self._free.pop()
        else:
            voice = self._steal_voice()
            if voice is None:
                return
        voice.start(event.note, event.velocity)
        self._owned[vid] = voice
        if voice not in self._active:
            self._active.append(voice)
        self._voice_order.append(voice)

    def note_off(self, event: MusicEvent) -> None:
        vid = event.voice_id
        voice = self._owned.pop(vid, None)
        if voice is not None:
            voice.release()

    def pitch_bend_active(self, cents: float) -> None:
        for v in self._active:
            v.set_bend_cents(cents)

    def all_notes_off(self) -> None:
        for v in self._active:
            v.panic_release()
        self._owned.clear()

    def hard_silence(self) -> None:
        """Immediate, unconditional silence -- used on shutdown/device change."""
        self._active.clear()
        self._owned.clear()
        self._voice_order.clear()
        self._free = [self._create_voice() for _ in range(self.polyphony)]

    def _steal_voice(self) -> Voice | None:
        if not self._voice_order:
            return None
        oldest = self._voice_order.pop(0)
        for vid, v in list(self._owned.items()):
            if v is oldest:
                del self._owned[vid]
        logger.debug("%s: voice stolen (polyphony=%d)", self.name, self.polyphony)
        return oldest

    def render(self, n_frames: int) -> np.ndarray:
        if not self._active:
            return np.zeros((n_frames, 2), dtype=np.float32)
        rendered = []
        still_active = []
        for v in self._active:
            buf = v.render(n_frames)
            if v.finished:
                if v in self._voice_order:
                    self._voice_order.remove(v)
                self._free.append(v)
            else:
                still_active.append(v)
                rendered.append(buf)
        self._active = still_active
        if not rendered:
            return np.zeros((n_frames, 2), dtype=np.float32)
        mono = np.sum(rendered, axis=0).astype(np.float32)
        return self._mix(mono, n_frames)

    def _mix(self, mono: np.ndarray, n_frames: int) -> np.ndarray:
        """Default: duplicate mono to stereo. Override for stereo width/FX."""
        stereo = np.empty((n_frames, 2), dtype=np.float32)
        stereo[:, 0] = mono
        stereo[:, 1] = mono
        return stereo

    def set_param(self, name: str, value) -> None:
        """Override in subclasses for instrument-specific params."""

    @property
    def active_voice_count(self) -> int:
        return len(self._active)

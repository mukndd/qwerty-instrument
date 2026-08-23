"""Dedicated bass instrument: sine sub-oscillator + a quiet filtered
harmonic layer, heavily low-pass filtered, no chorus/delay/reverb/stereo
spread. Distinct from the chord synth on purpose (spec: bass and chords
must not just be "the same synth playing a low note") -- reuses the same
Voice/InstrumentBackend/ADSR/StateVariableFilter building blocks as
SynthLead rather than inventing a second audio architecture.
"""

from __future__ import annotations

import numpy as np

from . import effects
from .voices import InstrumentBackend, Voice

TWO_PI = 2.0 * np.pi

DEFAULT_BASS_PATCH = {
    "sub_level": 0.75,       # pure sine at the fundamental -- the "weight"
    "harmonic_level": 0.25,  # softened sawtooth at the fundamental -- adds definition without brightness
    "attack": 0.008,
    "decay": 0.05,
    "sustain": 0.85,
    "release": 0.12,
    "cutoff": 480.0,         # aggressive low-pass -- this is what keeps it "warm/rounded", not "clicky"
    "resonance": 0.05,
    "filter_env_amount": 0.08,
    "master_gain": 0.65,
}


class BassVoice(Voice):
    def __init__(self, sample_rate: int, block_size: int, patch: dict):
        super().__init__(sample_rate, block_size)
        self.patch = patch
        self.env = effects.ADSR()
        self.filter = effects.StateVariableFilter(sample_rate)
        self._phase_sub = 0.0
        self._phase_harm = 0.0

    def start(self, note: int, velocity: float) -> None:
        self.note = note
        self.velocity = velocity
        self.bend_cents = 0.0
        p = self.patch
        self.env.attack = p["attack"]
        self.env.decay = p["decay"]
        self.env.sustain = p["sustain"]
        self.env.release = p["release"]
        self.env.note_on()
        self.filter.reset()

    def release(self) -> None:
        self.env.note_off()

    def render(self, n_frames: int) -> np.ndarray:
        p = self.patch
        freq = self.current_freq()
        inc = TWO_PI * freq / self.sample_rate

        phases_sub = self._phase_sub + inc * np.arange(n_frames, dtype=np.float64)
        self._phase_sub = float((self._phase_sub + inc * n_frames) % TWO_PI)
        sub = np.sin(phases_sub).astype(np.float32)

        phases_h = self._phase_harm + inc * np.arange(n_frames, dtype=np.float64)
        self._phase_harm = float((self._phase_harm + inc * n_frames) % TWO_PI)
        ph = np.mod(phases_h, TWO_PI)
        saw = (2.0 * (ph / TWO_PI) - 1.0).astype(np.float32)

        mix = sub * p["sub_level"] + saw * p["harmonic_level"]
        env = self.env.process(n_frames, self.sample_rate)
        vel_gain = 0.5 + 0.5 * self.velocity
        mix = mix * env * vel_gain

        env_mean = float(np.mean(env)) if n_frames else 0.0
        cutoff = p["cutoff"] * (1.0 - p["filter_env_amount"] + p["filter_env_amount"] * env_mean)
        mix = self.filter.process(mix, cutoff, p["resonance"])
        return mix

    @property
    def finished(self) -> bool:
        return self.env.finished


class BassSynth(InstrumentBackend):
    name = "bass_synth"

    def __init__(self, sample_rate: int, block_size: int, polyphony: int = 6, patch: dict | None = None):
        self.patch = dict(DEFAULT_BASS_PATCH)
        if patch:
            self.patch.update(patch)
        super().__init__(sample_rate, block_size, polyphony)

    def _create_voice(self) -> Voice:
        return BassVoice(self.sample_rate, self.block_size, self.patch)

    def set_param(self, name: str, value) -> None:
        self.patch[name] = value

    def _mix(self, mono: np.ndarray, n_frames: int) -> np.ndarray:
        # Deliberately no chorus/delay/reverb/stereo widening -- a bass that
        # stays centered and dry is part of "warm, rounded, not clicky".
        gain = self.patch["master_gain"]
        stereo = np.stack([mono, mono], axis=1) * gain
        return stereo.astype(np.float32)

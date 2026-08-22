"""Karplus-Strong plucked-string guitar backend.

Not a physical model of a real guitar -- an extended Karplus-Strong
synthesis (Jaffe & Smith-style: fractional delay + one-pole loop damping)
tuned to sit well under the FX chain in section 44 of the spec. Pitch bend
works by continuously varying the (fractional) delay-line length, which is
why this voice runs a genuine per-sample loop rather than vectorized numpy
ops -- the delay length is the pitch period itself, usually well under one
audio block at musical pitches, so it cannot be vectorized like the
independent Delay effect can. Real measured per-voice cost is reported by
tools/audio_diagnostics.py; MAX polyphony below is a conservative default
pending that measurement on the user's machine.
"""

from __future__ import annotations

import numpy as np

from . import effects
from .voices import InstrumentBackend, Voice

MAX_VOICE_SECONDS = 6.0

DEFAULT_GUITAR_PATCH = {
    "damping": 0.4,            # loop lowpass amount: 0=bright/metallic, ~0.6=dark/muted
    "sustain_decay": 0.985,    # per-sample feedback gain: closer to 1.0 = longer sustain.
    # Tuned so the lowest supported note (~E2) reaches silence in ~3s after
    # release -- long enough to let a lead line ring naturally, short
    # enough that the voice pool reliably recovers under fast playing
    # (measured empirically; see tests/test_voices.py).
    "release_time": 0.35,      # seconds for extra damping to ramp in after note-off
    "pick_noise_color": 0.5,   # 0=harsh white pick noise, 1=soft/filtered pluck
    "drive": 0.18,
    "compressor_threshold_db": -20.0,
    "compressor_ratio": 3.0,
    "chorus_mix": 0.25,
    "delay_enabled": True,
    "delay_seconds": 0.32,
    "delay_feedback": 0.3,
    "delay_mix": 0.2,
    "reverb_enabled": True,
    "reverb_mix": 0.22,
    "reverb_decay": 0.5,
    "master_gain": 0.6,
    # Expression macro (F75 knob "guitar expression" mode, section 10):
    # low value = cleaner/darker/calmer, high value = brighter/more driven.
    "expression": 0.3,
}


class GuitarVoice(Voice):
    def __init__(self, sample_rate: int, block_size: int, patch: dict):
        super().__init__(sample_rate, block_size)
        self.patch = patch
        self._buf_len = sample_rate  # 1s ring buffer; covers pitches well below MIDI range
        self._buf = np.zeros(self._buf_len, dtype=np.float64)
        self._write = 0
        self._damp_state = 0.0
        self._current_period = 200.0
        self._age = 0
        self._releasing = False
        self._panic = False
        self._release_start_age = 0
        self._peak = 0.0
        self._started = False

    def start(self, note: int, velocity: float) -> None:
        self.note = note
        self.velocity = velocity
        self.bend_cents = 0.0
        p = self.patch
        freq = self.current_freq()
        period = self.sample_rate / max(20.0, freq)
        m = max(2, int(round(period)))
        rng = np.random.default_rng()
        noise = rng.uniform(-1.0, 1.0, size=m)
        color = p["pick_noise_color"]
        if color > 0:
            kernel_len = 1 + int(color * 6)
            kernel = np.ones(kernel_len) / kernel_len
            noise = np.convolve(noise, kernel, mode="same")
        amp = 0.5 + 0.5 * velocity
        idx = (self._write + np.arange(m)) % self._buf_len
        self._buf[idx] = noise * amp
        # Advance the write head past the just-primed region so the delay
        # tap (which trails `write` by `period`) reads back into the burst
        # instead of into silence elsewhere in the ring buffer.
        self._write = (self._write + m) % self._buf_len
        self._damp_state = 0.0
        self._current_period = period
        self._age = 0
        self._releasing = False
        self._panic = False
        self._peak = amp
        self._started = True

    def release(self) -> None:
        self._releasing = True
        self._release_start_age = self._age

    def panic_release(self) -> None:
        """Fast emergency silence: cut buffer energy now, then decay hard.

        A plucked string's natural release (governed by `damping`/
        `sustain_decay`) can musically take several seconds, which is
        appropriate for note-off but far too slow for panic. We scale the
        whole ring buffer down immediately (a smooth amplitude scale, not
        a hard zero, so it doesn't introduce a harsh discontinuity) and
        switch to aggressive per-sample damping/decay for what's left.
        """
        self._releasing = True
        self._release_start_age = self._age
        self._panic = True
        self._buf *= 0.03
        self._peak *= 0.03

    def render(self, n_frames: int) -> np.ndarray:
        p = self.patch
        buf = self._buf
        blen = self._buf_len
        w = self._write
        damp_state = self._damp_state
        if self._panic:
            base_damping = 0.88
            decay = 0.85
            release_time = 0.05
        else:
            base_damping = p["damping"]
            decay = p["sustain_decay"]
            release_time = max(0.02, p["release_time"])

        target_period = self.sample_rate / max(20.0, self.current_freq())
        period_start = self._current_period
        if abs(target_period - period_start) > 1e-9:
            periods = np.linspace(period_start, target_period, n_frames)
        else:
            periods = np.full(n_frames, target_period)

        out = np.empty(n_frames, dtype=np.float64)
        age0 = self._age
        for i in range(n_frames):
            per = periods[i]
            read_pos = (w - per) % blen
            i0 = int(read_pos)
            frac = read_pos - i0
            i1 = (i0 + 1) % blen
            delayed = buf[i0] * (1.0 - frac) + buf[i1] * frac

            damping = base_damping
            if self._releasing:
                rel_elapsed = (age0 + i - self._release_start_age) / self.sample_rate
                extra = min(1.0, rel_elapsed / release_time)
                damping = base_damping + (1.0 - base_damping) * 0.6 * extra

            damp_state = damp_state * damping + delayed * (1.0 - damping)
            fed = damp_state * decay
            buf[w] = fed
            out[i] = fed
            w = (w + 1) % blen

        self._write = w
        self._damp_state = damp_state
        self._current_period = float(periods[-1])
        self._age += n_frames
        self._peak = float(np.max(np.abs(out))) if out.size else 0.0
        return out.astype(np.float32)

    @property
    def finished(self) -> bool:
        if not self._started:
            return True
        if self._age > MAX_VOICE_SECONDS * self.sample_rate:
            return True
        min_release_samples = 0.05 * self.sample_rate
        if self._releasing and (self._age - self._release_start_age) > min_release_samples:
            return self._peak < 1e-3
        return False


class GuitarLead(InstrumentBackend):
    name = "guitar_lead"

    def __init__(self, sample_rate: int, block_size: int, polyphony: int = 6, patch: dict | None = None):
        self.patch = dict(DEFAULT_GUITAR_PATCH)
        if patch:
            self.patch.update(patch)
        super().__init__(sample_rate, block_size, polyphony)
        self._compressor = effects.Compressor(
            threshold_db=self.patch["compressor_threshold_db"], ratio=self.patch["compressor_ratio"]
        )
        self._chorus_l = effects.Chorus(sample_rate, rate_hz=0.5, base_ms=10.0)
        self._chorus_r = effects.Chorus(sample_rate, rate_hz=0.66, base_ms=13.0)
        self._delay = effects.Delay(sample_rate)
        self._delay.set_delay_seconds(self.patch["delay_seconds"], block_size)
        self._delay.feedback = self.patch["delay_feedback"]
        self._delay.mix = self.patch["delay_mix"]
        self._reverb = effects.SchroederReverb(sample_rate)
        self._reverb.mix = self.patch["reverb_mix"]
        self._reverb.set_decay(self.patch["reverb_decay"])

    def _create_voice(self) -> Voice:
        return GuitarVoice(self.sample_rate, self.block_size, self.patch)

    def set_param(self, name: str, value) -> None:
        self.patch[name] = value
        if name == "delay_seconds":
            self._delay.set_delay_seconds(value, self.block_size)
        elif name == "delay_feedback":
            self._delay.feedback = value
        elif name == "delay_mix":
            self._delay.mix = value
        elif name == "reverb_mix":
            self._reverb.mix = value
        elif name == "reverb_decay":
            self._reverb.set_decay(value)
        elif name == "expression":
            self.apply_expression_macro(value)

    def apply_expression_macro(self, value: float) -> None:
        """One knob value -> coordinated brightness/drive/vibrato/delay (spec section 10).

        Deliberately gentle: low = cleaner/darker/calmer, high = brighter
        and a bit more driven, never comically distorted.
        """
        value = max(0.0, min(1.0, value))
        self.patch["expression"] = value
        self.patch["damping"] = 0.55 - 0.32 * value       # brighter as value rises
        self.patch["drive"] = 0.08 + 0.22 * value
        self.patch["delay_mix"] = 0.12 + 0.14 * value
        self._delay.mix = self.patch["delay_mix"]

    def _mix(self, mono: np.ndarray, n_frames: int) -> np.ndarray:
        p = self.patch
        block_seconds = n_frames / self.sample_rate
        x = self._compressor.process(mono, self.sample_rate, block_seconds)
        x = effects.saturate(x, p["drive"])
        if p["delay_enabled"]:
            x = self._delay.process(x)
        if p["reverb_enabled"]:
            x = self._reverb.process(x)
        left = self._chorus_l.process(x)
        right = self._chorus_r.process(x)
        gain = p["master_gain"]
        stereo = np.stack([left, right], axis=1) * gain
        return stereo.astype(np.float32)

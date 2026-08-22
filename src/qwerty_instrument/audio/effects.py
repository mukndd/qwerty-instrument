"""Reusable DSP building blocks used by instruments and the master FX chain.

Everything here is block-based (`process(x: np.ndarray[n_frames]) -> np.ndarray`)
and carries its own state between calls, so effects tails (delay/reverb) stay
continuous across notes instead of resetting every block (spec section 44).

No allocation-heavy or blocking work happens here beyond simple numpy array
ops sized to the block -- this code runs inside the real-time audio callback.
"""

from __future__ import annotations

import numpy as np


class ADSR:
    """Linear-segment attack/decay/sustain/release envelope.

    Advances in blocks using np.linspace rather than a per-sample Python
    loop. A stage (e.g. a 180ms release) almost always spans multiple
    audio blocks, so progress within a stage is tracked as
    elapsed/total samples *across calls*, not recomputed fresh each call
    -- an earlier version recomputed a full-duration ramp capped to the
    current block's frame count every call, which reached the target
    level correctly but never advanced its own "stage complete" counter,
    leaving released voices stuck in RELEASE forever and never freed back
    to the voice pool.
    """

    __slots__ = (
        "attack",
        "decay",
        "sustain",
        "release",
        "_level",
        "_stage",
        "_stage_start_level",
        "_stage_target_level",
        "_stage_duration",
        "_stage_total_samples",
        "_stage_elapsed_samples",
    )

    IDLE, ATTACK, DECAY, SUSTAIN, RELEASE = range(5)

    def __init__(self, attack: float = 0.005, decay: float = 0.08, sustain: float = 0.7, release: float = 0.15):
        self.attack = max(0.0005, attack)
        self.decay = max(0.0005, decay)
        self.sustain = max(0.0, min(1.0, sustain))
        self.release = max(0.0005, release)
        self._level = 0.0
        self._stage = self.IDLE
        self._stage_start_level = 0.0
        self._stage_target_level = 0.0
        self._stage_duration = 0.0
        self._stage_total_samples: int | None = None
        self._stage_elapsed_samples = 0

    def note_on(self) -> None:
        self._begin_stage(self.ATTACK)

    def note_off(self) -> None:
        if self._stage != self.IDLE:
            self._begin_stage(self.RELEASE)

    def _begin_stage(self, stage: int) -> None:
        self._stage = stage
        self._stage_start_level = self._level
        if stage == self.ATTACK:
            self._stage_target_level = 1.0
            self._stage_duration = self.attack
        elif stage == self.DECAY:
            self._stage_target_level = self.sustain
            self._stage_duration = self.decay
        elif stage == self.RELEASE:
            self._stage_target_level = 0.0
            self._stage_duration = self.release
        else:
            self._stage_duration = 0.0
        self._stage_total_samples = None  # computed lazily in process(), which knows sample_rate
        self._stage_elapsed_samples = 0

    @property
    def finished(self) -> bool:
        return self._stage == self.IDLE

    @property
    def active(self) -> bool:
        return self._stage != self.IDLE

    def process(self, n_frames: int, sample_rate: int) -> np.ndarray:
        out = np.empty(n_frames, dtype=np.float32)
        pos = 0
        while pos < n_frames:
            remaining = n_frames - pos
            if self._stage == self.IDLE:
                out[pos:] = 0.0
                break
            if self._stage == self.SUSTAIN:
                out[pos:] = self.sustain
                self._level = self.sustain
                break

            if self._stage_total_samples is None:
                self._stage_total_samples = max(1, int(self._stage_duration * sample_rate))

            seg_remaining = self._stage_total_samples - self._stage_elapsed_samples
            seg_frames = max(1, min(seg_remaining, remaining))
            frac_start = self._stage_elapsed_samples / self._stage_total_samples
            frac_end = (self._stage_elapsed_samples + seg_frames) / self._stage_total_samples
            span = self._stage_target_level - self._stage_start_level
            start_level = self._stage_start_level + span * frac_start
            end_level = self._stage_start_level + span * frac_end
            ramp = np.linspace(start_level, end_level, seg_frames, endpoint=True, dtype=np.float32)
            out[pos:pos + seg_frames] = ramp
            self._level = end_level
            self._stage_elapsed_samples += seg_frames
            pos += seg_frames

            if self._stage_elapsed_samples >= self._stage_total_samples:
                if self._stage == self.ATTACK:
                    self._begin_stage(self.DECAY)
                elif self._stage == self.DECAY:
                    if self.sustain > 0:
                        self._stage = self.SUSTAIN
                        self._level = self.sustain
                    else:
                        self._stage = self.IDLE
                        self._level = 0.0
                elif self._stage == self.RELEASE:
                    self._stage = self.IDLE
                    self._level = 0.0
        return out


class StateVariableFilter:
    """Chamberlin state-variable low-pass filter (per-voice, per-sample recursion).

    This is the one place in the engine that runs a genuine per-sample
    Python loop rather than a vectorized block op, because the low-pass
    output at sample n depends recursively on n-1. Measured cost is
    reported by tools/audio_diagnostics.py; polyphony limits are tuned
    against that measurement (see ARCHITECTURE.md).
    """

    __slots__ = ("_low", "_band", "sample_rate")

    def __init__(self, sample_rate: int):
        self.sample_rate = sample_rate
        self._low = 0.0
        self._band = 0.0

    def reset(self) -> None:
        self._low = 0.0
        self._band = 0.0

    def process(self, x: np.ndarray, cutoff_hz: float, resonance: float) -> np.ndarray:
        n = x.shape[0]
        out = np.empty(n, dtype=np.float32)
        cutoff_hz = max(20.0, min(cutoff_hz, self.sample_rate * 0.45))
        f = 2.0 * np.sin(np.pi * cutoff_hz / self.sample_rate)
        q = max(0.0001, 1.0 - min(0.98, resonance))
        low = self._low
        band = self._band
        xv = x
        for i in range(n):
            high = xv[i] - low - q * band
            band = band + f * high
            low = low + f * band
            out[i] = low
        self._low = low
        self._band = band
        return out


class Delay:
    """Feedback delay line. Vectorized per block; requires delay >= block size.

    Musically useful delay times (tens to hundreds of ms) always satisfy
    this at any sane block size (64-256 frames @ 48kHz = 1.3-5.3ms), so the
    engine clamps delay_seconds up to the current block period on set().
    """

    def __init__(self, sample_rate: int, max_delay_seconds: float = 2.0):
        self.sample_rate = sample_rate
        self._buf = np.zeros(int(max_delay_seconds * sample_rate) + 8, dtype=np.float32)
        self._write = 0
        self.delay_samples = int(0.3 * sample_rate)
        self.feedback = 0.3
        self.mix = 0.25

    def set_delay_seconds(self, seconds: float, min_block: int) -> None:
        samples = int(seconds * self.sample_rate)
        self.delay_samples = max(min_block + 1, min(samples, len(self._buf) - 8))

    def process(self, x: np.ndarray) -> np.ndarray:
        n = x.shape[0]
        buf = self._buf
        blen = len(buf)
        write = self._write
        read_start = (write - self.delay_samples) % blen
        idx = (read_start + np.arange(n)) % blen
        delayed = buf[idx]
        wet = x + self.feedback * delayed
        write_idx = (write + np.arange(n)) % blen
        buf[write_idx] = wet
        self._write = (write + n) % blen
        return x * (1.0 - self.mix) + delayed * self.mix


class SchroederReverb:
    """Small parallel-comb + series-allpass reverb. Cheap, continuous tail."""

    def __init__(self, sample_rate: int):
        self.sample_rate = sample_rate
        comb_ms = [29.7, 37.1, 41.1, 43.7]
        self._combs = [Delay(sample_rate, max_delay_seconds=0.2) for _ in comb_ms]
        for c, ms in zip(self._combs, comb_ms):
            c.set_delay_seconds(ms / 1000.0, min_block=1)
            c.feedback = 0.55
            c.mix = 1.0
        allpass_ms = [5.0, 1.7]
        self._allpasses = [Delay(sample_rate, max_delay_seconds=0.05) for _ in allpass_ms]
        for a, ms in zip(self._allpasses, allpass_ms):
            a.set_delay_seconds(ms / 1000.0, min_block=1)
            a.feedback = 0.5
            a.mix = 1.0
        self.mix = 0.2
        self.decay = 0.55

    def set_decay(self, decay: float) -> None:
        self.decay = max(0.1, min(0.95, decay))
        for c in self._combs:
            c.feedback = self.decay

    def process(self, x: np.ndarray) -> np.ndarray:
        acc = np.zeros_like(x)
        for c in self._combs:
            acc += c.process(x)
        acc /= len(self._combs)
        wet = acc
        for a in self._allpasses:
            wet = a.process(wet)
        return x * (1.0 - self.mix) + wet * self.mix


class Chorus:
    """Short modulated delay for stereo width / chorus coloration."""

    def __init__(self, sample_rate: int, rate_hz: float = 0.6, depth_ms: float = 4.0, base_ms: float = 12.0):
        self.sample_rate = sample_rate
        self.rate_hz = rate_hz
        self.depth_ms = depth_ms
        self.base_ms = base_ms
        self.mix = 0.35
        self._buf = np.zeros(int(sample_rate * 0.06) + 8, dtype=np.float32)
        self._write = 0
        self._phase = 0.0

    def process(self, x: np.ndarray) -> np.ndarray:
        n = x.shape[0]
        buf = self._buf
        blen = len(buf)
        t = (self._phase + np.arange(n) / self.sample_rate) * 2.0 * np.pi * self.rate_hz
        mod_ms = self.base_ms + self.depth_ms * np.sin(t)
        mod_samples = mod_ms * 0.001 * self.sample_rate
        write_positions = (self._write + np.arange(n)) % blen
        buf[write_positions] = x
        read_pos = (write_positions - mod_samples) % blen
        idx0 = np.floor(read_pos).astype(np.int64) % blen
        idx1 = (idx0 + 1) % blen
        frac = (read_pos - np.floor(read_pos)).astype(np.float32)
        delayed = buf[idx0] * (1.0 - frac) + buf[idx1] * frac
        self._write = (self._write + n) % blen
        self._phase += n / self.sample_rate
        return x * (1.0 - self.mix) + delayed * self.mix


def saturate(x: np.ndarray, drive: float) -> np.ndarray:
    """Soft-clip saturation. drive in [0, 1]; 0 = clean passthrough."""
    if drive <= 0.0:
        return x
    amount = 1.0 + drive * 9.0
    return np.tanh(x * amount) / np.tanh(amount)


class Compressor:
    """Block-rate feed-forward compressor (control resolution = block size).

    Not sample-accurate; adequate for a musical "glue" effect on a lead
    voice and cheap enough for the real-time callback.
    """

    def __init__(self, threshold_db: float = -18.0, ratio: float = 3.0, attack: float = 0.01, release: float = 0.15):
        self.threshold_db = threshold_db
        self.ratio = ratio
        self.attack = attack
        self.release = release
        self._env_db = -60.0
        self._gain = 1.0

    def process(self, x: np.ndarray, sample_rate: int, block_seconds: float) -> np.ndarray:
        peak = float(np.max(np.abs(x))) if x.size else 0.0
        peak_db = 20.0 * np.log10(max(peak, 1e-6))
        coeff = self.attack if peak_db > self._env_db else self.release
        alpha = 1.0 - np.exp(-block_seconds / max(0.001, coeff))
        self._env_db += (peak_db - self._env_db) * alpha
        if self._env_db > self.threshold_db:
            over = self._env_db - self.threshold_db
            reduction_db = over - over / self.ratio
            self._gain = 10 ** (-reduction_db / 20.0)
        else:
            self._gain = 1.0
        return x * self._gain


class Limiter:
    """Safety limiter: smoothed gain reduction + hard tanh ceiling.

    The hard ceiling guarantees no full-scale spike can ever reach the
    output device, satisfying the "audio safety" requirement independent
    of anything upstream misbehaving.
    """

    def __init__(self, ceiling: float = 0.95):
        self.ceiling = ceiling
        self._gain = 1.0

    def process(self, x: np.ndarray) -> np.ndarray:
        peak = float(np.max(np.abs(x))) if x.size else 0.0
        if peak > self.ceiling:
            target_gain = self.ceiling / peak
            self._gain = min(self._gain, target_gain)
        else:
            self._gain += (1.0 - self._gain) * 0.05
            self._gain = min(1.0, self._gain)
        y = x * self._gain
        return np.tanh(y * 1.05) * (self.ceiling / np.tanh(1.05 * self.ceiling)) if self.ceiling < 1.0 else np.clip(y, -0.999, 0.999)


def smooth_ramp(current: float, target: float, n_frames: int, coeff: float = 0.35) -> np.ndarray:
    """Exponential smoothing ramp from current to target over n_frames.

    Used for all knob/control-driven parameters (pitch bend, cutoff,
    expression) to avoid zipper noise -- see spec section 45. The
    recurrence v[n] = v[n-1] + coeff*(target-v[n-1]) has a closed form,
    so this is computed directly with numpy rather than a per-sample loop.
    """
    if n_frames <= 0:
        return np.array([], dtype=np.float32)
    factors = (1.0 - coeff) ** np.arange(1, n_frames + 1, dtype=np.float64)
    vals = target + (current - target) * factors
    return vals.astype(np.float32)

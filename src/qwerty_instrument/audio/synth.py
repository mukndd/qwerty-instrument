"""Internal synth-based instruments: Analog/Synth Lead and Electric Piano.

Both are usable with zero external dependencies (no VST, no SoundFont),
satisfying the requirement that the app works with nothing installed.
"""

from __future__ import annotations

import numpy as np

from . import effects
from .voices import InstrumentBackend, Voice

TWO_PI = 2.0 * np.pi


def _osc(waveform: str, phases: np.ndarray) -> np.ndarray:
    ph = np.mod(phases, TWO_PI)
    if waveform == "sine":
        return np.sin(ph)
    if waveform == "square":
        return np.where(ph < np.pi, 1.0, -1.0).astype(np.float32)
    # default: naive sawtooth
    return (2.0 * (ph / TWO_PI) - 1.0).astype(np.float32)


def _detune_ratios(osc_count: int, detune_cents: float) -> list[float]:
    if osc_count <= 1:
        return [1.0]
    ratios = []
    for i in range(osc_count):
        frac = (i / (osc_count - 1)) - 0.5
        cents = frac * detune_cents * 2.0
        ratios.append(2.0 ** (cents / 1200.0))
    return ratios


DEFAULT_SYNTH_PATCH = {
    "waveform": "saw",
    "osc_count": 2,
    "detune_cents": 9.0,
    "attack": 0.006,
    "decay": 0.12,
    "sustain": 0.75,
    "release": 0.18,
    "cutoff": 3200.0,
    "resonance": 0.25,
    "filter_env_amount": 0.5,
    "drive": 0.12,
    "chorus_mix": 0.3,
    "delay_enabled": True,
    "delay_seconds": 0.28,
    "delay_feedback": 0.28,
    "delay_mix": 0.22,
    "reverb_enabled": True,
    "reverb_mix": 0.18,
    "reverb_decay": 0.5,
    "master_gain": 0.55,
}


class SynthLeadVoice(Voice):
    def __init__(self, sample_rate: int, block_size: int, patch: dict):
        super().__init__(sample_rate, block_size)
        self.patch = patch
        self.env = effects.ADSR()
        self.filter = effects.StateVariableFilter(sample_rate)
        self._phases = [0.0, 0.0, 0.0]

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
        ratios = _detune_ratios(p["osc_count"], p["detune_cents"])
        mix = np.zeros(n_frames, dtype=np.float32)
        for i, ratio in enumerate(ratios):
            f = freq * ratio
            phase_inc = TWO_PI * f / self.sample_rate
            phases = self._phases[i] + phase_inc * np.arange(n_frames, dtype=np.float64)
            self._phases[i] = float((self._phases[i] + phase_inc * n_frames) % TWO_PI)
            mix += _osc(p["waveform"], phases)
        mix /= max(1, len(ratios))
        env = self.env.process(n_frames, self.sample_rate)
        vel_gain = 0.35 + 0.65 * self.velocity
        mix = mix * env * vel_gain
        env_mean = float(np.mean(env)) if n_frames else 0.0
        cutoff = p["cutoff"] * (1.0 - p["filter_env_amount"] + p["filter_env_amount"] * env_mean * 1.3)
        mix = self.filter.process(mix, cutoff, p["resonance"])
        return mix

    @property
    def finished(self) -> bool:
        return self.env.finished


class SynthLead(InstrumentBackend):
    name = "synth_lead"

    def __init__(self, sample_rate: int, block_size: int, polyphony: int = 16, patch: dict | None = None):
        self.patch = dict(DEFAULT_SYNTH_PATCH)
        if patch:
            self.patch.update(patch)
        super().__init__(sample_rate, block_size, polyphony)
        self._chorus_l = effects.Chorus(sample_rate, rate_hz=0.55, base_ms=11.0)
        self._chorus_r = effects.Chorus(sample_rate, rate_hz=0.71, base_ms=14.0)
        self._delay = effects.Delay(sample_rate)
        self._reverb = effects.SchroederReverb(sample_rate)

    def _create_voice(self) -> Voice:
        return SynthLeadVoice(self.sample_rate, self.block_size, self.patch)

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
        elif name == "chorus_mix":
            self._chorus_l.mix = value
            self._chorus_r.mix = value

    def _mix(self, mono: np.ndarray, n_frames: int) -> np.ndarray:
        p = self.patch
        x = effects.saturate(mono, p["drive"])
        if p["delay_enabled"]:
            x = self._delay.process(x)
        if p["reverb_enabled"]:
            x = self._reverb.process(x)
        left = self._chorus_l.process(x)
        right = self._chorus_r.process(x)
        gain = p["master_gain"]
        stereo = np.stack([left, right], axis=1) * gain
        return stereo.astype(np.float32)


DEFAULT_EPIANO_PATCH = {
    "attack": 0.002,
    "decay_tau": 2.2,
    "sustain_floor": 0.08,
    "release": 0.35,
    "tine_ratio": 6.7,
    "tine_decay": 0.09,
    "tremolo_rate": 4.5,
    "tremolo_depth": 0.12,
    "cutoff": 5200.0,
    "resonance": 0.05,
    "chorus_mix": 0.25,
    "master_gain": 0.6,
}


class ElectricPianoVoice(Voice):
    """Sine carrier + fast-decaying higher partial ('tine') + slow decay-while-held.

    Not a physical model -- a lightweight additive approximation of the
    classic electric-piano attack/decay character (bright transient that
    settles into a softer sustained tone), per spec section B.
    """

    def __init__(self, sample_rate: int, block_size: int, patch: dict):
        super().__init__(sample_rate, block_size)
        self.patch = patch
        self.env = effects.ADSR()
        self.tine_env = effects.ADSR()
        self.filter = effects.StateVariableFilter(sample_rate)
        self._phase = 0.0
        self._tine_phase = 0.0
        self._elapsed = 0
        self._held = True

    def start(self, note: int, velocity: float) -> None:
        self.note = note
        self.velocity = velocity
        self.bend_cents = 0.0
        p = self.patch
        self.env.attack = p["attack"]
        self.env.decay = 0.05
        self.env.sustain = 1.0
        self.env.release = p["release"]
        self.env.note_on()
        self.tine_env.attack = 0.001
        self.tine_env.decay = p["tine_decay"]
        self.tine_env.sustain = 0.0
        self.tine_env.release = 0.02
        self.tine_env.note_on()
        self.filter.reset()
        self._elapsed = 0
        self._held = True

    def release(self) -> None:
        self.env.note_off()
        self._held = False

    def render(self, n_frames: int) -> np.ndarray:
        p = self.patch
        freq = self.current_freq()
        phase_inc = TWO_PI * freq / self.sample_rate
        phases = self._phase + phase_inc * np.arange(n_frames, dtype=np.float64)
        self._phase = float((self._phase + phase_inc * n_frames) % TWO_PI)
        carrier = np.sin(phases).astype(np.float32)

        tine_inc = TWO_PI * freq * p["tine_ratio"] / self.sample_rate
        tine_phases = self._tine_phase + tine_inc * np.arange(n_frames, dtype=np.float64)
        self._tine_phase = float((self._tine_phase + tine_inc * n_frames) % TWO_PI)
        tine = np.sin(tine_phases).astype(np.float32)

        env = self.env.process(n_frames, self.sample_rate)
        tine_env = self.tine_env.process(n_frames, self.sample_rate)

        t = (self._elapsed + np.arange(n_frames)) / self.sample_rate
        held_decay = np.exp(-t / max(0.05, p["decay_tau"])).astype(np.float32)
        held_decay = p["sustain_floor"] + (1.0 - p["sustain_floor"]) * held_decay
        self._elapsed += n_frames

        tremolo = 1.0 + p["tremolo_depth"] * np.sin(
            TWO_PI * p["tremolo_rate"] * t
        ).astype(np.float32)

        vel_gain = 0.4 + 0.6 * self.velocity
        tine_gain = 0.15 + 0.5 * self.velocity

        mix = (carrier * held_decay + tine * tine_gain * tine_env) * env * vel_gain * tremolo
        mix = self.filter.process(mix, p["cutoff"], p["resonance"])
        return mix

    @property
    def finished(self) -> bool:
        return self.env.finished


class ElectricPiano(InstrumentBackend):
    name = "electric_piano"

    def __init__(self, sample_rate: int, block_size: int, polyphony: int = 16, patch: dict | None = None):
        self.patch = dict(DEFAULT_EPIANO_PATCH)
        if patch:
            self.patch.update(patch)
        super().__init__(sample_rate, block_size, polyphony)
        self._chorus_l = effects.Chorus(sample_rate, rate_hz=0.45, base_ms=9.0)
        self._chorus_r = effects.Chorus(sample_rate, rate_hz=0.62, base_ms=12.0)

    def _create_voice(self) -> Voice:
        return ElectricPianoVoice(self.sample_rate, self.block_size, self.patch)

    def set_param(self, name: str, value) -> None:
        self.patch[name] = value
        if name == "chorus_mix":
            self._chorus_l.mix = value
            self._chorus_r.mix = value

    def _mix(self, mono: np.ndarray, n_frames: int) -> np.ndarray:
        left = self._chorus_l.process(mono)
        right = self._chorus_r.process(mono)
        gain = self.patch["master_gain"]
        stereo = np.stack([left, right], axis=1) * gain
        return stereo.astype(np.float32)

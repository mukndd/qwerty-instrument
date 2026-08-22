"""AULA F75 rotary-knob integration.

DO NOT ASSUME: the exact events the F75's knob generates depend on
firmware/mode and must be confirmed per-machine with
tools/f75_input_probe.py first (spec section 8). This module implements
the most likely default -- the knob's office/multimedia mode surfacing as
standard VK_VOLUME_UP/VK_VOLUME_DOWN/VK_VOLUME_MUTE virtual keys, which
arrive through the same WH_KEYBOARD_LL stream as ordinary keys (see
VK_TO_KEYID in input/windows_hook.py) -- and is built to be easy to extend
with an additional detection path (e.g. WM_APPCOMMAND) if the probe shows
the real hardware behaves differently.

Because our low-level hook can suppress propagation, intercepting these
VK codes while Instrument Capture is on also prevents the actual Windows
volume from changing (spec section 11); releasing capture (CapsLock)
restores normal system volume behaviour immediately.
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass
from typing import Callable

from ..music.events import EventType, MusicEvent, Source

logger = logging.getLogger("qwerty_instrument.input.f75")

MusicEventSink = Callable[[MusicEvent], None]

# Musically sensible ranges for non-pitch-bend knob modes (0..1 normalized -> range).
PARAM_RANGES: dict[str, tuple[float, float]] = {
    "cutoff": (200.0, 8000.0),
    "tremolo_depth": (0.0, 0.4),
    "drive": (0.0, 0.6),
    "delay_feedback": (0.0, 0.6),
    "expression": (0.0, 1.0),
    "modulation": (0.0, 1.0),
}

MODE_TO_PARAM = {
    "expression": "expression",
    "filter_cutoff": "cutoff",
    "vibrato_depth": "tremolo_depth",
    "drive": "drive",
    "delay_feedback": "delay_feedback",
    "modulation": "modulation",
    "custom_macro": "expression",
}


@dataclass
class KnobConfig:
    mode: str = "pitch_bend"
    bend_step_cents: float = 25.0
    max_bend_cents: float = 200.0  # +/- 2 semitones default (spec section 9)
    expression_step: float = 0.08
    spring_return: bool = True
    return_delay_s: float = 0.16
    return_duration_s: float = 0.22
    short_press_max_s: float = 0.5


class F75Knob:
    """Consumes VOLUME_UP/VOLUME_DOWN/VOLUME_MUTE key events as knob input.

    Wire this in *before* KeyboardInstrument in the raw-key dispatch chain
    (see app.py's InputRouter): `handle_key` returns True when it consumed
    the event, so the router can fall through to normal QWERTY handling for
    everything else.
    """

    def __init__(
        self,
        event_sink: MusicEventSink,
        config: KnobConfig | None = None,
        on_short_press: Callable[[], None] | None = None,
    ):
        self.event_sink = event_sink
        self.config = config or KnobConfig()
        self.on_short_press = on_short_press
        self.value = 0.0  # cents (pitch_bend mode) or 0..1 normalized (other modes)
        self._last_rotation_perf = 0.0
        self._press_down_perf: float | None = None
        self._spring_running = False
        self._lock = threading.RLock()

    def handle_key(self, key_id: str, is_down: bool, ts_ns: int) -> bool:
        if key_id == "VOLUME_UP":
            if is_down:
                self._rotate(+1, ts_ns)
            return True
        if key_id == "VOLUME_DOWN":
            if is_down:
                self._rotate(-1, ts_ns)
            return True
        if key_id == "VOLUME_MUTE":
            if is_down:
                self._press_down_perf = time.perf_counter()
            elif self._press_down_perf is not None:
                held = time.perf_counter() - self._press_down_perf
                self._press_down_perf = None
                if held <= self.config.short_press_max_s and self.on_short_press:
                    self.on_short_press()
            return True
        return False

    def set_mode(self, mode: str) -> None:
        self.config.mode = mode
        self.value = 0.0
        self._emit(time.perf_counter_ns())

    def _rotate(self, direction: int, ts_ns: int) -> None:
        cfg = self.config
        with self._lock:
            self._last_rotation_perf = time.perf_counter()
            if cfg.mode == "pitch_bend":
                self.value = max(-cfg.max_bend_cents, min(cfg.max_bend_cents, self.value + cfg.bend_step_cents * direction))
            else:
                self.value = max(0.0, min(1.0, self.value + cfg.expression_step * direction))
            self._emit(ts_ns)
        if cfg.spring_return:
            self._ensure_spring_thread()

    def _emit(self, ts_ns: int) -> None:
        cfg = self.config
        if cfg.mode == "pitch_bend":
            self.event_sink(
                MusicEvent(type=EventType.PITCH_BEND, timestamp_ns=ts_ns, source=Source.F75_KNOB, metadata={"cents": self.value})
            )
        else:
            param = MODE_TO_PARAM.get(cfg.mode, "expression")
            lo, hi = PARAM_RANGES.get(param, (0.0, 1.0))
            scaled = lo + (hi - lo) * self.value
            self.event_sink(
                MusicEvent(
                    type=EventType.EXPRESSION,
                    timestamp_ns=ts_ns,
                    source=Source.F75_KNOB,
                    metadata={"param": param, "value": scaled, "normalized": self.value},
                )
            )

    def _ensure_spring_thread(self) -> None:
        with self._lock:
            if self._spring_running:
                return
            self._spring_running = True
        threading.Thread(target=self._spring_loop, daemon=True, name="f75-knob-spring").start()

    def _spring_loop(self) -> None:
        cfg = self.config
        tick = 0.02
        while True:
            time.sleep(tick)
            with self._lock:
                idle = time.perf_counter() - self._last_rotation_perf
                if idle < cfg.return_delay_s:
                    continue
                if abs(self.value) < 1e-3:
                    self.value = 0.0
                    self._spring_running = False
                    self._emit(time.perf_counter_ns())
                    return
                alpha = min(1.0, (tick / max(0.02, cfg.return_duration_s)) * 3.0)
                self.value *= 1.0 - alpha
                self._emit(time.perf_counter_ns())

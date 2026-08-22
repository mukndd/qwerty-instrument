"""QWERTY orchestrator: turns raw (key_id, is_down, ts_ns) into MusicEvents.

This is the layer that owns musical meaning -- the InputSource below it
(windows_hook.WindowsLowLevelKeyboardHook, or a future Qt/other backend)
only knows about raw keys. `handle_key_event` runs on the input backend's own
thread (for the Win32 hook, that's the hook's message-pump thread) so it
must stay cheap: dict lookups and a non-blocking queue put via
`event_sink`, nothing else.
"""

from __future__ import annotations

import random
import time

from typing import Callable

from ..music.events import EventType, MusicEvent, Source
from ..music.mapping import KeyboardMapping
from .base import InputSource

MusicEventSink = Callable[[MusicEvent], None]


class KeyboardInstrument:
    def __init__(
        self,
        mapping: KeyboardMapping,
        input_source: InputSource,
        event_sink: MusicEventSink,
        instrument_names: list[str] | None = None,
        sustain_mode: str = "hold",
        base_velocity: float = 0.85,
        humanize_amount: float = 0.04,
    ):
        self.mapping = mapping
        self.input_source = input_source
        self.event_sink = event_sink
        self.instrument_names = instrument_names or []
        self.sustain_mode = sustain_mode
        self.base_velocity = base_velocity
        self.humanize_amount = humanize_amount

        self.capture_enabled = True
        self._sustain_engaged = False
        self._accent_held = False
        self._active_keys: dict[str, int] = {}
        self._current_instrument_index = 0

    def start(self) -> None:
        self.input_source.start(self.handle_key_event)
        self.input_source.set_capture_enabled(self.capture_enabled)

    def stop(self) -> None:
        self.panic()
        self.input_source.stop()

    # ---- raw key handling (runs on the input backend's thread) ------------

    def handle_key_event(self, key_id: str, is_down: bool, ts_ns: int) -> None:
        if key_id == "CAPSLOCK":
            if is_down:
                self.toggle_capture()
            return

        if not self.capture_enabled:
            return

        control = self.mapping.resolve_control(key_id)
        if control:
            self._handle_control(control, is_down, ts_ns)
            return

        note = self.mapping.resolve_note(key_id)
        if note is None:
            return

        if is_down:
            if key_id in self._active_keys:
                return  # defensive; hook already suppresses OS repeat
            self._active_keys[key_id] = note
            velocity = self._compute_velocity()
            self.event_sink(
                MusicEvent(
                    type=EventType.NOTE_ON,
                    note=note,
                    velocity=velocity,
                    timestamp_ns=ts_ns,
                    source=Source.QWERTY,
                    metadata={"key_id": key_id},
                )
            )
        else:
            played_note = self._active_keys.pop(key_id, note)
            self.event_sink(
                MusicEvent(
                    type=EventType.NOTE_OFF,
                    note=played_note,
                    timestamp_ns=ts_ns,
                    source=Source.QWERTY,
                    metadata={"key_id": key_id},
                )
            )

    def _handle_control(self, control: str, is_down: bool, ts_ns: int) -> None:
        if control == "sustain":
            if self.sustain_mode == "hold":
                if is_down and not self._sustain_engaged:
                    self._set_sustain(True, ts_ns)
                elif not is_down and self._sustain_engaged:
                    self._set_sustain(False, ts_ns)
            elif is_down:
                self._set_sustain(not self._sustain_engaged, ts_ns)
        elif control == "panic":
            if is_down:
                self.panic()
        elif control == "octave_up":
            if is_down:
                self.mapping.shift_octave(1)
        elif control == "octave_down":
            if is_down:
                self.mapping.shift_octave(-1)
        elif control == "instrument_next":
            if is_down:
                self.cycle_instrument()
        elif control == "accent":
            self._accent_held = is_down
        elif control == "capture_toggle":
            if is_down:
                self.toggle_capture()
        elif control in ("song_mode_toggle", "metronome_toggle"):
            if is_down:
                self.event_sink(
                    MusicEvent(
                        type=EventType.MODE_CHANGE,
                        timestamp_ns=ts_ns,
                        source=Source.QWERTY,
                        metadata={"control": control},
                    )
                )

    def _set_sustain(self, on: bool, ts_ns: int) -> None:
        self._sustain_engaged = on
        self.event_sink(
            MusicEvent(type=EventType.SUSTAIN, timestamp_ns=ts_ns, source=Source.QWERTY, metadata={"on": on})
        )

    def panic(self) -> None:
        ts = time.perf_counter_ns()
        self.event_sink(MusicEvent(type=EventType.ALL_NOTES_OFF, timestamp_ns=ts, source=Source.QWERTY))
        self._active_keys.clear()
        self._sustain_engaged = False

    def toggle_capture(self) -> None:
        self.capture_enabled = not self.capture_enabled
        self.input_source.set_capture_enabled(self.capture_enabled)
        if not self.capture_enabled:
            self.panic()

    def cycle_instrument(self) -> None:
        if not self.instrument_names:
            return
        self._current_instrument_index = (self._current_instrument_index + 1) % len(self.instrument_names)
        name = self.instrument_names[self._current_instrument_index]
        self.event_sink(
            MusicEvent(
                type=EventType.PROGRAM_CHANGE,
                timestamp_ns=time.perf_counter_ns(),
                source=Source.QWERTY,
                metadata={"instrument": name},
            )
        )

    def _compute_velocity(self) -> float:
        v = self.base_velocity
        if self._accent_held:
            v = min(1.0, v + 0.15)
        v += random.uniform(-self.humanize_amount, self.humanize_amount)
        return max(0.05, min(1.0, v))

    def suppress_predicate(self, key_id: str) -> bool:
        """Passed to the InputSource: should this key be swallowed system-wide?"""
        return self.mapping.resolve_note(key_id) is not None or self.mapping.resolve_control(key_id) is not None

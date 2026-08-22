"""MIDI input, feeding the same MusicEvent bus as the QWERTY keyboard.

This is what makes swapping in a real MIDI keyboard later a non-event for
the rest of the app (spec section 30): it produces exactly the same
MusicEvent shape, just with real velocity preserved from the hardware
instead of a QWERTY humanization strategy.
"""

from __future__ import annotations

import logging
from typing import Callable

from ..music.events import EventType, MusicEvent, Source

logger = logging.getLogger("qwerty_instrument.input.midi")

MusicEventSink = Callable[[MusicEvent], None]

try:
    import mido

    MIDO_AVAILABLE = True
except Exception:  # pragma: no cover
    MIDO_AVAILABLE = False


def list_input_ports() -> list[str]:
    if not MIDO_AVAILABLE:
        return []
    try:
        return list(mido.get_input_names())
    except Exception as exc:
        logger.warning("failed to list MIDI input ports: %s", exc)
        return []


class MidiInput:
    def __init__(self, event_sink: MusicEventSink, channel: int = 0):
        self.event_sink = event_sink
        self.channel = channel
        self._port = None
        self.port_name: str | None = None

    @property
    def is_available(self) -> bool:
        return MIDO_AVAILABLE

    def open(self, port_name: str) -> bool:
        if not MIDO_AVAILABLE:
            return False
        try:
            self._port = mido.open_input(port_name, callback=self._on_message)
            self.port_name = port_name
            logger.info("MIDI input opened: %s", port_name)
            return True
        except Exception as exc:
            logger.warning("failed to open MIDI input %s: %s", port_name, exc)
            return False

    def close(self) -> None:
        if self._port is not None:
            try:
                self._port.close()
            except Exception:
                pass
            self._port = None
            self.port_name = None

    def _on_message(self, msg) -> None:
        import time

        ts = time.perf_counter_ns()
        if msg.type == "note_on" and msg.velocity > 0:
            self.event_sink(
                MusicEvent(
                    type=EventType.NOTE_ON,
                    note=msg.note,
                    velocity=msg.velocity / 127.0,
                    timestamp_ns=ts,
                    source=Source.MIDI_IN,
                    channel=msg.channel,
                    metadata={"key_id": f"midi_{msg.note}"},
                )
            )
        elif msg.type == "note_off" or (msg.type == "note_on" and msg.velocity == 0):
            self.event_sink(
                MusicEvent(
                    type=EventType.NOTE_OFF,
                    note=msg.note,
                    timestamp_ns=ts,
                    source=Source.MIDI_IN,
                    channel=msg.channel,
                    metadata={"key_id": f"midi_{msg.note}"},
                )
            )
        elif msg.type == "pitchwheel":
            cents = (msg.pitch / 8192.0) * 200.0  # +/- 2 semitones full range, matches default bend clamp
            self.event_sink(
                MusicEvent(type=EventType.PITCH_BEND, timestamp_ns=ts, source=Source.MIDI_IN, channel=msg.channel, metadata={"cents": cents})
            )
        elif msg.type == "control_change" and msg.control == 64:  # sustain pedal
            self.event_sink(
                MusicEvent(type=EventType.SUSTAIN, timestamp_ns=ts, source=Source.MIDI_IN, channel=msg.channel, metadata={"on": msg.value >= 64})
            )
        elif msg.type == "control_change" and msg.control == 11:  # expression
            self.event_sink(
                MusicEvent(
                    type=EventType.EXPRESSION,
                    timestamp_ns=ts,
                    source=Source.MIDI_IN,
                    channel=msg.channel,
                    metadata={"param": "expression", "value": msg.value / 127.0},
                )
            )

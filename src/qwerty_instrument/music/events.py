"""Internal musical event bus abstraction.

Every input source (QWERTY keyboard, F75 knob, future MIDI controllers)
produces `MusicEvent` objects. The audio engine and song/training engine
only ever consume `MusicEvent`s -- they never know whether a note came
from a keypress or a MIDI cable. This is what lets a real MIDI keyboard
be swapped in later without touching the song engine (see ARCHITECTURE.md).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Any


class EventType(Enum):
    NOTE_ON = auto()
    NOTE_OFF = auto()
    PITCH_BEND = auto()
    EXPRESSION = auto()
    MODULATION = auto()
    SUSTAIN = auto()
    PROGRAM_CHANGE = auto()
    MODE_CHANGE = auto()
    TRANSPORT_START = auto()
    TRANSPORT_STOP = auto()
    ALL_NOTES_OFF = auto()


class Source(Enum):
    QWERTY = auto()
    F75_KNOB = auto()
    MIDI_IN = auto()
    SONG_PLAYBACK = auto()
    UI = auto()
    ASSIST_ENGINE = auto()


@dataclass(slots=True)
class MusicEvent:
    """A single timestamped musical event.

    timestamp_ns: value from time.perf_counter_ns() at the moment the
    *input* was captured (not when it was processed) -- used for latency
    instrumentation and for scoring timing accuracy.
    """

    type: EventType
    note: int = 0
    velocity: float = 1.0
    timestamp_ns: int = 0
    source: Source = Source.QWERTY
    channel: int = 0
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def voice_id(self) -> tuple[Source, int, int]:
        """Identity used to match a NOTE_OFF back to its NOTE_ON.

        Includes the input key/identity (metadata['key_id']) rather than
        just note+channel, so two different physical keys mapped to the
        same note don't steal each other's note-off.
        """
        return (self.source, self.channel, self.metadata.get("key_id", self.note))

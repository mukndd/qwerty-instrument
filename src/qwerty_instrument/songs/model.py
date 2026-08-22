"""Data-driven song model.

Nothing here is specific to any one song -- Instant Crush is just one
directory of data conforming to this shape (see songs/instant_crush/ and
docs/ADDING_SONGS.md). Timing is stored in beats; wall-clock conversion
happens via music.timing.TempoMap so practice-speed scaling is centralized.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

from ..music.timing import TempoMap


class NoteVerification(Enum):
    """Provenance of a note's pitch/timing data.

    VERIFIED means it came from a user-supplied MIDI/reference file via
    tools/import_midi.py. PLACEHOLDER means the section exists in the song
    structure but no verified transcription has been imported yet -- the
    trainer and UI must show this distinction, never silently teach a
    guessed note as if it were confirmed (spec section 13/57).
    """

    VERIFIED = "verified"
    PLACEHOLDER = "placeholder"


@dataclass(slots=True)
class NoteEvent:
    beat: float
    duration_beats: float
    note: int  # MIDI pitch
    velocity: float = 0.9
    layer: str = "melody"  # melody | chords | bass | lead_guitar (song-defined, not fixed)
    verification: NoteVerification = NoteVerification.PLACEHOLDER


@dataclass(slots=True)
class ChordEvent:
    beat: float
    duration_beats: float
    name: str
    notes: list[int]
    layer: str = "chords"
    verification: NoteVerification = NoteVerification.PLACEHOLDER


@dataclass(slots=True)
class KnobAction:
    beat: float
    action: str  # "bend_up" | "bend_down" | "mode_lead_guitar" | "mode_normal"
    amount: float = 0.0


@dataclass(slots=True)
class InstrumentChange:
    beat: float
    instrument: str


@dataclass(slots=True)
class Section:
    id: str
    name: str
    start_beat: float
    end_beat: float
    difficulty: str = "medium"
    loop_default: bool = False
    notes: list = field(default_factory=list)  # NoteEvent | ChordEvent


@dataclass(slots=True)
class Song:
    title: str
    artist: str
    tempo_map: TempoMap
    sections: list[Section] = field(default_factory=list)
    knob_actions: list[KnobAction] = field(default_factory=list)
    instrument_changes: list[InstrumentChange] = field(default_factory=list)
    source_dir: str = ""
    notes_disclaimer: str = ""

    def section_by_id(self, section_id: str) -> Section | None:
        for s in self.sections:
            if s.id == section_id:
                return s
        return None

    def all_events_in_beat_range(self, start_beat: float, end_beat: float) -> list:
        events = []
        for section in self.sections:
            for ev in section.notes:
                if start_beat <= ev.beat < end_beat:
                    events.append(ev)
        return sorted(events, key=lambda e: e.beat)

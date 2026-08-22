"""Chord voicings for Chord Performance Mode (see section 16 of the spec).

Chords are stored as explicit MIDI pitch lists, never just names -- a chord
"name" is ambiguous about voicing/inversion/octave, and the whole point of
this mode is deterministic, authored playback.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(slots=True)
class Chord:
    name: str
    notes: list[int]  # explicit MIDI pitches, e.g. [48, 52, 55] = C3 E3 G3


@dataclass
class ChordBank:
    """Maps a physical key to a chord, for one-key-per-chord performance."""

    chords: dict[str, Chord]

    @classmethod
    def from_config(cls, entries: dict) -> "ChordBank":
        chords = {}
        for key_id, data in entries.items():
            chords[key_id] = Chord(name=data["name"], notes=list(data["notes"]))
        return cls(chords=chords)

    def resolve(self, key_id: str) -> Chord | None:
        return self.chords.get(key_id)

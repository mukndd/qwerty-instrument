"""MIDI note number <-> name <-> frequency helpers.

MIDI note numbers are used as the canonical pitch representation
throughout the engine (0-127, 60 = C4 "middle C").
"""

from __future__ import annotations

_NOTE_NAMES = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]
_NAME_TO_SEMITONE = {name: i for i, name in enumerate(_NOTE_NAMES)}
# Common enharmonic spellings accepted on input.
_NAME_TO_SEMITONE.update(
    {
        "Db": 1,
        "Eb": 3,
        "Fb": 4,
        "Gb": 6,
        "Ab": 8,
        "Bb": 10,
        "Cb": 11,
        "E#": 5,
        "B#": 0,
    }
)

A4_MIDI = 69
A4_FREQ = 440.0


def midi_to_freq(note: int, bend_cents: float = 0.0) -> float:
    """Convert a MIDI note number (+ optional cents offset) to Hz (equal temperament, A4=440)."""
    semitone_offset = (note - A4_MIDI) + (bend_cents / 100.0)
    return A4_FREQ * (2.0 ** (semitone_offset / 12.0))


def midi_to_name(note: int) -> str:
    octave = note // 12 - 1
    name = _NOTE_NAMES[note % 12]
    return f"{name}{octave}"


def name_to_midi(name: str) -> int:
    """Parse a note name like 'C#4', 'Db5', 'A0' into a MIDI note number."""
    name = name.strip()
    idx = 1
    if len(name) > 1 and name[1] in ("#", "b"):
        idx = 2
    pitch_part = name[:idx]
    octave_part = name[idx:]
    if pitch_part not in _NAME_TO_SEMITONE:
        raise ValueError(f"Unrecognized note name: {name!r}")
    semitone = _NAME_TO_SEMITONE[pitch_part]
    octave = int(octave_part)
    return (octave + 1) * 12 + semitone


def clamp_midi(note: int) -> int:
    return max(0, min(127, note))

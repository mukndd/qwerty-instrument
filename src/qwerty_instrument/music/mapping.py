"""QWERTY -> musical note mapping.

Layout design
-------------
Physical QWERTY keys are staggered such that each "home row" key sits
between two "bottom row" keys (e.g. S sits between Z and X; D sits between
X and C). This is the same physical relationship a piano's black keys have
to its white keys, so we exploit it directly: the lower physical row of a
pair supplies the "white" (natural) notes in sequence, and the neighbouring
upper row supplies the "black" (sharp) notes, landing in the correct gaps
and skipping the E-F and B-C gaps exactly like a real keyboard.

We do this twice, stacked an octave apart, using two independent row pairs:

    Block 1 (base octave):      bottom row (Z X C V B N M , . /) = white
                                 home row   (S D F G H J K L ;)   = black
    Block 2 (base octave + 12): top row    (Q W E R T Y U I O P) = white
                                 number row (2 3 4 5 6 7 8 9 0)   = black

Block 2 is anchored one octave above Block 1, giving a combined default
range of C3-E5 (2 octaves + a third) with some deliberately reachable
overlap in the middle, spanning both hands. Octave Up/Down controls shift
both blocks together for material outside that range.

This is a default, not a hardcoded constraint -- the whole map is data
(see config.py) and can be overridden per-song or per-user.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .notes import clamp_midi

# Semitone offsets of the 7 natural (white) notes within an octave, in order.
_WHITE_DEGREES = [0, 2, 4, 5, 7, 9, 11]
# For a given white-note degree index (0=C..6=B), the semitone offset of the
# sharp/black note immediately above it, if one exists (none after E or B).
_BLACK_AFTER_DEGREE = {0: 1, 1: 3, 3: 6, 4: 8, 5: 10}

BLOCK1_WHITE = ["Z", "X", "C", "V", "B", "N", "M", "COMMA", "PERIOD", "SLASH"]
BLOCK1_BLACK = ["S", "D", "F", "G", "H", "J", "K", "L", "SEMICOLON"]
BLOCK2_WHITE = ["Q", "W", "E", "R", "T", "Y", "U", "I", "O", "P"]
BLOCK2_BLACK = ["2", "3", "4", "5", "6", "7", "8", "9", "0"]

DEFAULT_BASE_NOTE = 48  # C3
DEFAULT_BLOCK2_OFFSET = 12  # one octave above block 1

# Keys deliberately left unassigned by the zigzag algorithm (they land on a
# non-black gap, or are the row's unpaired leading key) -- free for reuse as
# song-specific chord/bass layers without disturbing the default note map.
RESERVED_FREE_KEYS = ["A", "F", "K", "APOSTROPHE", "1", "4", "8"]

# Default control-key bindings. All overridable via config.
DEFAULT_CONTROLS = {
    "SPACE": "sustain",
    "ESCAPE": "panic",
    "TAB": "instrument_next",
    "CAPSLOCK": "capture_toggle",
    "PAGEUP": "octave_up",
    "PAGEDOWN": "octave_down",
    "SHIFT": "accent",
    "F5": "song_mode_toggle",
    "F6": "metronome_toggle",
}


def build_row_pair_block(white_row: list[str], black_row: list[str], base_note: int) -> dict[str, int]:
    """Zigzag a (white-row, black-row) key pair into a MIDI note map.

    `black_row[i]` is assumed to physically sit in the gap immediately
    after `white_row[i]`; it is only assigned a note if that gap actually
    contains a black key on a real keyboard (see `_BLACK_AFTER_DEGREE`).
    """
    notes: dict[str, int] = {}
    for i, key in enumerate(white_row):
        octave, deg = divmod(i, 7)
        notes[key] = base_note + 12 * octave + _WHITE_DEGREES[deg]
        if i < len(black_row) and deg in _BLACK_AFTER_DEGREE:
            notes[black_row[i]] = base_note + 12 * octave + _BLACK_AFTER_DEGREE[deg]
    return notes


def default_note_map(base_note: int = DEFAULT_BASE_NOTE, block2_offset: int = DEFAULT_BLOCK2_OFFSET) -> dict[str, int]:
    notes = build_row_pair_block(BLOCK1_WHITE, BLOCK1_BLACK, base_note)
    notes.update(build_row_pair_block(BLOCK2_WHITE, BLOCK2_BLACK, base_note + block2_offset))
    return notes


@dataclass
class KeyboardMapping:
    """Runtime QWERTY -> note/control resolver, built from config data."""

    note_map: dict[str, int] = field(default_factory=default_note_map)
    control_map: dict[str, str] = field(default_factory=lambda: dict(DEFAULT_CONTROLS))
    octave_shift: int = 0
    min_octave_shift: int = -3
    max_octave_shift: int = 3

    @classmethod
    def from_config(cls, cfg: dict) -> "KeyboardMapping":
        base_note = cfg.get("base_note", DEFAULT_BASE_NOTE)
        block2_offset = cfg.get("block2_offset", DEFAULT_BLOCK2_OFFSET)
        notes = default_note_map(base_note, block2_offset)
        notes.update(cfg.get("note_overrides", {}))
        controls = dict(DEFAULT_CONTROLS)
        controls.update(cfg.get("control_overrides", {}))
        return cls(note_map=notes, control_map=controls)

    def resolve_note(self, key_id: str) -> int | None:
        base = self.note_map.get(key_id)
        if base is None:
            return None
        return clamp_midi(base + 12 * self.octave_shift)

    def resolve_control(self, key_id: str) -> str | None:
        return self.control_map.get(key_id)

    def shift_octave(self, delta: int) -> int:
        self.octave_shift = max(self.min_octave_shift, min(self.max_octave_shift, self.octave_shift + delta))
        return self.octave_shift

    def all_note_keys(self) -> list[str]:
        return list(self.note_map.keys())

    def find_key_for_note(self, note: int) -> tuple[str, int] | None:
        """Which physical key plays `note`, and at what octave_shift?

        Searches the base (octave_shift=0) note map for a key whose pitch
        reaches `note` after some octave shift within range, preferring
        the shift closest to the mapping's *current* octave_shift (so
        practice-sheet/UI suggestions stay near wherever the player
        currently has the keyboard shifted to). Used by GUIDED-mode key
        highlighting and the practice-sheet exporter.
        """
        best: tuple[str, int] | None = None
        best_distance = None
        for key_id, base_note in self.note_map.items():
            diff = note - base_note
            if diff % 12 != 0:
                continue
            shift = diff // 12
            if not (self.min_octave_shift <= shift <= self.max_octave_shift):
                continue
            distance = abs(shift - self.octave_shift)
            if best is None or distance < best_distance:
                best = (key_id, shift)
                best_distance = distance
        return best

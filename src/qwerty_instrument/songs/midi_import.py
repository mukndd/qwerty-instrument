"""MIDI file inspection and import into our song note format.

This is the ONLY sanctioned way verified note data enters a song profile
(spec section 13/57): we do not fabricate transcriptions. A user supplies
a legally-obtained MIDI file (their own transcription, a licensed file,
etc.), tools/import_midi.py drives this module to inspect its tracks and
convert selected ones into NoteEvent lists marked NoteVerification.VERIFIED.
"""

from __future__ import annotations

from dataclasses import dataclass

import mido

from ..music.timing import TempoEvent
from .model import NoteEvent, NoteVerification

# General MIDI program numbers -> instrument name (standard, fixed GM1 spec).
GM_INSTRUMENTS = {
    0: "Acoustic Grand Piano", 1: "Bright Acoustic Piano", 2: "Electric Grand Piano", 3: "Honky-tonk Piano",
    4: "Electric Piano 1", 5: "Electric Piano 2", 6: "Harpsichord", 7: "Clavi",
    8: "Celesta", 9: "Glockenspiel", 10: "Music Box", 11: "Vibraphone", 12: "Marimba", 13: "Xylophone",
    14: "Tubular Bells", 15: "Dulcimer",
    16: "Drawbar Organ", 17: "Percussive Organ", 18: "Rock Organ", 19: "Church Organ", 20: "Reed Organ",
    21: "Accordion", 22: "Harmonica", 23: "Tango Accordion",
    24: "Acoustic Guitar (nylon)", 25: "Acoustic Guitar (steel)", 26: "Electric Guitar (jazz)",
    27: "Electric Guitar (clean)", 28: "Electric Guitar (muted)", 29: "Overdriven Guitar",
    30: "Distortion Guitar", 31: "Guitar Harmonics",
    32: "Acoustic Bass", 33: "Electric Bass (finger)", 34: "Electric Bass (pick)", 35: "Fretless Bass",
    36: "Slap Bass 1", 37: "Slap Bass 2", 38: "Synth Bass 1", 39: "Synth Bass 2",
    40: "Violin", 41: "Viola", 42: "Cello", 43: "Contrabass", 44: "Tremolo Strings",
    45: "Pizzicato Strings", 46: "Orchestral Harp", 47: "Timpani",
    48: "String Ensemble 1", 49: "String Ensemble 2", 50: "Synth Strings 1", 51: "Synth Strings 2",
    52: "Choir Aahs", 53: "Voice Oohs", 54: "Synth Voice", 55: "Orchestra Hit",
    56: "Trumpet", 57: "Trombone", 58: "Tuba", 59: "Muted Trumpet", 60: "French Horn", 61: "Brass Section",
    62: "Synth Brass 1", 63: "Synth Brass 2",
    64: "Soprano Sax", 65: "Alto Sax", 66: "Tenor Sax", 67: "Baritone Sax", 68: "Oboe", 69: "English Horn",
    70: "Bassoon", 71: "Clarinet",
    72: "Piccolo", 73: "Flute", 74: "Recorder", 75: "Pan Flute", 76: "Blown Bottle", 77: "Shakuhachi",
    78: "Whistle", 79: "Ocarina",
    80: "Lead 1 (square)", 81: "Lead 2 (sawtooth)", 82: "Lead 3 (calliope)", 83: "Lead 4 (chiff)",
    84: "Lead 5 (charang)", 85: "Lead 6 (voice)", 86: "Lead 7 (fifths)", 87: "Lead 8 (bass + lead)",
    88: "Pad 1 (new age)", 89: "Pad 2 (warm)", 90: "Pad 3 (polysynth)", 91: "Pad 4 (choir)",
    92: "Pad 5 (bowed)", 93: "Pad 6 (metallic)", 94: "Pad 7 (halo)", 95: "Pad 8 (sweep)",
}


@dataclass
class MidiTrackInfo:
    index: int
    name: str
    channels: list[int]
    programs: list[int]
    instrument_names: list[str]
    note_count: int


@dataclass
class MidiInspection:
    ticks_per_beat: int
    tracks: list[MidiTrackInfo]
    tempo_events: list[TempoEvent]
    duration_seconds: float


def inspect_midi(path: str) -> MidiInspection:
    mid = mido.MidiFile(path)
    ticks_per_beat = mid.ticks_per_beat
    tracks: list[MidiTrackInfo] = []
    tempo_events: list[TempoEvent] = []

    for i, track in enumerate(mid.tracks):
        abs_ticks = 0
        name = None
        channels: set[int] = set()
        programs: set[int] = set()
        note_count = 0
        for msg in track:
            abs_ticks += msg.time
            if msg.type == "track_name":
                name = msg.name
            elif msg.type == "program_change":
                programs.add(msg.program)
                channels.add(msg.channel)
            elif msg.type == "note_on" and msg.velocity > 0:
                note_count += 1
                channels.add(msg.channel)
            elif msg.type == "set_tempo":
                tempo_events.append(TempoEvent(beat=abs_ticks / ticks_per_beat, bpm=mido.tempo2bpm(msg.tempo)))

        tracks.append(
            MidiTrackInfo(
                index=i,
                name=name or f"Track {i}",
                channels=sorted(channels),
                programs=sorted(programs),
                instrument_names=[GM_INSTRUMENTS.get(p, f"Program {p}") for p in sorted(programs)],
                note_count=note_count,
            )
        )

    if not tempo_events:
        tempo_events = [TempoEvent(beat=0.0, bpm=120.0)]
    tempo_events.sort(key=lambda e: e.beat)

    duration_seconds = mid.length
    return MidiInspection(ticks_per_beat=ticks_per_beat, tracks=tracks, tempo_events=tempo_events, duration_seconds=duration_seconds)


def extract_notes(path: str, track_indices: list[int], transpose: int = 0, layer: str = "melody") -> list[NoteEvent]:
    mid = mido.MidiFile(path)
    ticks_per_beat = mid.ticks_per_beat
    notes: list[NoteEvent] = []

    for i in track_indices:
        if i < 0 or i >= len(mid.tracks):
            continue
        track = mid.tracks[i]
        abs_ticks = 0
        active: dict[int, tuple[float, float]] = {}
        for msg in track:
            abs_ticks += msg.time
            beat = abs_ticks / ticks_per_beat
            if msg.type == "note_on" and msg.velocity > 0:
                active[msg.note] = (beat, msg.velocity / 127.0)
            elif msg.type == "note_off" or (msg.type == "note_on" and msg.velocity == 0):
                if msg.note in active:
                    start_beat, velocity = active.pop(msg.note)
                    duration = max(0.05, beat - start_beat)
                    notes.append(
                        NoteEvent(
                            beat=start_beat,
                            duration_beats=duration,
                            note=msg.note + transpose,
                            velocity=velocity,
                            layer=layer,
                            verification=NoteVerification.VERIFIED,
                        )
                    )
        for note, (start_beat, velocity) in active.items():
            notes.append(
                NoteEvent(beat=start_beat, duration_beats=0.25, note=note + transpose, velocity=velocity, layer=layer, verification=NoteVerification.VERIFIED)
            )

    notes.sort(key=lambda n: n.beat)
    return notes


def notes_to_json_entries(notes: list[NoteEvent], section_id: str | None = None) -> list[dict]:
    out = []
    for n in notes:
        entry = {
            "beat": round(n.beat, 6),
            "duration_beats": round(n.duration_beats, 6),
            "note": n.note,
            "velocity": round(n.velocity, 3),
            "layer": n.layer,
            "verification": n.verification.value,
        }
        if section_id:
            entry["section"] = section_id
        out.append(entry)
    return out

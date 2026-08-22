"""Load a Song from a data-driven song directory.

Expected layout (see docs/ADDING_SONGS.md):
    songs/<song_id>/
        song.yaml       -- title, artist, time signature, tempo map
        sections.yaml   -- section list (id, name, beat range, difficulty)
        notes.json       -- optional: NoteEvent/ChordEvent data per section
        mappings.yaml   -- optional: recommended keyboard/knob overrides

`notes.json` is optional on purpose: a song can exist with verified
section boundaries and tempo but no verified note transcription yet
(exactly the Instant Crush situation until a legally-obtained MIDI/
reference file is imported via tools/import_midi.py). Missing note data
produces empty, clearly-PLACEHOLDER sections rather than an error.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

import yaml

from ..music.timing import TempoEvent, TempoMap
from .model import ChordEvent, InstrumentChange, KnobAction, NoteEvent, NoteVerification, Section, Song

logger = logging.getLogger("qwerty_instrument.songs")


def _read_yaml(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            return yaml.safe_load(f) or {}
    except Exception as exc:
        logger.warning("failed to parse %s: %s", path, exc)
        return {}


def _read_json(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as exc:
        logger.warning("failed to parse %s: %s", path, exc)
        return {}


def load_song(song_dir: Path) -> Song:
    song_dir = Path(song_dir)
    meta = _read_yaml(song_dir / "song.yaml")

    time_sig = tuple(meta.get("time_signature", [4, 4]))
    tempo_events = [
        TempoEvent(beat=float(e.get("beat", 0.0)), bpm=float(e.get("bpm", 120.0)))
        for e in meta.get("tempo_events", [{"beat": 0.0, "bpm": 120.0}])
    ]
    tempo_map = TempoMap(time_signature=time_sig, events=tempo_events)

    sections_data = _read_yaml(song_dir / "sections.yaml").get("sections", [])
    sections: dict[str, Section] = {}
    for s in sections_data:
        try:
            sec = Section(
                id=s["id"],
                name=s.get("name", s["id"]),
                start_beat=float(s["start_beat"]),
                end_beat=float(s["end_beat"]),
                difficulty=s.get("difficulty", "medium"),
                loop_default=bool(s.get("loop_default", False)),
            )
            sections[sec.id] = sec
        except (KeyError, ValueError, TypeError) as exc:
            logger.warning("skipping malformed section entry %r: %s", s, exc)

    notes_data = _read_json(song_dir / "notes.json")
    _distribute_events(notes_data.get("notes", []), sections, is_chord=False)
    _distribute_events(notes_data.get("chords", []), sections, is_chord=True)

    knob_actions = [
        KnobAction(beat=float(k["beat"]), action=k["action"], amount=float(k.get("amount", 0.0)))
        for k in meta.get("knob_actions", [])
        if "beat" in k and "action" in k
    ]
    instrument_changes = [
        InstrumentChange(beat=float(c["beat"]), instrument=c["instrument"])
        for c in meta.get("instrument_changes", [])
        if "beat" in c and "instrument" in c
    ]

    return Song(
        title=meta.get("title", song_dir.name),
        artist=meta.get("artist", "Unknown"),
        tempo_map=tempo_map,
        sections=list(sections.values()),
        knob_actions=knob_actions,
        instrument_changes=instrument_changes,
        source_dir=str(song_dir),
        notes_disclaimer=meta.get(
            "notes_disclaimer",
            "No verified note transcription has been imported for this song yet. "
            "Use tools/import_midi.py with a legally-obtained MIDI file to add one.",
        ),
    )


def _distribute_events(entries: list[dict], sections: dict[str, Section], is_chord: bool) -> None:
    for e in entries:
        try:
            beat = float(e["beat"])
            section_id = e.get("section")
            target = sections.get(section_id) if section_id else None
            if target is None:
                target = next((s for s in sections.values() if s.start_beat <= beat < s.end_beat), None)
            if target is None:
                continue
            verification = NoteVerification(e.get("verification", "placeholder"))
            if is_chord:
                target.notes.append(
                    ChordEvent(
                        beat=beat,
                        duration_beats=float(e.get("duration_beats", 1.0)),
                        name=e.get("name", "?"),
                        notes=list(e["notes"]),
                        layer=e.get("layer", "chords"),
                        verification=verification,
                    )
                )
            else:
                target.notes.append(
                    NoteEvent(
                        beat=beat,
                        duration_beats=float(e.get("duration_beats", 1.0)),
                        note=int(e["note"]),
                        velocity=float(e.get("velocity", 0.9)),
                        layer=e.get("layer", "melody"),
                        verification=verification,
                    )
                )
        except (KeyError, ValueError, TypeError) as exc:
            logger.warning("skipping malformed note/chord entry %r: %s", e, exc)

    for sec in sections.values():
        sec.notes.sort(key=lambda ev: ev.beat)


def load_song_mapping_overrides(song_dir: Path) -> dict:
    return _read_yaml(Path(song_dir) / "mappings.yaml")


def list_available_songs(songs_root: Path) -> list[str]:
    songs_root = Path(songs_root)
    if not songs_root.exists():
        return []
    return sorted(p.name for p in songs_root.iterdir() if p.is_dir() and (p / "song.yaml").exists())

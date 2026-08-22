"""Practice-sheet and keymap export (spec section 22).

Generates a deterministic, human-readable performance sheet directly from
a Song's actual data -- never invented. A section with no verified notes
renders as explicitly empty/placeholder rather than fabricated content.
"""

from __future__ import annotations

import json
from pathlib import Path

from ..music.mapping import KeyboardMapping
from ..music.notes import midi_to_name
from .model import ChordEvent, NoteEvent, Song

SUBDIVISIONS_PER_BEAT = 4  # 16th-note grid resolution for the bar layout


def _event_label(event: NoteEvent | ChordEvent, mapping: KeyboardMapping) -> str:
    if isinstance(event, ChordEvent):
        keys = []
        for n in event.notes:
            found = mapping.find_key_for_note(n)
            keys.append(found[0] if found else "?")
        return "[" + "+".join(keys) + "]"
    found = mapping.find_key_for_note(event.note)
    key = found[0] if found else "?"
    return f"{key}({midi_to_name(event.note)})"


def _render_bars(song: Song, section, events: list, mapping: KeyboardMapping) -> list[str]:
    beats_per_bar = song.tempo_map.time_signature[0]
    slots_per_bar = beats_per_bar * SUBDIVISIONS_PER_BEAT
    start_bar = int(section.start_beat // beats_per_bar)
    end_bar = int(section.end_beat // beats_per_bar) + (1 if section.end_beat % beats_per_bar else 0)

    events_sorted = sorted(events, key=lambda e: e.beat)
    idx = 0
    out = []
    for bar_num in range(start_bar, end_bar):
        bar_start_beat = bar_num * beats_per_bar
        slots = ["."] * slots_per_bar
        while idx < len(events_sorted) and events_sorted[idx].beat < bar_start_beat + beats_per_bar:
            e = events_sorted[idx]
            rel_beat = e.beat - bar_start_beat
            slot_idx = max(0, min(slots_per_bar - 1, round(rel_beat * SUBDIVISIONS_PER_BEAT)))
            slots[slot_idx] = _event_label(e, mapping)
            idx += 1
        beat_groups = [slots[i:i + SUBDIVISIONS_PER_BEAT] for i in range(0, slots_per_bar, SUBDIVISIONS_PER_BEAT)]
        row = " | ".join(" ".join(g) for g in beat_groups)
        out.append(f"Bar {bar_num + 1}: {row}")
    return out


def generate_practice_sheet(song: Song, mapping: KeyboardMapping, speed_percent: float = 100.0) -> str:
    lines = [f"# {song.title.upper()} -- Practice Sheet", f"Artist: {song.artist}", f"Speed: {speed_percent:.0f}%", ""]
    if song.notes_disclaimer:
        lines += [f"> **STATUS:** {song.notes_disclaimer}", ""]

    for section in song.sections:
        bpm = song.tempo_map.bpm_at_beat(section.start_beat)
        bar1, _ = song.tempo_map.beat_to_bar_beat(section.start_beat)
        bar2, _ = song.tempo_map.beat_to_bar_beat(section.end_beat)
        lines += [
            f"## Section: {section.name} (difficulty: {section.difficulty})",
            f"Bars {bar1}-{bar2}  (beats {section.start_beat:.0f}-{section.end_beat:.0f})  @ {bpm:.0f} BPM",
            "",
        ]

        layers = sorted({e.layer for e in section.notes})
        if not layers:
            lines += ["_(No verified notes in this section yet -- see NOTES_STATUS.md / docs/ADDING_SONGS.md.)_", ""]
        for layer in layers:
            layer_events = [e for e in section.notes if e.layer == layer]
            verified = all(e.verification.value == "verified" for e in layer_events)
            status = "verified" if verified else "PLACEHOLDER -- not yet verified"
            lines += [f"### {layer.title()}  ({status})", *_render_bars(song, section, layer_events, mapping), ""]

        knob_here = [k for k in song.knob_actions if section.start_beat <= k.beat < section.end_beat]
        if knob_here:
            lines.append("### Knob actions")
            for k in knob_here:
                bar, beat = song.tempo_map.beat_to_bar_beat(k.beat)
                extra = f" (amount={k.amount})" if k.amount else ""
                lines.append(f"- Bar {bar}, beat {beat:.2f}: {k.action}{extra}")
            lines.append("")

        instr_here = [c for c in song.instrument_changes if section.start_beat <= c.beat < section.end_beat]
        if instr_here:
            lines.append("### Instrument changes")
            for c in instr_here:
                bar, beat = song.tempo_map.beat_to_bar_beat(c.beat)
                lines.append(f"- Bar {bar}, beat {beat:.2f}: switch to {c.instrument}")
            lines.append("")

    return "\n".join(lines)


def export_keymap_json(mapping: KeyboardMapping) -> dict:
    return {
        "octave_shift": mapping.octave_shift,
        "notes": {key: {"midi": note, "name": midi_to_name(note)} for key, note in mapping.note_map.items()},
        "controls": mapping.control_map,
    }


def export_keymap_txt(mapping: KeyboardMapping) -> str:
    lines = ["QWERTY -> MUSICAL NOTE MAP", "=" * 40, ""]
    for key, note in sorted(mapping.note_map.items(), key=lambda kv: kv[1]):
        lines.append(f"{key:12s} -> {midi_to_name(note):5s} (MIDI {note})")
    lines += ["", "CONTROLS", "=" * 40, ""]
    for key, control in mapping.control_map.items():
        lines.append(f"{key:12s} -> {control}")
    return "\n".join(lines)


def write_all_exports(song: Song, mapping: KeyboardMapping, exports_dir: Path, speed_percent: float = 100.0, filename_stem: str | None = None) -> dict[str, Path]:
    exports_dir = Path(exports_dir)
    exports_dir.mkdir(parents=True, exist_ok=True)
    stem = filename_stem or song.title.lower().replace(" ", "_")

    sheet_path = exports_dir / f"{stem}_practice_sheet.md"
    sheet_path.write_text(generate_practice_sheet(song, mapping, speed_percent), encoding="utf-8")

    keymap_json_path = exports_dir / "keymap.json"
    keymap_json_path.write_text(json.dumps(export_keymap_json(mapping), indent=2), encoding="utf-8")

    keymap_txt_path = exports_dir / "keymap.txt"
    keymap_txt_path.write_text(export_keymap_txt(mapping), encoding="utf-8")

    return {"practice_sheet": sheet_path, "keymap_json": keymap_json_path, "keymap_txt": keymap_txt_path}

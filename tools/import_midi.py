"""Interactive MIDI importer: inspect a MIDI file and convert selected
tracks into this project's notes.json format for a song directory.

Never downloads or fabricates anything -- it only reads a MIDI file you
already have locally and point it at (spec section 13/57).

Usage:
    .venv\\Scripts\\python.exe tools\\import_midi.py path\\to\\file.mid
    .venv\\Scripts\\python.exe tools\\import_midi.py path\\to\\file.mid --song-dir songs\\instant_crush
    .venv\\Scripts\\python.exe tools\\import_midi.py path\\to\\file.mid --non-interactive --tracks 1,2 --layer melody
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from qwerty_instrument.songs.midi_import import extract_notes, inspect_midi, notes_to_json_entries


def print_inspection(info) -> None:
    print(f"\nticks_per_beat: {info.ticks_per_beat}")
    print(f"duration: {info.duration_seconds:.1f}s")
    print("\ntempo events:")
    for t in info.tempo_events:
        print(f"  beat {t.beat:.2f}: {t.bpm:.2f} BPM")
    print("\ntracks:")
    for t in info.tracks:
        instruments = ", ".join(t.instrument_names) if t.instrument_names else "(no program_change / percussion?)"
        print(f"  [{t.index}] {t.name!r:30s} channels={t.channels} notes={t.note_count:5d} instruments: {instruments}")


def prompt_int_list(prompt: str) -> list[int]:
    raw = input(prompt).strip()
    if not raw:
        return []
    return [int(x.strip()) for x in raw.split(",") if x.strip()]


def interactive_flow(info, midi_path: str) -> None:
    while True:
        track_indices = prompt_int_list("\nWhich track index/indices to import (comma-separated, or blank to quit)? ")
        if not track_indices:
            print("No tracks selected; exiting.")
            return
        layer = input("Layer for these notes [melody/chords/bass/lead_guitar] (default: melody): ").strip() or "melody"
        transpose_raw = input("Transpose in semitones (default: 0): ").strip()
        transpose = int(transpose_raw) if transpose_raw else 0

        notes = extract_notes(midi_path, track_indices, transpose=transpose, layer=layer)
        print(f"\nExtracted {len(notes)} notes from track(s) {track_indices} as layer {layer!r} (transpose={transpose:+d}).")
        if notes:
            print(f"First note: beat={notes[0].beat:.2f} note={notes[0].note}")
            print(f"Last note:  beat={notes[-1].beat:.2f} note={notes[-1].note}")

        section = input("Assign these notes to which section id (e.g. 'chorus', 'lead_guitar')? Leave blank to skip: ").strip()
        out_path_raw = input("Write to notes.json path (blank to skip writing): ").strip()
        if out_path_raw:
            out_path = Path(out_path_raw)
            existing = {"notes": [], "chords": []}
            if out_path.exists():
                try:
                    existing = json.loads(out_path.read_text(encoding="utf-8"))
                except Exception:
                    print(f"WARNING: could not parse existing {out_path}, starting fresh.")
            entries = notes_to_json_entries(notes, section_id=section or None)
            existing.setdefault("notes", []).extend(entries)
            out_path.parent.mkdir(parents=True, exist_ok=True)
            out_path.write_text(json.dumps(existing, indent=2), encoding="utf-8")
            print(f"Wrote/updated {out_path} ({len(entries)} notes added).")

        again = input("\nImport another track selection from this file? [y/N] ").strip().lower()
        if again != "y":
            return


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("midi_path", help="path to a legally-obtained .mid file")
    parser.add_argument("--song-dir", help="song directory to write notes.json into (default: derived from --tracks writing to ./notes.json in cwd)")
    parser.add_argument("--non-interactive", action="store_true")
    parser.add_argument("--tracks", help="comma-separated track indices (non-interactive mode)")
    parser.add_argument("--layer", default="melody")
    parser.add_argument("--section", default=None)
    parser.add_argument("--transpose", type=int, default=0)
    args = parser.parse_args()

    if not Path(args.midi_path).exists():
        print(f"File not found: {args.midi_path}")
        return 1

    info = inspect_midi(args.midi_path)
    print_inspection(info)

    if args.non_interactive:
        if not args.tracks:
            print("\n--non-interactive requires --tracks")
            return 1
        track_indices = [int(x) for x in args.tracks.split(",")]
        notes = extract_notes(args.midi_path, track_indices, transpose=args.transpose, layer=args.layer)
        print(f"\nExtracted {len(notes)} notes from track(s) {track_indices}.")
        if args.song_dir:
            out_path = Path(args.song_dir) / "notes.json"
            existing = {"notes": [], "chords": []}
            if out_path.exists():
                existing = json.loads(out_path.read_text(encoding="utf-8"))
            existing.setdefault("notes", []).extend(notes_to_json_entries(notes, section_id=args.section))
            out_path.parent.mkdir(parents=True, exist_ok=True)
            out_path.write_text(json.dumps(existing, indent=2), encoding="utf-8")
            print(f"Wrote {out_path}")
        return 0

    interactive_flow(info, args.midi_path)
    return 0


if __name__ == "__main__":
    sys.exit(main())

"""Promote reference-analysis candidates into the canonical runtime song
format, ONLY for events that clear a confidence threshold.

This is the one path by which songs/instant_crush/notes.json may ever
contain reference-derived (not hand-invented) musical content. Anything
promoted is marked verification="reference_derived" (never "verified")
and keeps its confidence/source/exact-seconds provenance.

If nothing clears the threshold, this correctly produces an empty
promotion -- that's not a bug, it's the point: low-confidence guesses
should not become "canonical playback data" just because a script ran.

Usage:
    .venv\\Scripts\\python.exe tools\\build_reference_song.py --section chorus
    .venv\\Scripts\\python.exe tools\\build_reference_song.py --section chorus --min-confidence 0.6 --layers melody,harmony
    .venv\\Scripts\\python.exe tools\\build_reference_song.py --section chorus --dry-run
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import yaml

ANALYSIS_DIR = Path("songs/instant_crush/reference_analysis")
SONG_DIR = Path("songs/instant_crush")

DEFAULT_MIN_CONFIDENCE = {"melody": 0.5, "bass": 0.5, "harmony": 0.5}


def load_tempo_bpm(song_dir: Path) -> float:
    meta = yaml.safe_load((song_dir / "song.yaml").read_text(encoding="utf-8"))
    return float(meta["tempo_events"][0]["bpm"])


def promote_melody_or_bass(candidates: list[dict], layer: str, bpm: float, min_confidence: float) -> list[dict]:
    out = []
    for c in candidates:
        if c["confidence"] < min_confidence:
            continue
        beat = c["start_seconds"] * bpm / 60.0
        duration_beats = c["duration_seconds"] * bpm / 60.0
        out.append(
            {
                "beat": round(beat, 4),
                "duration_beats": round(max(0.01, duration_beats), 4),
                "note": c["nearest_midi_note"],
                "velocity": 0.8,
                "layer": layer,
                "verification": "reference_derived",
                "start_seconds": c["start_seconds"],
                "duration_seconds": c["duration_seconds"],
                "confidence": c["confidence"],
                "source": "local_reference_audio",
            }
        )
    return out


def _merge_consecutive_same_chord(candidates: list[dict]) -> list[dict]:
    """harmony_candidates() emits one estimate per beat (~0.5-0.6s each).
    Promoting each of those individually would re-create exactly the
    "choppy stab every beat" articulation bug fixed in accuracy pass v2 --
    a real chord doesn't re-attack every beat just because we re-measured
    it. Merge consecutive beats sharing the same root+quality into one
    sustained span instead (spec Phase 16: prefer a sustained voice over a
    new NOTE_ON when there's no actual evidence of a re-attack -- here the
    evidence for continuity is that both neighboring beats' independent
    chroma measurements agree). Confidence of the merged span is the mean
    of its constituent beats.
    """
    if not candidates:
        return []
    ordered = sorted(candidates, key=lambda c: c["start_seconds"])
    merged = [dict(ordered[0], _confidences=[ordered[0]["confidence"]])]
    for c in ordered[1:]:
        last = merged[-1]
        same_chord = c["root_pitch_class"] == last["root_pitch_class"] and c["quality"] == last["quality"]
        adjacent = abs(c["start_seconds"] - last["end_seconds"]) < 0.05
        if same_chord and adjacent:
            last["end_seconds"] = c["end_seconds"]
            last["_confidences"].append(c["confidence"])
        else:
            merged.append(dict(c, _confidences=[c["confidence"]]))
    for m in merged:
        m["confidence"] = round(sum(m["_confidences"]) / len(m["_confidences"]), 3)
        del m["_confidences"]
    return merged


def promote_harmony(candidates: list[dict], bpm: float, min_confidence: float) -> list[dict]:
    """Chord roots only have a root+quality label, not a specific voicing --
    a defensible generic close-position triad is built from root+quality,
    clearly still reference_derived/low-confidence, never "verified"."""
    pitch_class_to_semitone = {"C": 0, "C#": 1, "D": 2, "D#": 3, "E": 4, "F": 5, "F#": 6, "G": 7, "G#": 8, "A": 9, "A#": 10, "B": 11}
    quality_intervals = {"maj": [0, 4, 7], "min": [0, 3, 7]}
    out = []
    for c in _merge_consecutive_same_chord(candidates):
        if c["confidence"] < min_confidence:
            continue
        root_semitone = pitch_class_to_semitone[c["root_pitch_class"]]
        base_note = 48 + root_semitone  # anchor around C3 -- generic, not a claim of the real voicing register
        notes = [base_note + iv for iv in quality_intervals[c["quality"]]]
        beat = c["start_seconds"] * bpm / 60.0
        duration_beats = (c["end_seconds"] - c["start_seconds"]) * bpm / 60.0
        out.append(
            {
                "beat": round(beat, 4),
                "duration_beats": round(max(0.01, duration_beats), 4),
                "name": f"{c['root_pitch_class']}{c['quality']}",
                "notes": notes,
                "layer": "chords",
                "verification": "reference_derived",
                "start_seconds": c["start_seconds"],
                "duration_seconds": round(c["end_seconds"] - c["start_seconds"], 4),
                "confidence": c["confidence"],
                "source": "local_reference_audio",
            }
        )
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--section", default="chorus")
    parser.add_argument("--layers", default="melody,bass,harmony", help="comma-separated: melody,bass,harmony")
    parser.add_argument("--min-confidence", type=float, default=None, help="overrides all per-layer defaults if set")
    parser.add_argument("--dry-run", action="store_true", help="report what would be promoted without writing notes.json")
    args = parser.parse_args()

    candidates_dir = ANALYSIS_DIR / "candidates"
    if not candidates_dir.exists():
        print(f"No analysis candidates found at {candidates_dir}. Run tools/analyze_reference.py first.")
        return 1

    bpm = load_tempo_bpm(SONG_DIR)
    layers = [l.strip() for l in args.layers.split(",")]

    notes_entries, chord_entries = [], []
    summary = {}

    if "melody" in layers:
        path = candidates_dir / "melody.json"
        cands = json.loads(path.read_text(encoding="utf-8")) if path.exists() else []
        min_conf = args.min_confidence if args.min_confidence is not None else DEFAULT_MIN_CONFIDENCE["melody"]
        promoted = promote_melody_or_bass(cands, "melody", bpm, min_conf)
        for p in promoted:
            p["section"] = args.section
        notes_entries.extend(promoted)
        summary["melody"] = f"{len(promoted)}/{len(cands)} candidates promoted (min_confidence={min_conf})"

    if "bass" in layers:
        path = candidates_dir / "bass.json"
        cands = json.loads(path.read_text(encoding="utf-8")) if path.exists() else []
        min_conf = args.min_confidence if args.min_confidence is not None else DEFAULT_MIN_CONFIDENCE["bass"]
        promoted = promote_melody_or_bass(cands, "bass", bpm, min_conf)
        for p in promoted:
            p["section"] = args.section
        notes_entries.extend(promoted)
        summary["bass"] = f"{len(promoted)}/{len(cands)} candidates promoted (min_confidence={min_conf})"

    if "harmony" in layers:
        path = candidates_dir / "harmony.json"
        cands = json.loads(path.read_text(encoding="utf-8")) if path.exists() else []
        min_conf = args.min_confidence if args.min_confidence is not None else DEFAULT_MIN_CONFIDENCE["harmony"]
        promoted = promote_harmony(cands, bpm, min_conf)
        for p in promoted:
            p["section"] = args.section
        chord_entries.extend(promoted)
        summary["harmony"] = f"{len(promoted)}/{len(cands)} candidates promoted (min_confidence={min_conf})"

    print("Promotion summary:")
    for layer, msg in summary.items():
        print(f"  {layer}: {msg}")

    total = len(notes_entries) + len(chord_entries)
    if total == 0:
        print("\nNothing cleared the confidence threshold -- notes.json NOT modified.")
        print("This is expected/correct if the reference analysis is low-confidence (see the report).")
        return 0

    if args.dry_run:
        print(f"\n--dry-run: would write {total} events to {SONG_DIR / 'notes.json'} (not writing).")
        return 0

    out_path = SONG_DIR / "notes.json"
    existing = {"notes": [], "chords": []}
    if out_path.exists():
        try:
            existing = json.loads(out_path.read_text(encoding="utf-8"))
        except Exception:
            pass

    # Re-running promotion for the same section+layer(s) must REPLACE the
    # previous promotion, not accumulate duplicates on top of it -- strip
    # any existing reference_derived entries matching this run's
    # section+layer before appending the fresh ones. Non-matching entries
    # (other sections/layers, or hand-authored/verified data) are untouched.
    replaced_layers = set(layers) & {"melody", "bass", "harmony"}
    note_layers_replaced = replaced_layers & {"melody", "bass"}
    chord_layers_replaced = {"chords"} if "harmony" in replaced_layers else set()

    def keep_note(e: dict) -> bool:
        return not (e.get("section") == args.section and e.get("layer") in note_layers_replaced and e.get("verification") == "reference_derived")

    def keep_chord(e: dict) -> bool:
        return not (e.get("section") == args.section and e.get("layer") in chord_layers_replaced and e.get("verification") == "reference_derived")

    existing["notes"] = [e for e in existing.get("notes", []) if keep_note(e)]
    existing["chords"] = [e for e in existing.get("chords", []) if keep_chord(e)]

    existing["notes"].extend(notes_entries)
    existing["chords"].extend(chord_entries)
    out_path.write_text(json.dumps(existing, indent=2), encoding="utf-8")
    print(f"\nWrote {total} reference_derived events to {out_path} (replacing any prior promotion for this section+layer)")
    return 0


if __name__ == "__main__":
    sys.exit(main())

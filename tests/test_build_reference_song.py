"""tools/build_reference_song.py: promotion must clear a confidence
threshold, merge consecutive same-chord estimates into sustained spans
(not one event per analysis window), and REPLACE any prior promotion for
the same section+layer on re-run rather than accumulating duplicates on
top of it (a real bug found and fixed during accuracy pass v3.2 --
running the tool twice for the same section silently doubled the chord
data in notes.json)."""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))

import build_reference_song as brs


def test_merge_consecutive_same_chord_combines_adjacent_matching_windows():
    candidates = [
        {"start_seconds": 0.0, "end_seconds": 0.25, "root_pitch_class": "A", "quality": "min", "confidence": 0.6},
        {"start_seconds": 0.25, "end_seconds": 0.5, "root_pitch_class": "A", "quality": "min", "confidence": 0.8},
        {"start_seconds": 0.5, "end_seconds": 0.75, "root_pitch_class": "C", "quality": "maj", "confidence": 0.7},
    ]
    merged = brs._merge_consecutive_same_chord(candidates)
    assert len(merged) == 2
    assert merged[0]["start_seconds"] == 0.0
    assert merged[0]["end_seconds"] == 0.5  # two A-min windows combined into one span
    assert merged[0]["confidence"] == 0.7  # mean of 0.6 and 0.8
    assert merged[1]["root_pitch_class"] == "C"


def test_merge_does_not_combine_non_adjacent_windows_even_if_same_chord():
    candidates = [
        {"start_seconds": 0.0, "end_seconds": 0.25, "root_pitch_class": "A", "quality": "min", "confidence": 0.6},
        {"start_seconds": 5.0, "end_seconds": 5.25, "root_pitch_class": "A", "quality": "min", "confidence": 0.6},
    ]
    merged = brs._merge_consecutive_same_chord(candidates)
    assert len(merged) == 2  # a 4.75s gap is not "the same sustained chord"


def test_promote_harmony_only_keeps_events_at_or_above_threshold():
    candidates = [
        {"start_seconds": 0.0, "end_seconds": 0.5, "root_pitch_class": "C", "quality": "maj", "confidence": 0.8},
        {"start_seconds": 0.5, "end_seconds": 1.0, "root_pitch_class": "D", "quality": "min", "confidence": 0.3},
    ]
    promoted = brs.promote_harmony(candidates, bpm=120.0, min_confidence=0.5)
    assert len(promoted) == 1
    assert promoted[0]["name"] == "Cmaj"
    assert promoted[0]["verification"] == "reference_derived"


def test_rerunning_promotion_replaces_rather_than_duplicates(tmp_path, monkeypatch):
    """The actual bug: running the tool twice for the same section used to
    leave both the old and new promoted events in notes.json."""
    song_dir = tmp_path / "test_song"
    (song_dir / "reference_analysis" / "candidates").mkdir(parents=True)
    (song_dir / "reference_analysis" / "candidates" / "harmony.json").write_text(
        json.dumps([{"start_seconds": 0.0, "end_seconds": 4.0, "root_pitch_class": "C", "quality": "maj", "confidence": 0.9}]),
        encoding="utf-8",
    )
    (song_dir / "song.yaml").write_text('title: "T"\ntempo_events:\n  - beat: 0\n    bpm: 120.0\n', encoding="utf-8")

    monkeypatch.setattr(brs, "SONG_DIR", song_dir)
    monkeypatch.setattr(brs, "ANALYSIS_DIR", song_dir / "reference_analysis")

    for _ in range(2):  # run promotion twice for the same section+layer
        argv_backup = sys.argv
        sys.argv = ["build_reference_song.py", "--section", "chorus", "--layers", "harmony", "--min-confidence", "0.5"]
        try:
            brs.main()
        finally:
            sys.argv = argv_backup

    data = json.loads((song_dir / "notes.json").read_text(encoding="utf-8"))
    assert len(data["chords"]) == 1  # not 2 -- the second run replaced, didn't accumulate

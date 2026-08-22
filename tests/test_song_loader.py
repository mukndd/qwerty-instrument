import json

import yaml

from qwerty_instrument.songs.loader import list_available_songs, load_song
from qwerty_instrument.songs.model import NoteVerification


def make_synthetic_song(tmp_path):
    song_dir = tmp_path / "test_song"
    song_dir.mkdir()
    (song_dir / "song.yaml").write_text(
        yaml.safe_dump(
            {
                "title": "Synthetic Test Song",
                "artist": "Test Fixture",
                "time_signature": [4, 4],
                "tempo_events": [{"beat": 0, "bpm": 120}],
            }
        ),
        encoding="utf-8",
    )
    (song_dir / "sections.yaml").write_text(
        yaml.safe_dump({"sections": [{"id": "verse", "name": "Verse", "start_beat": 0, "end_beat": 8, "difficulty": "easy", "loop_default": True}]}),
        encoding="utf-8",
    )
    notes = {
        "notes": [
            {"beat": 0.0, "duration_beats": 1.0, "note": 60, "velocity": 0.9, "layer": "melody", "section": "verse", "verification": "verified"},
            {"beat": 1.0, "duration_beats": 1.0, "note": 62, "layer": "melody", "section": "verse", "verification": "verified"},
        ],
        "chords": [
            {"beat": 0.0, "duration_beats": 4.0, "name": "Cmaj", "notes": [48, 52, 55], "layer": "chords", "section": "verse", "verification": "verified"},
        ],
    }
    (song_dir / "notes.json").write_text(json.dumps(notes), encoding="utf-8")
    return song_dir


def test_load_synthetic_song(tmp_path):
    song_dir = make_synthetic_song(tmp_path)
    song = load_song(song_dir)
    assert song.title == "Synthetic Test Song"
    assert len(song.sections) == 1
    section = song.sections[0]
    assert section.start_beat == 0
    assert section.end_beat == 8
    melody_notes = [n for n in section.notes if n.layer == "melody"]
    assert len(melody_notes) == 2
    assert melody_notes[0].note == 60
    assert melody_notes[0].verification == NoteVerification.VERIFIED


def test_song_without_notes_json_gives_empty_placeholder_sections(tmp_path):
    song_dir = tmp_path / "no_notes_song"
    song_dir.mkdir()
    (song_dir / "song.yaml").write_text(yaml.safe_dump({"title": "No Notes Yet", "artist": "?"}), encoding="utf-8")
    (song_dir / "sections.yaml").write_text(yaml.safe_dump({"sections": [{"id": "chorus", "name": "Chorus", "start_beat": 0, "end_beat": 32}]}), encoding="utf-8")
    song = load_song(song_dir)
    assert len(song.sections) == 1
    assert song.sections[0].notes == []
    assert "no verified" in song.notes_disclaimer.lower()


def test_malformed_section_entry_is_skipped_not_fatal(tmp_path):
    song_dir = tmp_path / "bad_song"
    song_dir.mkdir()
    (song_dir / "song.yaml").write_text(yaml.safe_dump({"title": "Bad"}), encoding="utf-8")
    (song_dir / "sections.yaml").write_text(
        yaml.safe_dump({"sections": [{"id": "ok", "start_beat": 0, "end_beat": 4}, {"id": "missing_end_beat"}]}), encoding="utf-8"
    )
    song = load_song(song_dir)
    assert len(song.sections) == 1
    assert song.sections[0].id == "ok"


def test_list_available_songs(tmp_path):
    make_synthetic_song(tmp_path)
    (tmp_path / "not_a_song").mkdir()  # no song.yaml, should be excluded
    songs = list_available_songs(tmp_path)
    assert songs == ["test_song"]

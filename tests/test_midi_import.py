"""MIDI import tests use a synthetic, code-generated MIDI fixture only --
never copyrighted song data (spec section 34)."""

import mido
import pytest

from qwerty_instrument.songs.midi_import import extract_notes, inspect_midi
from qwerty_instrument.songs.model import NoteVerification


@pytest.fixture
def synthetic_midi_path(tmp_path):
    mid = mido.MidiFile(ticks_per_beat=480)

    meta = mido.MidiTrack()
    mid.tracks.append(meta)
    meta.append(mido.MetaMessage("track_name", name="Meta", time=0))
    meta.append(mido.MetaMessage("set_tempo", tempo=mido.bpm2tempo(100), time=0))

    melody = mido.MidiTrack()
    mid.tracks.append(melody)
    melody.append(mido.MetaMessage("track_name", name="Melody", time=0))
    melody.append(mido.Message("program_change", program=80, channel=0, time=0))
    melody.append(mido.Message("note_on", note=60, velocity=100, channel=0, time=0))
    melody.append(mido.Message("note_off", note=60, velocity=0, channel=0, time=480))
    melody.append(mido.Message("note_on", note=64, velocity=90, channel=0, time=0))
    melody.append(mido.Message("note_off", note=64, velocity=0, channel=0, time=480))

    bass = mido.MidiTrack()
    mid.tracks.append(bass)
    bass.append(mido.MetaMessage("track_name", name="Bass", time=0))
    bass.append(mido.Message("program_change", program=33, channel=1, time=0))
    bass.append(mido.Message("note_on", note=36, velocity=110, channel=1, time=0))
    bass.append(mido.Message("note_off", note=36, velocity=0, channel=1, time=960))

    path = tmp_path / "synthetic.mid"
    mid.save(str(path))
    return str(path)


def test_inspect_midi_reports_tracks_and_tempo(synthetic_midi_path):
    info = inspect_midi(synthetic_midi_path)
    assert info.ticks_per_beat == 480
    assert len(info.tracks) == 3
    assert info.tempo_events[0].bpm == pytest.approx(100.0)

    melody_track = next(t for t in info.tracks if t.name == "Melody")
    assert melody_track.note_count == 2
    assert 80 in melody_track.programs
    assert "Lead 1 (square)" in melody_track.instrument_names

    bass_track = next(t for t in info.tracks if t.name == "Bass")
    assert bass_track.note_count == 1
    assert "Electric Bass (finger)" in bass_track.instrument_names


def test_extract_notes_preserves_timing_and_marks_verified(synthetic_midi_path):
    info = inspect_midi(synthetic_midi_path)
    melody_idx = next(t.index for t in info.tracks if t.name == "Melody")
    notes = extract_notes(synthetic_midi_path, [melody_idx])
    assert len(notes) == 2
    assert notes[0].note == 60
    assert notes[0].beat == pytest.approx(0.0)
    assert notes[0].duration_beats == pytest.approx(1.0)
    assert notes[1].note == 64
    assert notes[1].beat == pytest.approx(1.0)
    assert all(n.verification == NoteVerification.VERIFIED for n in notes)


def test_extract_notes_applies_transpose(synthetic_midi_path):
    info = inspect_midi(synthetic_midi_path)
    melody_idx = next(t.index for t in info.tracks if t.name == "Melody")
    notes = extract_notes(synthetic_midi_path, [melody_idx], transpose=-12)
    assert notes[0].note == 48
    assert notes[1].note == 52


def test_extract_notes_multiple_tracks_combined_and_sorted(synthetic_midi_path):
    info = inspect_midi(synthetic_midi_path)
    all_indices = [t.index for t in info.tracks if t.note_count > 0]
    notes = extract_notes(synthetic_midi_path, all_indices)
    assert len(notes) == 3
    assert notes == sorted(notes, key=lambda n: n.beat)

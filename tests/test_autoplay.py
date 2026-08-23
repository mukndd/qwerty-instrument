"""Tests for SongTrainer's AUTOPLAY mode: song data -> scheduler ->
MusicEvent, using the exact same event_sink path manual QWERTY playing
uses (no second audio architecture -- see docs/ARCHITECTURE.md)."""

import time
from pathlib import Path

import pytest

from qwerty_instrument.music.events import EventType
from qwerty_instrument.music.mapping import KeyboardMapping
from qwerty_instrument.music.timing import TempoMap
from qwerty_instrument.songs.loader import load_song
from qwerty_instrument.songs.model import ChordEvent, NoteEvent, Section, Song
from qwerty_instrument.songs.trainer import PracticeMode, SongTrainer


def make_song():
    section = Section(id="verse", name="Verse", start_beat=0, end_beat=8, difficulty="easy", loop_default=True)
    section.notes = [
        NoteEvent(beat=0.0, duration_beats=2.0, note=36, velocity=0.7, layer="bass"),
        NoteEvent(beat=2.0, duration_beats=2.0, note=38, velocity=0.7, layer="bass"),
        ChordEvent(beat=0.0, duration_beats=4.0, name="Test", notes=[60, 63, 67], layer="chords"),
    ]
    return Song(title="Test", artist="?", tempo_map=TempoMap(events=None), sections=[section])


def make_trainer(layers=("chords", "bass")):
    events = []
    trainer = SongTrainer(make_song(), KeyboardMapping(), events.append)
    trainer.load_section("verse")
    trainer.set_mode(PracticeMode.AUTOPLAY)
    trainer.set_autoplay_layers(list(layers))
    return trainer, events


def test_autoplay_emits_note_on_for_due_events():
    trainer, events = make_trainer()
    trainer.start()
    trainer._tick_autoplay(0.0)
    note_ons = [e for e in events if e.type == EventType.NOTE_ON]
    assert len(note_ons) == 4  # chord (3 notes) + bass note, both due at beat 0
    assert {e.note for e in note_ons} == {60, 63, 67, 36}


def test_autoplay_emits_note_off_when_duration_elapses():
    trainer, events = make_trainer(layers=["bass"])
    trainer.start()
    trainer._tick_autoplay(0.0)  # bass note 36 starts, duration_beats=2
    events.clear()
    trainer._tick_autoplay(2.5)  # past its end -> note-off, and the next bass note (38) starts
    note_offs = {e.note for e in events if e.type == EventType.NOTE_OFF}
    note_ons = {e.note for e in events if e.type == EventType.NOTE_ON}
    assert 36 in note_offs
    assert 38 in note_ons


def test_chord_autoplay_emits_all_chord_pitches():
    trainer, events = make_trainer(layers=["chords"])
    trainer.start()
    trainer._tick_autoplay(0.0)
    note_ons = {e.note for e in events if e.type == EventType.NOTE_ON}
    assert note_ons == {60, 63, 67}


def test_chords_and_bass_schedule_together():
    trainer, events = make_trainer(layers=["chords", "bass"])
    trainer.start()
    assert len(trainer._autoplay_queue) == 3  # chord@0 + bass@0 + bass@2
    trainer._tick_autoplay(0.0)
    assert len(trainer._autoplay_queue) == 1  # only bass@2 left pending
    assert len(trainer._autoplay_active) == 2  # chord + first bass note both sounding


def test_stop_clears_active_autoplay_notes():
    trainer, events = make_trainer(layers=["chords"])
    trainer.start()
    trainer._tick_autoplay(0.0)
    assert trainer.autoplay_sounding_notes == {60, 63, 67}
    events.clear()
    trainer.stop()
    note_offs = {e.note for e in events if e.type == EventType.NOTE_OFF}
    assert note_offs == {60, 63, 67}
    assert trainer.autoplay_sounding_notes == set()
    assert trainer._autoplay_active == []


def test_loop_does_not_leave_stuck_notes():
    """A chord still ringing when the section boundary is hit must be
    forcibly released before the loop restarts."""
    section = Section(id="s", name="S", start_beat=0, end_beat=8, loop_default=True)
    section.notes = [ChordEvent(beat=6.0, duration_beats=4.0, name="X", notes=[60, 64, 67], layer="chords")]
    song = Song(title="T", artist="?", tempo_map=TempoMap(events=None), sections=[section])
    events = []
    trainer = SongTrainer(song, KeyboardMapping(), events.append)
    trainer.load_section("s")
    trainer.set_mode(PracticeMode.AUTOPLAY)
    trainer.set_autoplay_layers(["chords"])
    trainer.loop_enabled = True
    trainer.start()

    trainer._tick_autoplay(6.0)  # chord starts; its natural end (beat 10) is past the section end (8)
    assert trainer.autoplay_sounding_notes == {60, 64, 67}

    events.clear()
    trainer.current_beat = lambda: 8.0  # simulate reaching the section end while the chord still rings
    trainer.tick()

    note_offs = {e.note for e in events if e.type == EventType.NOTE_OFF}
    assert note_offs == {60, 64, 67}
    assert trainer.autoplay_sounding_notes == set()


def test_changing_mode_away_from_autoplay_stops_notes():
    trainer, events = make_trainer(layers=["chords"])
    trainer.start()
    trainer._tick_autoplay(0.0)
    assert trainer.autoplay_sounding_notes
    events.clear()
    trainer.set_mode(PracticeMode.REAL)
    note_offs = {e.note for e in events if e.type == EventType.NOTE_OFF}
    assert note_offs == {60, 63, 67}
    assert trainer.autoplay_sounding_notes == set()


def test_speed_scaling_affects_current_beat():
    trainer, events = make_trainer()
    trainer.start()
    trainer._start_perf_time = time.perf_counter() - 2.0  # pretend 2 real seconds have elapsed

    trainer.set_speed_percent(100)
    beat_full_speed = trainer.current_beat()
    trainer.set_speed_percent(50)
    beat_half_speed = trainer.current_beat()

    assert beat_half_speed == pytest.approx(beat_full_speed / 2, rel=0.05)


def test_instant_crush_tempo_loads_as_110_bpm():
    song = load_song(Path("songs/instant_crush"))
    assert song.tempo_map.bpm_at_beat(0) == 110.0


def test_instant_crush_chorus_has_chords_and_bass_marked_placeholder():
    song = load_song(Path("songs/instant_crush"))
    chorus = song.section_by_id("chorus")
    assert chorus is not None
    layers = {e.layer for e in chorus.notes}
    assert layers == {"chords", "bass"}
    assert all(e.verification.value == "placeholder" for e in chorus.notes)

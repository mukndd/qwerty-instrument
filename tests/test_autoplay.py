"""Tests for SongTrainer's AUTOPLAY mode: song data -> scheduler ->
MusicEvent, using the exact same event_sink path manual QWERTY playing
uses (no second audio architecture -- see docs/ARCHITECTURE.md)."""

import time
from pathlib import Path

import pytest

from qwerty_instrument.music.events import EventType, MusicEvent, Source
from qwerty_instrument.music.mapping import KeyboardMapping
from qwerty_instrument.music.timing import TempoMap
from qwerty_instrument.songs.loader import load_song
from qwerty_instrument.songs.model import ChordEvent, NoteEvent, Section, Song
from qwerty_instrument.songs.trainer import PracticeMode, SongTrainer


def ev(etype, note=60, velocity=0.8, key_id="k0", **metadata):
    md = {"key_id": key_id, **metadata}
    return MusicEvent(type=etype, note=note, velocity=velocity, timestamp_ns=time.perf_counter_ns(), source=Source.QWERTY, metadata=md)


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


def test_instant_crush_chorus_has_chords_bass_melody_marked_placeholder():
    song = load_song(Path("songs/instant_crush"))
    chorus = song.section_by_id("chorus")
    assert chorus is not None
    layers = {e.layer for e in chorus.notes}
    assert layers == {"chords", "bass", "melody"}
    assert all(e.verification.value == "placeholder" for e in chorus.notes)


def test_no_voice_sounds_during_an_authored_rest():
    """Instant Crush melody bar 1 has a rest at beat 1.5-2.0 (note ends 1.5, next starts 2.0)."""
    song = load_song(Path("songs/instant_crush"))
    real_trainer = SongTrainer(song, KeyboardMapping(), lambda e: None)
    real_trainer.load_section("chorus")
    real_trainer.set_mode(PracticeMode.AUTOPLAY)
    real_trainer.set_autoplay_layers(["melody"])
    real_trainer.start()
    real_trainer._tick_autoplay(0.0)  # F4 starts, duration 1.5 -> ends at beat 1.5
    assert real_trainer.autoplay_sounding_notes == {65}
    real_trainer._tick_autoplay(1.7)  # inside the authored rest (1.5-2.0)
    assert real_trainer.autoplay_sounding_notes == set()  # nothing sounding during the rest
    real_trainer._tick_autoplay(2.0)  # next melody note (Db5=73) begins
    assert real_trainer.autoplay_sounding_notes == {73}


def test_overlapping_layers_remain_independently_active():
    trainer, events = make_trainer(layers=["chords", "bass"])
    trainer.start()
    trainer._tick_autoplay(0.0)
    # chord notes and the bass note were scheduled together but are tracked
    # as independent active entries, not merged into one voice group
    assert len(trainer._autoplay_active) == 2
    trainer._tick_autoplay(1.0)  # well before either's natural end (chord ends at 4, bass at 2) -- both still active
    assert len(trainer._autoplay_active) == 2
    assert trainer.autoplay_sounding_notes == {60, 63, 67, 36}


def test_layer_routing_targets_the_correct_instrument():
    trainer, events = make_trainer(layers=["chords", "bass"])
    trainer.start()
    trainer._tick_autoplay(0.0)
    chord_events = [e for e in events if e.note in (60, 63, 67)]
    bass_events = [e for e in events if e.note == 36]
    assert all(e.metadata["target_instrument"] == "synth_lead" for e in chord_events)
    assert all(e.metadata["target_instrument"] == "bass_synth" for e in bass_events)


def test_note_off_routes_back_to_the_same_backend_as_note_on():
    """Engine-level ownership: a bass NOTE_ON routed to bass_synth must have
    its matching NOTE_OFF released on bass_synth, not whatever instrument
    happens to be 'active' at release time."""
    from qwerty_instrument.audio.bass import BassSynth
    from qwerty_instrument.audio.engine import AudioConfig, AudioEngine
    from qwerty_instrument.audio.synth import SynthLead

    engine = AudioEngine(AudioConfig(sample_rate=48000, block_size=256))
    synth = SynthLead(48000, 256)
    bass = BassSynth(48000, 256)
    engine.register_instrument(synth)  # active_instrument_name = synth_lead (first registered)
    engine.register_instrument(bass)

    on = ev(EventType.NOTE_ON, note=34, key_id="b0")
    on.metadata["target_instrument"] = "bass_synth"
    engine._process_event(on)
    assert bass.active_voice_count == 1
    assert synth.active_voice_count == 0  # never touched the active (synth) instrument

    off = ev(EventType.NOTE_OFF, note=34, key_id="b0")
    off.metadata["target_instrument"] = "bass_synth"
    engine._process_event(off)
    bass.render(256)
    assert bass._owned == {}  # released on bass_synth specifically


def test_full_chorus_schedules_all_three_layers_simultaneously():
    song = load_song(Path("songs/instant_crush"))
    events = []
    trainer = SongTrainer(song, KeyboardMapping(), events.append)
    trainer.load_section("chorus")
    trainer.set_mode(PracticeMode.AUTOPLAY)
    trainer.set_autoplay_layers(["chords", "bass", "melody"])
    trainer.start()
    trainer._tick_autoplay(0.0)
    targets = {e.metadata["target_instrument"] for e in events if e.type == EventType.NOTE_ON}
    assert targets == {"synth_lead", "bass_synth"}  # chords+melody -> synth_lead, bass -> bass_synth
    layers_active = {layer for layer, notes in trainer.autoplay_sounding_by_layer.items() if notes}
    assert layers_active == {"chords", "bass", "melody"}


def test_loop_survives_multiple_iterations_without_stuck_notes():
    song = load_song(Path("songs/instant_crush"))
    events = []
    trainer = SongTrainer(song, KeyboardMapping(), events.append)
    trainer.load_section("chorus")
    trainer.set_mode(PracticeMode.AUTOPLAY)
    trainer.set_autoplay_layers(["chords", "bass", "melody"])
    trainer.loop_enabled = True
    trainer.start()

    # Simulate 3 full loop passes by driving _tick_autoplay across beat
    # ranges and manually invoking the loop-restart path each time.
    for _ in range(3):
        for beat in [b * 0.25 for b in range(0, 128)]:  # 0..32 in 0.25 steps
            trainer._tick_autoplay(beat)
        trainer._stop_autoplay_notes()
        trainer.start(from_beat=0.0)

    assert trainer.autoplay_sounding_notes == set()
    assert trainer._autoplay_active == []


def test_guided_mode_melody_layer_is_populated():
    song = load_song(Path("songs/instant_crush"))
    trainer = SongTrainer(song, KeyboardMapping(), lambda e: None)
    trainer.load_section("chorus")  # defaults to layer="melody"
    trainer.set_mode(PracticeMode.GUIDED)
    trainer.start()
    upcoming = trainer.upcoming_notes(count=3)
    assert len(upcoming) == 3
    assert upcoming[0].event.note == 65  # first melody note, F4
    assert upcoming[0].key_hint is not None  # find_key_for_note() resolved a real key


def test_assist_mode_press_triggers_next_authored_melody_note():
    song = load_song(Path("songs/instant_crush"))
    events = []
    trainer = SongTrainer(song, KeyboardMapping(), events.append)
    trainer.load_section("chorus")  # layer="melody"
    trainer.set_mode(PracticeMode.ASSIST)
    trainer.assist_strict = False
    trainer.start()

    consumed = trainer.handle_performance_key("J", True, time.time_ns())
    assert consumed
    note_ons = [e for e in events if e.type == EventType.NOTE_ON]
    assert len(note_ons) == 1
    assert note_ons[0].note == 65  # the authored first melody pitch, regardless of which assist key was pressed
    assert note_ons[0].metadata["target_instrument"] == "synth_lead"


def test_real_mode_scores_correct_melody_pitch():
    song = load_song(Path("songs/instant_crush"))
    trainer = SongTrainer(song, KeyboardMapping(), lambda e: None)
    trainer.load_section("chorus")  # layer="melody"
    trainer.set_mode(PracticeMode.REAL)
    trainer.start()
    result = trainer.judge_played_note(65, trainer._beat_to_wall_time_s(0.0) * 1e9)
    assert result is not None
    assert result.judgement.value in ("perfect", "good")

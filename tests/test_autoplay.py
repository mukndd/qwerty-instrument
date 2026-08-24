"""Tests for SongTrainer's AUTOPLAY mode: song data -> scheduler ->
MusicEvent, using the exact same event_sink path manual QWERTY playing
uses (no second audio architecture -- see docs/ARCHITECTURE.md)."""

import time
from pathlib import Path

import pytest

from qwerty_instrument.music.events import EventType, MusicEvent, Source
from qwerty_instrument.music.mapping import KeyboardMapping
from qwerty_instrument.music.timing import TempoEvent, TempoMap
from qwerty_instrument.songs.loader import load_song
from qwerty_instrument.songs.midi_import import notes_to_json_entries
from qwerty_instrument.songs.model import ChordEvent, NoteEvent, NoteVerification, Section, Song
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


def test_instant_crush_tempo_loads_as_measured_112_35_bpm():
    """Accuracy pass v3: replaced the 110/120 BPM guesses with a value
    actually measured from the local reference recording (librosa
    beat-tracking + tempogram cross-check, both methods agreed -- see
    songs/instant_crush/reference_analysis/beat_grid.json)."""
    song = load_song(Path("songs/instant_crush"))
    assert song.tempo_map.bpm_at_beat(0) == 112.35


def test_instant_crush_chorus_has_no_synthetic_placeholder_content():
    """Accuracy pass v3: the previous pass's invented chord/melody data was
    archived, not left in canonical playback data (spec: 'no hand-written
    Claude melody should remain in canonical playback data'). Accuracy
    pass v3.1 promoted real reference_derived chord data (Demucs stem
    separation + chroma template matching, confidence >= 0.5) -- every
    event must carry that provenance, never the old synthetic "placeholder"
    content and never silently relabeled "verified"."""
    song = load_song(Path("songs/instant_crush"))
    chorus = song.section_by_id("chorus")
    assert chorus is not None
    assert len(chorus.notes) > 0
    assert all(e.layer == "chords" for e in chorus.notes)  # melody/bass still not promoted (see NOTES_STATUS.md)
    assert all(e.verification == NoteVerification.REFERENCE_DERIVED for e in chorus.notes)
    assert all(e.confidence is not None and e.confidence >= 0.5 for e in chorus.notes)
    assert all(e.source == "local_reference_audio" for e in chorus.notes)


def test_instant_crush_melody_and_bass_are_not_ready_but_chords_are():
    song = load_song(Path("songs/instant_crush"))
    trainer = SongTrainer(song, KeyboardMapping(), lambda e: None)
    trainer.load_section("chorus")
    assert trainer.has_playable_data(["chords"]) is True
    assert trainer.has_playable_data(["melody"]) is False
    assert trainer.has_playable_data(["bass"]) is False
    # Full Chorus (any() semantics): still starts, since chords alone is real
    # content -- melody/bass layers simply schedule nothing rather than a guess.
    assert trainer.has_playable_data(["chords", "bass", "melody"]) is True


def make_song_with_melody():
    section = Section(id="verse", name="Verse", start_beat=0, end_beat=8, difficulty="easy", loop_default=True)
    section.notes = [
        NoteEvent(beat=0.0, duration_beats=1.5, note=65, velocity=0.85, layer="melody"),
        NoteEvent(beat=2.0, duration_beats=1.0, note=73, velocity=0.9, layer="melody"),  # gap 1.5-2.0 = a real rest
        NoteEvent(beat=3.0, duration_beats=1.0, note=70, velocity=0.8, layer="melody"),
    ]
    return Song(title="Synthetic Melody Test", artist="?", tempo_map=TempoMap(events=None), sections=[section])


def test_no_voice_sounds_during_an_authored_rest():
    """A synthetic melody with an explicit gap between notes: nothing
    should sound during the rest, not "sustained until the next note" --
    this was the actual root cause of the earlier "notes persist too
    long" complaint, and is a property of the scheduler, not of any one
    song's data (see accuracy pass v2's audit in git history)."""
    song = make_song_with_melody()
    trainer = SongTrainer(song, KeyboardMapping(), lambda e: None)
    trainer.load_section("verse")
    trainer.set_mode(PracticeMode.AUTOPLAY)
    trainer.set_autoplay_layers(["melody"])
    trainer.start()
    trainer._tick_autoplay(0.0)  # F4 starts, duration 1.5 -> ends at beat 1.5
    assert trainer.autoplay_sounding_notes == {65}
    trainer._tick_autoplay(1.7)  # inside the authored rest (1.5-2.0)
    assert trainer.autoplay_sounding_notes == set()  # nothing sounding during the rest
    trainer._tick_autoplay(2.0)  # next melody note begins
    assert trainer.autoplay_sounding_notes == {73}


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


def make_song_full_chorus():
    section = Section(id="verse", name="Verse", start_beat=0, end_beat=8, difficulty="easy", loop_default=True)
    section.notes = [
        NoteEvent(beat=0.0, duration_beats=4.0, note=34, velocity=0.75, layer="bass"),
        NoteEvent(beat=0.0, duration_beats=1.5, note=65, velocity=0.85, layer="melody"),
        ChordEvent(beat=0.0, duration_beats=4.0, name="Test", notes=[46, 49, 53], layer="chords"),
    ]
    return Song(title="Synthetic Full-Chorus Test", artist="?", tempo_map=TempoMap(events=None), sections=[section])


def test_full_chorus_schedules_all_three_layers_simultaneously():
    song = make_song_full_chorus()
    events = []
    trainer = SongTrainer(song, KeyboardMapping(), events.append)
    trainer.load_section("verse")
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
    """Once real melody data exists for a section (here: a synthetic
    fixture, standing in for a future reference_derived/verified melody --
    Instant Crush itself has none promoted yet, see
    test_instant_crush_has_playable_data_is_false_until_reference_derived_data_exists),
    Guided mode must actually surface it."""
    song = make_song_with_melody()
    trainer = SongTrainer(song, KeyboardMapping(), lambda e: None)
    trainer.load_section("verse")  # defaults to layer="melody"
    trainer.set_mode(PracticeMode.GUIDED)
    trainer.start()
    upcoming = trainer.upcoming_notes(count=3)
    assert len(upcoming) == 3
    assert upcoming[0].event.note == 65
    assert upcoming[0].key_hint is not None  # find_key_for_note() resolved a real key


def test_assist_mode_press_triggers_next_authored_melody_note():
    song = make_song_with_melody()
    events = []
    trainer = SongTrainer(song, KeyboardMapping(), events.append)
    trainer.load_section("verse")  # layer="melody"
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
    song = make_song_with_melody()
    trainer = SongTrainer(song, KeyboardMapping(), lambda e: None)
    trainer.load_section("verse")  # layer="melody"
    trainer.set_mode(PracticeMode.REAL)
    trainer.start()
    result = trainer.judge_played_note(65, trainer._beat_to_wall_time_s(0.0) * 1e9)
    assert result is not None
    assert result.judgement.value in ("perfect", "good")


def make_song_with_reference_timing():
    """A NoteEvent with start_seconds/duration_seconds set (as
    tools/build_reference_song.py would produce) alongside its beat/
    duration_beats -- exact-seconds timing should be preferred when present."""
    section = Section(id="verse", name="Verse", start_beat=0, end_beat=8, difficulty="easy", loop_default=True)
    section.notes = [
        NoteEvent(
            beat=0.0,  # a crude quantized beat estimate
            duration_beats=1.0,
            note=65,
            layer="melody",
            verification=NoteVerification.REFERENCE_DERIVED,
            start_seconds=0.421,  # the actual measured onset -- deliberately not beat-aligned
            duration_seconds=0.340,
            confidence=0.82,
            source="local_reference_audio",
        ),
    ]
    return Song(title="Reference Timing Test", artist="?", tempo_map=TempoMap(events=[TempoEvent(beat=0.0, bpm=120.0)]), sections=[section])


def test_exact_seconds_timing_is_preferred_over_the_beat_grid():
    song = make_song_with_reference_timing()
    trainer = SongTrainer(song, KeyboardMapping(), lambda e: None)
    trainer.load_section("verse")
    trainer.set_mode(PracticeMode.AUTOPLAY)
    trainer.set_autoplay_layers(["melody"])
    trainer.start()

    # At 120 BPM, beat=0.0 (the quantized grid value) would fire immediately
    # at tick(0.0). The real onset is 0.421s in -- at 100% speed that's
    # 0.421 * (120/60) = 0.842 beats, NOT beat 0.
    trainer._tick_autoplay(0.0)
    assert trainer.autoplay_sounding_notes == set(), "fired on the crude beat grid instead of the exact reference onset"
    trainer._tick_autoplay(0.842)
    assert trainer.autoplay_sounding_notes == {65}


def test_exact_seconds_timing_scales_with_practice_speed():
    """50% speed must take ~2x as long (in wall-clock beats-per-second
    terms) to reach the same reference-derived onset as 100% speed --
    proportional scaling via the existing tempo-map beat conversion."""
    song = make_song_with_reference_timing()

    trainer_full = SongTrainer(song, KeyboardMapping(), lambda e: None)
    trainer_full.load_section("verse")
    trainer_full.set_autoplay_layers(["melody"])
    start_b_full, _ = trainer_full._effective_beat_range(song.sections[0].notes[0])

    trainer_half = SongTrainer(song, KeyboardMapping(), lambda e: None)
    trainer_half.load_section("verse")
    trainer_half.set_speed_percent(50)
    start_b_half, _ = trainer_half._effective_beat_range(song.sections[0].notes[0])

    # The effective beat position itself doesn't change with speed (speed
    # only affects how fast current_beat() advances through beats over
    # wall-clock time) -- both should compute the same beat position...
    assert start_b_full == start_b_half
    # ...but reaching that beat position takes proportionally longer in
    # wall-clock time at half speed, via PracticeClock.beats_to_seconds().
    seconds_full = trainer_full.clock.beats_to_seconds(start_b_full)
    seconds_half = trainer_half.clock.beats_to_seconds(start_b_half)
    assert seconds_half == pytest.approx(seconds_full * 2, rel=0.01)


def test_backward_compatibility_beat_only_events_still_schedule_correctly():
    """An event with no start_seconds/duration_seconds (every pre-existing
    song file, MIDI-imported or hand-authored) must schedule exactly as
    before this pass."""
    section = Section(id="verse", name="Verse", start_beat=0, end_beat=8, loop_default=True)
    section.notes = [NoteEvent(beat=1.0, duration_beats=1.0, note=60, layer="melody")]  # start_seconds=None (the default)
    song = Song(title="Beat Only", artist="?", tempo_map=TempoMap(events=None), sections=[section])
    trainer = SongTrainer(song, KeyboardMapping(), lambda e: None)
    trainer.load_section("verse")
    trainer.set_mode(PracticeMode.AUTOPLAY)
    trainer.set_autoplay_layers(["melody"])
    trainer.start()
    trainer._tick_autoplay(0.9)
    assert trainer.autoplay_sounding_notes == set()
    trainer._tick_autoplay(1.0)
    assert trainer.autoplay_sounding_notes == {60}


def test_reference_derived_events_never_silently_become_verified():
    song = make_song_with_reference_timing()
    event = song.sections[0].notes[0]
    assert event.verification == NoteVerification.REFERENCE_DERIVED
    assert event.verification != NoteVerification.VERIFIED
    assert event.confidence == 0.82
    assert event.source == "local_reference_audio"

    # Round-trip through the loader (as tools/build_reference_song.py's
    # output would be read back) must preserve this distinction too.
    entries = notes_to_json_entries([event])
    assert entries[0]["verification"] == "reference_derived"
    assert entries[0]["verification"] != "verified"

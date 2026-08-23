"""Multi-instrument mixer tests (accuracy pass v3 fix): AudioEngine must
render and sum EVERY registered instrument every block, not just whichever
one is "active" for manual QWERTY playing -- otherwise target_instrument-
routed audio (e.g. autoplay bass) is silently never rendered at all.
"""

import time

import numpy as np

from qwerty_instrument.audio.bass import BassSynth
from qwerty_instrument.audio.engine import AudioConfig, AudioEngine
from qwerty_instrument.audio.synth import SynthLead
from qwerty_instrument.music.events import EventType, MusicEvent, Source

SR = 48000
BLOCK = 256


def ev(etype, note, key_id, target_instrument=None):
    md = {"key_id": key_id}
    if target_instrument:
        md["target_instrument"] = target_instrument
    return MusicEvent(type=etype, note=note, velocity=0.8, timestamp_ns=time.perf_counter_ns(), source=Source.QWERTY, metadata=md)


def make_engine():
    engine = AudioEngine(AudioConfig(sample_rate=SR, block_size=BLOCK))
    synth = SynthLead(SR, BLOCK)
    bass = BassSynth(SR, BLOCK)
    engine.register_instrument(synth)  # active_instrument_name = synth_lead (first registered)
    engine.register_instrument(bass)
    return engine, synth, bass


def render_via_callback(engine, frames=BLOCK):
    outdata = np.zeros((frames, 2), dtype=np.float32)
    engine._callback(outdata, frames, None, None)
    return outdata


def test_bass_only_produces_nonzero_audio_while_synth_is_manually_active():
    """The confirmed bug: bass_synth had an active voice but contributed
    zero audio because only active_instrument_name was rendered."""
    engine, synth, bass = make_engine()
    assert engine.active_instrument_name == "synth_lead"

    engine._process_event(ev(EventType.NOTE_ON, 34, "b0", target_instrument="bass_synth"))
    assert synth.active_voice_count == 0
    assert bass.active_voice_count == 1

    audio = render_via_callback(engine)
    assert np.all(np.isfinite(audio))
    assert np.max(np.abs(audio)) > 0.0, "bass-only voice produced silent output despite synth_lead being manually active"


def test_synth_and_bass_are_actually_mixed_together():
    """Not just 'both have active voices' -- the rendered samples must
    actually contain contributions from both simultaneously."""
    engine, synth, bass = make_engine()

    # Render synth alone first to get its solo waveform.
    engine._process_event(ev(EventType.NOTE_ON, 60, "s0"))
    solo_synth = render_via_callback(engine)
    engine._process_event(ev(EventType.NOTE_OFF, 60, "s0"))
    engine.hard_silence_all()

    # Fresh engine, bass alone.
    engine2, synth2, bass2 = make_engine()
    engine2._process_event(ev(EventType.NOTE_ON, 34, "b0", target_instrument="bass_synth"))
    solo_bass = render_via_callback(engine2)
    engine2._process_event(ev(EventType.NOTE_OFF, 34, "b0", target_instrument="bass_synth"))
    engine2.hard_silence_all()

    # Fresh engine, both together.
    engine3, synth3, bass3 = make_engine()
    engine3._process_event(ev(EventType.NOTE_ON, 60, "s0"))
    engine3._process_event(ev(EventType.NOTE_ON, 34, "b0", target_instrument="bass_synth"))
    mixed = render_via_callback(engine3)

    assert np.max(np.abs(solo_synth)) > 0.0
    assert np.max(np.abs(solo_bass)) > 0.0
    assert np.max(np.abs(mixed)) > 0.0
    # The mix must differ from either solo signal alone -- proof it's an
    # actual sum, not silently falling back to only one instrument.
    assert not np.allclose(mixed, solo_synth, atol=1e-6)
    assert not np.allclose(mixed, solo_bass, atol=1e-6)


def test_targeted_note_off_stops_the_routed_backend():
    engine, synth, bass = make_engine()
    engine._process_event(ev(EventType.NOTE_ON, 34, "b0", target_instrument="bass_synth"))
    assert bass.active_voice_count == 1
    engine._process_event(ev(EventType.NOTE_OFF, 34, "b0", target_instrument="bass_synth"))
    assert bass._owned == {}
    # give the release envelope a moment then confirm it actually settles
    for _ in range(50):
        render_via_callback(engine)
    assert bass.active_voice_count == 0


def test_mixer_returns_to_near_silence_after_all_voices_release():
    engine, synth, bass = make_engine()
    engine._process_event(ev(EventType.NOTE_ON, 60, "s0"))
    engine._process_event(ev(EventType.NOTE_ON, 34, "b0", target_instrument="bass_synth"))
    render_via_callback(engine)
    engine._process_event(ev(EventType.NOTE_OFF, 60, "s0"))
    engine._process_event(ev(EventType.NOTE_OFF, 34, "b0", target_instrument="bass_synth"))

    last = None
    for _ in range(100):  # generous -- long enough for both release tails
        last = render_via_callback(engine)
    assert np.max(np.abs(last)) < 1e-3
    assert synth.active_voice_count == 0
    assert bass.active_voice_count == 0


def test_manual_program_change_still_governs_untargeted_events():
    """Sanity check that the fix didn't change manual QWERTY semantics:
    an event with no target_instrument still goes to active_instrument_name."""
    engine, synth, bass = make_engine()
    engine._process_event(MusicEvent(type=EventType.PROGRAM_CHANGE, source=Source.UI, metadata={"instrument": "bass_synth"}))
    assert engine.active_instrument_name == "bass_synth"
    engine._process_event(ev(EventType.NOTE_ON, 60, "m0"))  # no target_instrument
    assert bass.active_voice_count == 1
    assert synth.active_voice_count == 0

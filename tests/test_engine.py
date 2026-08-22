"""AudioEngine event-processing tests (sustain deferral, program change,
panic) exercised directly against `_process_event` -- no real audio stream
is opened, matching how the DSP-only tests avoid touching hardware. See
tests/_hardware_smoke.py for the real-device end-to-end check.
"""

import time

from qwerty_instrument.audio.engine import AudioConfig, AudioEngine
from qwerty_instrument.audio.synth import ElectricPiano, SynthLead
from qwerty_instrument.music.events import EventType, MusicEvent, Source

SR = 48000
BLOCK = 256


def make_engine():
    engine = AudioEngine(AudioConfig(sample_rate=SR, block_size=BLOCK))
    engine.register_instrument(SynthLead(SR, BLOCK))
    engine.register_instrument(ElectricPiano(SR, BLOCK))
    return engine


def ev(etype, note=60, velocity=0.8, key_id="k0", **metadata):
    md = {"key_id": key_id, **metadata}
    return MusicEvent(type=etype, note=note, velocity=velocity, timestamp_ns=time.perf_counter_ns(), source=Source.QWERTY, metadata=md)


def test_note_on_off_routes_to_active_instrument():
    engine = make_engine()
    engine._process_event(ev(EventType.NOTE_ON))
    assert engine.instruments["synth_lead"].active_voice_count == 1
    engine._process_event(ev(EventType.NOTE_OFF))
    engine.instruments["synth_lead"].render(BLOCK)  # let release begin
    # released, not necessarily freed yet -- that's fine, just must not still be "held"
    assert "k0" not in engine._pending_release


def test_sustain_defers_note_off_until_sustain_released():
    engine = make_engine()
    engine._process_event(ev(EventType.NOTE_ON))
    engine._process_event(MusicEvent(type=EventType.SUSTAIN, source=Source.QWERTY, metadata={"on": True}))
    engine._process_event(ev(EventType.NOTE_OFF))

    inst = engine.instruments["synth_lead"]
    assert len(engine._pending_release) == 1
    assert inst.active_voice_count == 1  # note-off was deferred: voice is still held, not released

    engine._process_event(MusicEvent(type=EventType.SUSTAIN, source=Source.QWERTY, metadata={"on": False}))
    assert len(engine._pending_release) == 0
    assert inst._owned == {}  # now actually released (may still be in its release-tail render)


def test_program_change_panics_then_switches_active_instrument():
    engine = make_engine()
    engine._process_event(ev(EventType.NOTE_ON))
    assert engine.instruments["synth_lead"].active_voice_count == 1
    engine._process_event(MusicEvent(type=EventType.PROGRAM_CHANGE, source=Source.QWERTY, metadata={"instrument": "electric_piano"}))
    assert engine.active_instrument_name == "electric_piano"
    # the synth voice was released (panic), not left dangling as "owned"
    assert engine.instruments["synth_lead"]._owned == {}


def test_all_notes_off_panics_every_registered_instrument():
    engine = make_engine()
    engine._process_event(ev(EventType.NOTE_ON, key_id="a"))
    engine.set_active_instrument("electric_piano")
    engine._process_event(ev(EventType.NOTE_ON, key_id="b"))
    engine._process_event(MusicEvent(type=EventType.ALL_NOTES_OFF, source=Source.QWERTY))
    assert engine.instruments["synth_lead"]._owned == {}
    assert engine.instruments["electric_piano"]._owned == {}


def test_pitch_bend_routes_to_active_instrument_voices():
    engine = make_engine()
    engine._process_event(ev(EventType.NOTE_ON))
    engine._process_event(MusicEvent(type=EventType.PITCH_BEND, source=Source.F75_KNOB, metadata={"cents": 50.0}))
    voice = next(iter(engine.instruments["synth_lead"]._active))
    assert voice.bend_cents == 50.0

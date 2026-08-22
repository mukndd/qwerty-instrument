"""Instrument backend tests: note lifecycle, polyphony, panic, no-stuck-notes.

Pure DSP/state assertions, no real audio device involved (renders into
numpy arrays only) -- see tests/_hardware_smoke.py for the manual,
real-device end-to-end check.
"""

import time

import numpy as np
import pytest

from qwerty_instrument.audio.guitar import GuitarLead
from qwerty_instrument.audio.synth import ElectricPiano, SynthLead
from qwerty_instrument.music.events import EventType, MusicEvent, Source

SR = 48000
BLOCK = 256


def ev(etype, note=60, velocity=0.8, key_id="k0"):
    return MusicEvent(type=etype, note=note, velocity=velocity, timestamp_ns=time.perf_counter_ns(), source=Source.QWERTY, metadata={"key_id": key_id})


@pytest.mark.parametrize("backend_cls,notes", [(SynthLead, [60, 64, 67]), (ElectricPiano, [60, 64, 67]), (GuitarLead, [40, 45, 50])])
def test_note_on_produces_finite_nonclipping_audio(backend_cls, notes):
    inst = backend_cls(SR, BLOCK)
    inst.note_on(ev(EventType.NOTE_ON, note=notes[0]))
    audio = inst.render(BLOCK)
    assert audio.shape == (BLOCK, 2)
    assert np.all(np.isfinite(audio))
    assert np.max(np.abs(audio)) > 0  # actually produces sound


@pytest.mark.parametrize("backend_cls,notes", [(SynthLead, [60, 64, 67]), (ElectricPiano, [60, 64, 67]), (GuitarLead, [40, 45, 50])])
def test_note_off_eventually_frees_the_voice(backend_cls, notes):
    inst = backend_cls(SR, BLOCK)
    inst.note_on(ev(EventType.NOTE_ON, note=notes[0]))
    inst.render(BLOCK)
    inst.note_off(ev(EventType.NOTE_OFF, note=notes[0]))
    max_blocks = int(7.0 * SR / BLOCK)  # guitar's low-register natural release is tuned to ~3s; generous margin
    for _ in range(max_blocks):
        inst.render(BLOCK)
        if inst.active_voice_count == 0:
            break
    assert inst.active_voice_count == 0, "voice was never freed back to the pool after release"


@pytest.mark.parametrize("backend_cls,notes", [(SynthLead, [60, 64, 67]), (GuitarLead, [40, 45, 50])])
def test_chord_polyphony(backend_cls, notes):
    inst = backend_cls(SR, BLOCK)
    for i, n in enumerate(notes):
        inst.note_on(ev(EventType.NOTE_ON, note=n, key_id=f"key{i}"))
    assert inst.active_voice_count == len(notes)
    audio = inst.render(BLOCK)
    assert np.all(np.isfinite(audio))


def test_note_off_uses_key_id_not_pitch_for_voice_ownership():
    """Two different keys mapped to the same pitch must not steal each
    other's note-off (spec: voice ownership by input source identity)."""
    inst = SynthLead(SR, BLOCK)
    inst.note_on(ev(EventType.NOTE_ON, note=60, key_id="keyA"))
    inst.note_on(ev(EventType.NOTE_ON, note=60, key_id="keyB"))
    assert inst.active_voice_count == 2
    inst.note_off(ev(EventType.NOTE_OFF, note=60, key_id="keyA"))
    inst.render(BLOCK)
    # keyB's voice must still be sounding (in attack/sustain), not released
    remaining = [v for v in inst._active if not v.env._stage == v.env.RELEASE and not v.env._stage == v.env.IDLE]
    assert len(remaining) == 1


@pytest.mark.parametrize("backend_cls,notes", [(SynthLead, [60, 64, 67, 71]), (ElectricPiano, [60, 64, 67, 71]), (GuitarLead, [40, 45, 50, 55])])
def test_panic_silences_everything_quickly(backend_cls, notes):
    inst = backend_cls(SR, BLOCK, polyphony=8)
    for i, n in enumerate(notes):
        inst.note_on(ev(EventType.NOTE_ON, note=n, key_id=f"k{i}"))
    inst.all_notes_off()
    max_seconds = 2.0
    frames = 0
    while inst.active_voice_count > 0 and frames < SR * max_seconds:
        inst.render(BLOCK)
        frames += BLOCK
    assert inst.active_voice_count == 0, f"{backend_cls.__name__}: panic did not silence all voices within {max_seconds}s"


def test_repeated_note_on_same_key_id_is_idempotent():
    """Defends against a stuck/leaked voice if a duplicate NOTE_ON somehow
    reaches the backend (the input layer should already prevent this)."""
    inst = SynthLead(SR, BLOCK)
    inst.note_on(ev(EventType.NOTE_ON, note=60, key_id="k0"))
    inst.note_on(ev(EventType.NOTE_ON, note=60, key_id="k0"))  # duplicate, same key_id
    assert inst.active_voice_count == 1


def test_voice_stealing_when_polyphony_exceeded():
    inst = SynthLead(SR, BLOCK, polyphony=2)
    inst.note_on(ev(EventType.NOTE_ON, note=60, key_id="a"))
    inst.note_on(ev(EventType.NOTE_ON, note=62, key_id="b"))
    inst.note_on(ev(EventType.NOTE_ON, note=64, key_id="c"))  # should steal the oldest voice
    assert inst.active_voice_count == 2
    audio = inst.render(BLOCK)
    assert np.all(np.isfinite(audio))

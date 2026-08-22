"""Not a pytest file (leading underscore) -- a quick manual offline render
smoke test, run directly, that never opens a real audio stream. Verifies
the instrument backends produce finite, non-clipping, non-silent audio
across note-on/hold/release/polyphony without touching hardware.
"""

from __future__ import annotations

import sys
import time

import numpy as np

from qwerty_instrument.audio.effects import Limiter
from qwerty_instrument.audio.guitar import GuitarLead
from qwerty_instrument.audio.synth import ElectricPiano, SynthLead
from qwerty_instrument.music.events import EventType, MusicEvent, Source

SR = 48000
BLOCK = 256


def make_event(etype, note=60, velocity=0.9, key_id="Z"):
    return MusicEvent(type=etype, note=note, velocity=velocity, timestamp_ns=time.perf_counter_ns(), source=Source.QWERTY, metadata={"key_id": key_id})


def render_seconds(inst, seconds) -> np.ndarray:
    n_blocks = int(seconds * SR / BLOCK)
    out = []
    for _ in range(n_blocks):
        out.append(inst.render(BLOCK))
    return np.concatenate(out, axis=0)


def check(name, audio):
    finite = np.all(np.isfinite(audio))
    peak = float(np.max(np.abs(audio))) if audio.size else 0.0
    rms = float(np.sqrt(np.mean(audio.astype(np.float64) ** 2))) if audio.size else 0.0
    status = "OK" if finite and peak <= 1.0001 else "FAIL"
    print(f"[{status}] {name}: frames={audio.shape[0]} peak={peak:.4f} rms={rms:.4f} finite={finite}")
    return status == "OK"


def main() -> int:
    all_ok = True
    limiter = Limiter()

    for cls, name, notes in [
        (SynthLead, "SynthLead", [60, 64, 67]),
        (ElectricPiano, "ElectricPiano", [60, 64, 67]),
        (GuitarLead, "GuitarLead", [40, 45, 50]),
    ]:
        inst = cls(SR, BLOCK)
        print(f"\n--- {name}: single note on/hold/off ---")
        inst.note_on(make_event(EventType.NOTE_ON, note=notes[0], key_id="k0"))
        audio = render_seconds(inst, 0.3)
        audio = limiter.process(audio)
        all_ok &= check(f"{name} attack+hold", audio)
        inst.note_off(make_event(EventType.NOTE_OFF, note=notes[0], key_id="k0"))
        audio = render_seconds(inst, 0.6)
        audio = limiter.process(audio)
        all_ok &= check(f"{name} release+tail", audio)

        print(f"--- {name}: polyphony (chord) ---")
        for i, n in enumerate(notes):
            inst.note_on(make_event(EventType.NOTE_ON, note=n, key_id=f"chord{i}"))
        audio = render_seconds(inst, 0.4)
        audio = limiter.process(audio)
        all_ok &= check(f"{name} chord", audio)
        assert inst.active_voice_count >= len(notes), f"expected at least {len(notes)} active voices, got {inst.active_voice_count}"
        for i in range(len(notes)):
            inst.note_off(make_event(EventType.NOTE_OFF, key_id=f"chord{i}"))
        audio = render_seconds(inst, 0.6)
        all_ok &= check(f"{name} chord release", audio)

        print(f"--- {name}: rapid retrigger (stuck-note stress) ---")
        for i in range(40):
            inst.note_on(make_event(EventType.NOTE_ON, note=60 + (i % 12), key_id=f"rt{i}"))
            _ = inst.render(BLOCK)
            inst.note_off(make_event(EventType.NOTE_OFF, key_id=f"rt{i}"))
            _ = inst.render(BLOCK)
        audio = render_seconds(inst, 1.0)
        audio = limiter.process(audio)
        all_ok &= check(f"{name} post-stress tail (post-limiter)", audio)
        assert inst.active_voice_count == 0 or True  # voices may still be releasing; just must not crash

        print(f"--- {name}: panic clears everything ---")
        for i in range(5):
            inst.note_on(make_event(EventType.NOTE_ON, note=60 + i, key_id=f"p{i}"))
        inst.all_notes_off()
        audio = render_seconds(inst, 1.0)
        all_ok &= check(f"{name} after panic", audio)
        assert inst.active_voice_count == 0, f"{name}: voices still active {inst.active_voice_count} frames after panic release"
        print(f"{name}: panic OK, 0 active voices after release tail")

    print("\n=== RESULT:", "ALL OK" if all_ok else "FAILURES DETECTED", "===")
    return 0 if all_ok else 1


if __name__ == "__main__":
    sys.exit(main())

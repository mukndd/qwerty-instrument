"""Manual (non-pytest) real-hardware smoke test for the multi-instrument
mixer fix (accuracy pass v3, Phase 1/23). Instant Crush's chorus data is
now correctly EMPTY (see songs/instant_crush/NOTES_STATUS.md) -- this test
verifies the actual bug fix (bass_synth audible while synth_lead remains
the manually-active instrument, both mixed simultaneously) using direct
synthetic events through the real AudioEngine, since there's no reference-
derived chorus data yet to autoplay.
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

from qwerty_instrument.audio.bass import BassSynth
from qwerty_instrument.audio.engine import AudioConfig, AudioEngine
from qwerty_instrument.audio.presets import apply_preset, load_preset
from qwerty_instrument.audio.synth import SynthLead
from qwerty_instrument.music.events import EventType, MusicEvent, Source

SR = 48000
BLOCK = 256


def note_on(engine, note, key_id, target=None, velocity=0.75):
    md = {"key_id": key_id}
    if target:
        md["target_instrument"] = target
    engine.submit(MusicEvent(type=EventType.NOTE_ON, note=note, velocity=velocity, timestamp_ns=time.perf_counter_ns(), source=Source.UI, metadata=md))


def note_off(engine, note, key_id, target=None):
    md = {"key_id": key_id}
    if target:
        md["target_instrument"] = target
    engine.submit(MusicEvent(type=EventType.NOTE_OFF, note=note, timestamp_ns=time.perf_counter_ns(), source=Source.UI, metadata=md))


def main() -> int:
    engine = AudioEngine(AudioConfig(sample_rate=SR, block_size=BLOCK))
    synth = SynthLead(SR, BLOCK)
    bass = BassSynth(SR, BLOCK)
    engine.register_instrument(synth)  # active_instrument_name = synth_lead
    engine.register_instrument(bass)
    engine.start()
    print(f"Stream open. Reported latency: {engine.stats.reported_latency_s * 1000:.2f} ms")
    print(f"active_instrument_name = {engine.active_instrument_name!r} (bass_synth is NOT active)")

    synth_preset = load_preset(Path("presets/instant_crush_synth.json"))
    bass_preset = load_preset(Path("presets/instant_crush_bass.json"))
    apply_preset(synth, synth_preset)
    apply_preset(bass, bass_preset)

    print("\n--- Test 1: BASS ONLY (synth_lead stays manually active, unused) ---")
    note_on(engine, 34, "b0", target="bass_synth")
    time.sleep(1.5)
    note_off(engine, 34, "b0", target="bass_synth")
    time.sleep(0.5)
    print(f"  bass active_voice_count during hold: (see below) max_simultaneous_notes so far={engine.stats.max_simultaneous_notes}")

    print("\n--- Test 2: SYNTH + BASS together (Full Chorus mixer proof) ---")
    note_on(engine, 60, "s0")  # no target -> goes to active_instrument_name (synth_lead)
    note_on(engine, 34, "b0b", target="bass_synth")
    time.sleep(2.0)
    note_off(engine, 60, "s0")
    note_off(engine, 34, "b0b", target="bass_synth")
    time.sleep(0.8)

    engine.stop()

    print("\n=== STATS ===")
    print(f"underruns: {engine.stats.underrun_count}")
    print(f"max callback time: {engine.stats.max_duration_ms:.3f} ms (budget {BLOCK / SR * 1000:.3f} ms)")
    print(f"active voices after stop: synth={synth.active_voice_count} bass={bass.active_voice_count}")

    ok = engine.stats.underrun_count == 0 and synth.active_voice_count == 0 and bass.active_voice_count == 0
    print("\n=== RESULT:", "OK" if ok else "CHECK ABOVE", "===")
    print("(Listen check: you should have heard a low bass tone alone in Test 1,")
    print(" then a mid synth tone + the bass tone together in Test 2.)")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())

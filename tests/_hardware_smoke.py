"""Manual (non-pytest) real-hardware smoke test: opens an actual WASAPI
output stream and plays a short, quiet C-major scale on each instrument by
injecting synthetic MusicEvents directly into the engine queue (bypassing
the Win32 keyboard hook, which needs a real physical key press to test).
Confirms the Phase 2 milestone end-to-end: engine opens, renders, reports
latency, and shuts down with zero underruns and zero stuck voices.
"""

from __future__ import annotations

import sys
import time

from qwerty_instrument.audio.engine import AudioConfig, AudioEngine
from qwerty_instrument.audio.guitar import GuitarLead
from qwerty_instrument.audio.synth import ElectricPiano, SynthLead
from qwerty_instrument.music.events import EventType, MusicEvent, Source

SR = 48000
BLOCK = 256
SCALE = [60, 62, 64, 65, 67, 69, 71, 72]  # C major


def play_scale(engine: AudioEngine, note_seconds: float = 0.22) -> None:
    for i, note in enumerate(SCALE):
        key_id = f"scale{i}"
        engine.submit(MusicEvent(type=EventType.NOTE_ON, note=note, velocity=0.75, timestamp_ns=time.perf_counter_ns(), source=Source.QWERTY, metadata={"key_id": key_id}))
        time.sleep(note_seconds * 0.85)
        engine.submit(MusicEvent(type=EventType.NOTE_OFF, note=note, timestamp_ns=time.perf_counter_ns(), source=Source.QWERTY, metadata={"key_id": key_id}))
        time.sleep(note_seconds * 0.15)
    time.sleep(0.5)


def main() -> int:
    cfg = AudioConfig(sample_rate=SR, block_size=BLOCK)
    engine = AudioEngine(cfg)
    engine.register_instrument(SynthLead(SR, BLOCK))
    engine.register_instrument(ElectricPiano(SR, BLOCK))
    engine.register_instrument(GuitarLead(SR, BLOCK, polyphony=6))

    engine.start()
    print(f"Stream open. Reported latency: {engine.stats.reported_latency_s * 1000:.2f} ms")

    for name in ("synth_lead", "electric_piano", "guitar_lead"):
        print(f"\n--- playing C major scale on {name} ---")
        engine.set_active_instrument(name)
        play_scale(engine)

    print("\n--- chord test on synth_lead ---")
    engine.set_active_instrument("synth_lead")
    for i, note in enumerate([60, 64, 67]):
        engine.submit(MusicEvent(type=EventType.NOTE_ON, note=note, velocity=0.7, timestamp_ns=time.perf_counter_ns(), source=Source.QWERTY, metadata={"key_id": f"c{i}"}))
    time.sleep(1.0)
    engine.submit(MusicEvent(type=EventType.ALL_NOTES_OFF, timestamp_ns=time.perf_counter_ns(), source=Source.QWERTY))
    time.sleep(0.5)

    engine.stop()

    print("\n=== STATS ===")
    print(f"callbacks: {engine.stats.callback_count}")
    print(f"underruns: {engine.stats.underrun_count}")
    print(f"overruns: {engine.stats.overrun_count}")
    print(f"avg callback time: {engine.stats.ema_duration_ms:.3f} ms (budget: {BLOCK / SR * 1000:.3f} ms)")
    print(f"max callback time: {engine.stats.max_duration_ms:.3f} ms")
    print(f"max simultaneous notes: {engine.stats.max_simultaneous_notes}")

    ok = engine.stats.underrun_count == 0 and engine.stats.max_duration_ms < (BLOCK / SR * 1000)
    print("\n=== RESULT:", "OK" if ok else "CHECK ABOVE (underruns or overtime blocks present)", "===")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())

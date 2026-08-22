"""Audio diagnostics: devices, host APIs, latency, and a real callback-timing
stress test against actual hardware.

Usage:
    .venv\\Scripts\\python.exe tools\\audio_diagnostics.py                 # device/latency report
    .venv\\Scripts\\python.exe tools\\audio_diagnostics.py --tone           # + audible test tone
    .venv\\Scripts\\python.exe tools\\audio_diagnostics.py --stress         # + polyphony stress test
    .venv\\Scripts\\python.exe tools\\audio_diagnostics.py --block 128      # try a specific block size
    .venv\\Scripts\\python.exe tools\\audio_diagnostics.py --exclusive      # try WASAPI exclusive mode

All measured numbers below are the *software-path* latency PortAudio
reports for the chosen device/buffer combination -- not a loopback-measured
acoustic latency (that would require a physical mic-to-output loopback
rig, which this tool does not assume you have). Treat "reported_latency"
as an informed estimate, not a guaranteed physical measurement.
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import numpy as np
import sounddevice as sd

from qwerty_instrument.audio import devices as devmod
from qwerty_instrument.audio.engine import AudioConfig, AudioEngine
from qwerty_instrument.audio.guitar import GuitarLead
from qwerty_instrument.audio.synth import ElectricPiano, SynthLead
from qwerty_instrument.music.events import EventType, MusicEvent, Source

LATENCY_TIERS = [(10, "excellent"), (15, "very good"), (25, "acceptable"), (float("inf"), "undesirable")]


def latency_tier(ms: float) -> str:
    for threshold, label in LATENCY_TIERS:
        if ms < threshold:
            return label
    return "undesirable"


def report_devices() -> None:
    print("=" * 78)
    print("HOST APIs")
    print("=" * 78)
    for i, api in enumerate(devmod.list_host_apis()):
        print(f"  [{i}] {api['name']}  (default_output_device={api['default_output_device']})")

    print()
    print("=" * 78)
    print("OUTPUT DEVICES")
    print("=" * 78)
    for d in devmod.list_output_devices():
        print(f"  [{d.index:3d}] {d.name:45s} hostapi={d.hostapi_name:18s} channels={d.max_output_channels} default_sr={d.default_samplerate:.0f}")

    wasapi_default = devmod.default_wasapi_output_device()
    print()
    print(f"WASAPI default output device index: {wasapi_default}")
    if wasapi_default is not None:
        info = devmod.describe_device(wasapi_default)
        if info:
            print(f"  -> {info.name} ({info.hostapi_name}, default_sr={info.default_samplerate:.0f})")


def open_and_report(sample_rate: int, block_size: int, device: int | None, exclusive: bool) -> AudioEngine:
    engine = AudioEngine(AudioConfig(sample_rate=sample_rate, block_size=block_size, device_index=device, use_wasapi_exclusive=exclusive))
    engine.register_instrument(SynthLead(sample_rate, block_size))
    engine.register_instrument(ElectricPiano(sample_rate, block_size))
    engine.register_instrument(GuitarLead(sample_rate, block_size, polyphony=6))
    engine.start()
    ms = engine.stats.reported_latency_s * 1000
    print(f"\nOpened: sr={sample_rate} block={block_size} device={device} exclusive={engine.config.use_wasapi_exclusive}")
    print(f"Reported stream latency: {ms:.2f} ms  [{latency_tier(ms)}]")
    print(f"Block period budget: {block_size / sample_rate * 1000:.3f} ms per callback")
    return engine


def run_test_tone(engine: AudioEngine, seconds: float = 1.5) -> None:
    print(f"\nPlaying {seconds:.1f}s test tone (A4, synth_lead, moderate volume)...")
    engine.set_active_instrument("synth_lead")
    engine.submit(MusicEvent(type=EventType.NOTE_ON, note=69, velocity=0.5, timestamp_ns=time.perf_counter_ns(), source=Source.UI, metadata={"key_id": "diag_tone"}))
    time.sleep(seconds)
    engine.submit(MusicEvent(type=EventType.NOTE_OFF, timestamp_ns=time.perf_counter_ns(), source=Source.UI, metadata={"key_id": "diag_tone"}))
    time.sleep(0.4)


def run_stress_test(engine: AudioEngine, seconds: float = 3.0) -> None:
    print(f"\nPolyphony/latency stress test for {seconds:.1f}s (rapid notes across all instruments)...")
    import random

    instruments = ("synth_lead", "electric_piano", "guitar_lead")
    per_instrument_seconds = seconds / len(instruments)
    i = 0
    for name in instruments:
        engine.set_active_instrument(name)
        segment_end = time.time() + per_instrument_seconds
        while time.time() < segment_end:
            note = random.randint(48, 72)
            key_id = f"stress{i}"
            i += 1
            engine.submit(MusicEvent(type=EventType.NOTE_ON, note=note, velocity=random.uniform(0.5, 0.9), timestamp_ns=time.perf_counter_ns(), source=Source.UI, metadata={"key_id": key_id}))
            time.sleep(0.02)
            engine.submit(MusicEvent(type=EventType.NOTE_OFF, timestamp_ns=time.perf_counter_ns(), source=Source.UI, metadata={"key_id": key_id}))
    engine.submit(MusicEvent(type=EventType.ALL_NOTES_OFF, source=Source.UI))
    time.sleep(0.3)


def print_stats(engine: AudioEngine) -> None:
    s = engine.stats
    budget_ms = engine.config.block_size / engine.config.sample_rate * 1000
    print("\n" + "=" * 78)
    print("CALLBACK STATS")
    print("=" * 78)
    print(f"  callbacks processed:     {s.callback_count}")
    print(f"  underruns:               {s.underrun_count}")
    print(f"  overruns:                {s.overrun_count}")
    print(f"  avg callback duration:   {s.ema_duration_ms:.4f} ms")
    print(f"  max callback duration:   {s.max_duration_ms:.4f} ms  (budget: {budget_ms:.4f} ms)")
    print(f"  max simultaneous notes:  {s.max_simultaneous_notes}")
    verdict = "OK" if s.underrun_count == 0 else "UNDERRUNS DETECTED -- try a larger block size"
    print(f"  verdict: {verdict}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--block", type=int, default=256, help="block size in frames (default 256; try 128 or 64)")
    parser.add_argument("--rate", type=int, default=48000, help="sample rate (default 48000)")
    parser.add_argument("--device", type=int, default=None, help="output device index (default: WASAPI default)")
    parser.add_argument("--exclusive", action="store_true", help="try WASAPI exclusive mode")
    parser.add_argument("--tone", action="store_true", help="play an audible test tone")
    parser.add_argument("--stress", action="store_true", help="run a rapid-note polyphony stress test")
    args = parser.parse_args()

    report_devices()

    engine = open_and_report(args.rate, args.block, args.device, args.exclusive)
    try:
        if args.tone:
            run_test_tone(engine)
        if args.stress:
            run_stress_test(engine)
        if not args.tone and not args.stress:
            time.sleep(0.5)  # let a few silent callbacks run so stats aren't empty
    finally:
        engine.stop()

    print_stats(engine)
    return 0


if __name__ == "__main__":
    sys.exit(main())

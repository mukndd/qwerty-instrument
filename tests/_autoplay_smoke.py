"""Manual (non-pytest) real-hardware smoke test for the Instant Crush
Autoplay chorus (accuracy pass v2): loads the real song data, drives
SongTrainer in AUTOPLAY/Full Chorus mode through a real AudioEngine with
SynthLead (chords+melody) and BassSynth (bass) registered and their tuned
presets applied -- same as the UI does on Start -- and plays ~20s
(more than one full 8-bar/~17.5s loop) through actual speakers.
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

from qwerty_instrument.audio.bass import BassSynth
from qwerty_instrument.audio.engine import AudioConfig, AudioEngine
from qwerty_instrument.audio.presets import apply_preset, load_preset
from qwerty_instrument.audio.synth import SynthLead
from qwerty_instrument.music.mapping import KeyboardMapping
from qwerty_instrument.songs.loader import load_song
from qwerty_instrument.songs.trainer import PracticeMode, SongTrainer

SR = 48000
BLOCK = 256


def main() -> int:
    engine = AudioEngine(AudioConfig(sample_rate=SR, block_size=BLOCK))
    synth = SynthLead(SR, BLOCK)
    bass = BassSynth(SR, BLOCK)
    engine.register_instrument(synth)
    engine.register_instrument(bass)
    engine.start()
    print(f"Stream open. Reported latency: {engine.stats.reported_latency_s * 1000:.2f} ms")

    synth_preset = load_preset(Path("presets/instant_crush_synth.json"))
    bass_preset = load_preset(Path("presets/instant_crush_bass.json"))
    assert synth_preset and bass_preset, "presets failed to load"
    apply_preset(synth, synth_preset)
    apply_preset(bass, bass_preset)
    print(f"Applied {synth_preset.name!r} (cutoff={synth.patch['cutoff']}) and {bass_preset.name!r} (cutoff={bass.patch['cutoff']})")

    song = load_song(Path("songs/instant_crush"))
    print(f"Loaded song: {song.title}, tempo={song.tempo_map.bpm_at_beat(0)} BPM")

    mapping = KeyboardMapping()
    trainer = SongTrainer(song, mapping, engine.submit)
    trainer.load_section("chorus")
    trainer.set_mode(PracticeMode.AUTOPLAY)
    trainer.set_autoplay_layers(["chords", "bass", "melody"])
    trainer.loop_enabled = True

    print("Starting autoplay (8-bar Full Chorus @ 110 BPM, looping, ~20s = 1+ full loop)...")
    trainer.start()

    end_time = time.time() + 20.0
    max_notes_seen = 0
    while time.time() < end_time:
        trainer.tick()
        max_notes_seen = max(max_notes_seen, synth.active_voice_count + bass.active_voice_count)
        time.sleep(1 / 30)

    print(f"Max simultaneous voices (synth+bass) during playback: {max_notes_seen}")
    trainer.stop()
    time.sleep(0.5)
    engine.stop()

    print("\n=== STATS ===")
    print(f"underruns: {engine.stats.underrun_count}")
    print(f"max callback time: {engine.stats.max_duration_ms:.3f} ms (budget {BLOCK / SR * 1000:.3f} ms)")
    print(f"active voices after stop: synth={synth.active_voice_count} bass={bass.active_voice_count}")
    print(f"autoplay_sounding_notes after stop: {trainer.autoplay_sounding_notes}")

    ok = engine.stats.underrun_count == 0 and trainer.autoplay_sounding_notes == set() and synth.active_voice_count == 0 and bass.active_voice_count == 0
    print("\n=== RESULT:", "OK" if ok else "CHECK ABOVE", "===")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())

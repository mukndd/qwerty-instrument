"""Manual (non-pytest) real-hardware smoke test for the Instant Crush
Autoplay chorus: loads the real song data, drives SongTrainer in AUTOPLAY
mode through a real AudioEngine + SynthLead (with the Instant Crush Synth
preset applied, same as the UI does on Start), and plays ~10s of the
looping 8-bar chords+bass benchmark through actual speakers.
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

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
    engine.register_instrument(synth)
    engine.start()
    print(f"Stream open. Reported latency: {engine.stats.reported_latency_s * 1000:.2f} ms")

    preset = load_preset(Path("presets/instant_crush_synth.json"))
    assert preset is not None, "instant_crush_synth.json failed to load"
    applied = apply_preset(synth, preset)
    print(f"Applied preset {preset.name!r}: {applied}  cutoff={synth.patch['cutoff']}")

    song = load_song(Path("songs/instant_crush"))
    print(f"Loaded song: {song.title}, tempo={song.tempo_map.bpm_at_beat(0)} BPM")

    mapping = KeyboardMapping()
    trainer = SongTrainer(song, mapping, engine.submit)
    trainer.load_section("chorus")
    trainer.set_mode(PracticeMode.AUTOPLAY)
    trainer.set_autoplay_layers(["chords", "bass"])
    trainer.loop_enabled = True

    print("Starting autoplay (8-bar chorus @ 110 BPM, chords+bass, looping)...")
    trainer.start()

    end_time = time.time() + 10.0
    max_notes_seen = 0
    while time.time() < end_time:
        trainer.tick()
        max_notes_seen = max(max_notes_seen, synth.active_voice_count)
        time.sleep(1 / 30)

    print(f"Max simultaneous synth voices during playback: {max_notes_seen}")
    trainer.stop()
    time.sleep(0.5)
    engine.stop()

    print("\n=== STATS ===")
    print(f"underruns: {engine.stats.underrun_count}")
    print(f"max callback time: {engine.stats.max_duration_ms:.3f} ms (budget {BLOCK / SR * 1000:.3f} ms)")
    print(f"active voices after stop: {synth.active_voice_count}")
    print(f"autoplay_sounding_notes after stop: {trainer.autoplay_sounding_notes}")

    ok = engine.stats.underrun_count == 0 and trainer.autoplay_sounding_notes == set()
    print("\n=== RESULT:", "OK" if ok else "CHECK ABOVE", "===")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())

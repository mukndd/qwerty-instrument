"""Play a song's Autoplay layers straight from the terminal, no GUI.

Reuses the exact same signal path the PySide6 UI drives (AudioEngine ->
registered instruments -> SongTrainer in PracticeMode.AUTOPLAY) so what you
hear here is identical to what Autoplay sounds like in the app -- just
without launching the window every time you want to listen and judge
accuracy.

Usage:
    .venv\\Scripts\\python.exe tools\\play_autoplay.py --list
    .venv\\Scripts\\python.exe tools\\play_autoplay.py
    .venv\\Scripts\\python.exe tools\\play_autoplay.py --section chorus --layers chords,bass
    .venv\\Scripts\\python.exe tools\\play_autoplay.py --loops 3 --speed 80

This plays audio through your speakers. Run it only when you're ready to
listen -- there is no background/auto-trigger path.
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from qwerty_instrument import config as config_mod
from qwerty_instrument.audio.bass import BassSynth
from qwerty_instrument.audio.engine import AudioConfig, AudioEngine
from qwerty_instrument.audio.guitar import GuitarLead
from qwerty_instrument.audio.presets import apply_preset, load_preset
from qwerty_instrument.audio.synth import ElectricPiano, SynthLead
from qwerty_instrument.music.mapping import KeyboardMapping
from qwerty_instrument.songs.loader import list_available_songs, load_song
from qwerty_instrument.songs.trainer import PracticeMode, SongTrainer

SONGS_ROOT = Path(__file__).resolve().parent.parent / "songs"
PRESETS_ROOT = Path(__file__).resolve().parent.parent / "presets"
ALL_LAYERS = ["chords", "bass", "melody"]

# song_id -> preset filenames to auto-apply before Autoplay starts, mirroring
# ui/main_window.py's _start_practice() special-case for Instant Crush.
SONG_PRESETS = {
    "instant_crush": ["instant_crush_synth.json", "instant_crush_bass.json"],
}


def cmd_list() -> int:
    songs = list_available_songs(SONGS_ROOT)
    if not songs:
        print(f"No songs found under {SONGS_ROOT}")
        return 1
    for song_id in songs:
        song = load_song(SONGS_ROOT / song_id)
        print(f"{song_id}  ({song.title} -- {song.artist})")
        for section in song.sections:
            layers_with_data = sorted({e.layer for e in section.notes})
            status = ", ".join(layers_with_data) if layers_with_data else "NO DATA YET"
            print(f"    {section.id:15s} beats {section.start_beat:8.2f}-{section.end_beat:8.2f}  layers: {status}")
    return 0


def build_engine(rate: int, block: int, device: int | None, cfg: dict) -> AudioEngine:
    audio_cfg = cfg["audio"]
    poly = cfg["instruments"]["polyphony"]
    engine = AudioEngine(AudioConfig(sample_rate=rate, block_size=block, device_index=device, use_wasapi_exclusive=audio_cfg["use_wasapi_exclusive"]))
    engine.register_instrument(SynthLead(rate, block, polyphony=poly["synth_lead"]))
    engine.register_instrument(ElectricPiano(rate, block, polyphony=poly["electric_piano"]))
    engine.register_instrument(GuitarLead(rate, block, polyphony=poly["guitar_lead"]))
    engine.register_instrument(BassSynth(rate, block, polyphony=poly.get("bass_synth", 6)))
    engine.set_active_instrument(cfg["instruments"]["default"])
    return engine


def apply_song_presets(engine: AudioEngine, song_id: str) -> list[str]:
    applied = []
    for filename in SONG_PRESETS.get(song_id, []):
        path = PRESETS_ROOT / filename
        preset = load_preset(path) if path.exists() else None
        if not preset:
            continue
        backend = engine.instruments.get(preset.instrument)
        if backend and apply_preset(backend, preset):
            applied.append(preset.name)
    return applied


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("song", nargs="?", default="instant_crush", help="song id under songs/ (default: instant_crush)")
    parser.add_argument("--section", default="chorus", help="section id (default: chorus)")
    parser.add_argument("--layers", default=None, help="comma-separated layers to play (default: all layers that have data)")
    parser.add_argument("--loops", type=int, default=1, help="how many times to play the section (default: 1)")
    parser.add_argument("--speed", type=int, default=100, help="practice speed percent (default: 100)")
    parser.add_argument("--rate", type=int, default=48000, help="sample rate (default: 48000)")
    parser.add_argument("--block", type=int, default=256, help="audio block size in frames (default: 256)")
    parser.add_argument("--device", type=int, default=None, help="output device index (default: WASAPI default)")
    parser.add_argument("--no-presets", action="store_true", help="skip auto-applying the song's tuned presets")
    parser.add_argument("--list", action="store_true", help="list available songs/sections/layers and exit (no audio)")
    args = parser.parse_args()

    if args.list:
        return cmd_list()

    cfg = config_mod.load_config()
    mapping = KeyboardMapping.from_config(cfg["keyboard"])

    song_dir = SONGS_ROOT / args.song
    if not song_dir.exists():
        print(f"No such song: {args.song!r} (try --list)")
        return 1
    song = load_song(song_dir)
    if song.notes_disclaimer:
        print("=" * 78)
        print("SONG DATA STATUS")
        print("=" * 78)
        print(song.notes_disclaimer.strip())
        print("=" * 78)

    trainer = SongTrainer(song, mapping, lambda ev: engine.submit(ev))
    if not trainer.load_section(args.section, layer="chords"):
        print(f"No such section: {args.section!r} for {args.song!r} (try --list)")
        return 1
    trainer.set_mode(PracticeMode.AUTOPLAY)

    requested_layers = args.layers.split(",") if args.layers else ALL_LAYERS
    playable_layers = [layer for layer in requested_layers if trainer.has_playable_data([layer])]
    skipped = [layer for layer in requested_layers if layer not in playable_layers]
    if skipped:
        print(f"REFERENCE TRANSCRIPTION NOT READY for layer(s): {', '.join(skipped)} -- skipping (see NOTES_STATUS.md)")
    if not playable_layers:
        print("Nothing playable: none of the requested layers have reference data yet.")
        return 1
    trainer.set_autoplay_layers(playable_layers)
    trainer.set_speed_percent(args.speed)

    engine = build_engine(args.rate, args.block, args.device, cfg)
    if not args.no_presets:
        applied = apply_song_presets(engine, args.song)
        if applied:
            print(f"Applied presets: {', '.join(applied)}")

    duration_s = trainer.clock.tempo_map.beats_to_seconds(trainer.current_section.end_beat - trainer.current_section.start_beat)
    print(f"Playing {args.song}/{args.section}  layers={playable_layers}  speed={args.speed}%  "
          f"(~{duration_s:.1f}s/loop x {args.loops})")
    print("Ctrl+C to stop early.")

    engine.start()
    try:
        for i in range(max(1, args.loops)):
            trainer.start()
            while trainer.playing:
                trainer.tick()
                time.sleep(0.02)
            print(f"  loop {i + 1}/{args.loops} done")
        time.sleep(0.3)  # let release tails finish before closing the stream
    except KeyboardInterrupt:
        print("\nStopped.")
        trainer.stop()
    finally:
        engine.stop()

    return 0


if __name__ == "__main__":
    sys.exit(main())

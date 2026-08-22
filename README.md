# QWERTY Instrument -- Instant Crush Engine

Turns a normal mechanical keyboard (built and tuned around an **AULA F75**)
into a low-latency musical instrument, with a practice engine built around
Daft Punk's "Instant Crush" -- the chorus, and the lead/electric-guitar
material around ~3:18.

This is a real-time desktop instrument, not a MIDI-file player: pressing a
key produces a note immediately via an internal polyphonic synth engine
(synth lead, electric piano, plucked-string guitar), holding a key sustains
it, releasing stops it, and chords/rapid playing work without stuck notes.

## Status

V1. Genuinely working and tested on this machine (Windows 11, WASAPI,
USB audio interface): see "What's been verified" below. The Instant Crush
song profile currently ships with **no note transcription** -- infrastructure
only, honestly labeled as unverified, pending a legally-obtained MIDI file
(see `songs/instant_crush/NOTES_STATUS.md`).

## Requirements

- Windows 10/11 (the low-latency keyboard capture uses Win32 APIs directly)
- Python 3.12+

## Setup

```
python -m venv .venv
.venv\Scripts\activate
pip install -e .
python -m qwerty_instrument
```

Optional VST3 instrument hosting (experimental -- see `docs/ARCHITECTURE.md`):
```
pip install -e ".[vst]"
```

Dev/test dependencies:
```
pip install -e ".[dev]"
pytest
```

First launch runs a short setup wizard (audio output, a note on keyboard
capture, F75 knob detection, a latency check, a test scale) and saves your
choices to `%USERPROFILE%\.qwerty_instrument\config.toml`. Edit that file,
or copy `config.example.toml` as a starting point -- see comments inline.

## Playing it

- QWERTY keys play notes across ~2.3 octaves by default (see the keyboard
  mapping diagram below). **Space** sustains, **PageUp/PageDown** shift
  octaves, **Tab** cycles instruments (Synth -> Electric Piano -> Guitar),
  **Escape** is panic (all notes off), **CapsLock** toggles Instrument
  Capture on/off (off = your keyboard behaves normally again).
- The AULA F75's rotary knob (once confirmed via the probe tool below)
  controls pitch bend / expression, and a short press toggles Lead Guitar
  mode.
- Load a song from the top bar, pick a section and a mode
  (Real / Guided / Assist), and practice at a reduced speed with looping
  and a metronome.

## Keyboard mapping

Two overlapping "octave blocks", each built from a physical-key-stagger
zigzag (a home-row key sits between two bottom-row keys, exactly like a
piano's black keys sit between two white keys):

```
Block 1 (starts at C3):     bottom row  Z X C V B N M , . /   = white keys
                             home row    S D F G H J K L ;     = black keys
Block 2 (starts at C4):     top row     Q W E R T Y U I O P   = white keys
                             number row    2 3 4 5 6 7 8 9 0   = black keys
```

So `Z=C3, S=C#3, X=D3, D=D#3, C=E3, V=F3, G=F#3, B=G3, H=G#3, N=A3, J=A#3,
M=B3, ,=C4, L=C#4, .=D4, ;=D#4, /=E4`, and the same pattern one octave up
starting at `Q=C4`. Combined range: C3-E5. Fully configurable in
`config.toml`; run `python -m qwerty_instrument` and open **Audio
Settings > Export Practice Sheet** to generate `exports/keymap.txt` for
the exact current mapping.

## F75 knob test

```
.venv\Scripts\python.exe tools\f75_input_probe.py
```

Then, in that console: press a normal key, rotate the knob clockwise, rotate
it counter-clockwise, short-press it, and long-press it (~1s). The tool logs
everything it sees across three channels (low-level keyboard hook, raw
input, WM_APPCOMMAND) with timestamps -- see `docs/F75_SETUP.md` for how to
read the output and what to do if your knob doesn't behave like the
default assumption (VK_VOLUME_UP/DOWN/MUTE).

## Audio settings for this machine

Verified on this machine (USB Audio Device, WASAPI):

| Setting | Result |
|---|---|
| 256 frames, WASAPI shared | 22 ms reported latency, 0 underruns |
| 128 frames, WASAPI shared | 22 ms reported latency, 0 underruns |
| 64 frames, WASAPI shared | 22 ms reported latency, 0 underruns |
| 256 frames, WASAPI **exclusive** | **8 ms** reported latency, 0 underruns |

Recommended starting point: 256 frames, WASAPI shared (safest). Try
WASAPI exclusive mode in Audio Settings for lower latency if your device
supports it -- the app falls back to shared mode automatically if it
doesn't. Run `tools\audio_diagnostics.py --stress` to check your own
machine.

## Generating the practice sheet

```
.venv\Scripts\python.exe -c "
from pathlib import Path
from qwerty_instrument.songs.loader import load_song
from qwerty_instrument.songs.export import write_all_exports
from qwerty_instrument.music.mapping import KeyboardMapping
song = load_song(Path('songs/instant_crush'))
write_all_exports(song, KeyboardMapping(), Path('exports'), filename_stem='instant_crush')
"
```

Or from the app: load Instant Crush, then **Practice tab > Export Practice
Sheet**. Writes `exports/instant_crush_practice_sheet.md`,
`exports/keymap.json`, `exports/keymap.txt`. Right now the sheet will
correctly show the chorus and lead-guitar sections as having **no verified
notes yet** -- see "Adding real song data" below.

## Adding real song data

No copyrighted audio/MIDI is bundled or downloaded by this project. To
teach the app real notes, legally obtain a MIDI file (your own
transcription, a licensed file, etc.) and run:

```
.venv\Scripts\python.exe tools\import_midi.py path\to\file.mid
```

See `docs/ADDING_SONGS.md` for the full data format and workflow.

## Testing

```
pytest                              # 80+ unit/logic tests, no hardware needed
python tests\_offline_smoke.py      # DSP stress test (no audio device)
python tests\_hardware_smoke.py     # plays an audible test scale on your real speakers
python tests\_ui_smoke.py           # launches the real app+UI for 2s, verifies clean shutdown
```

## What's been verified (this session, on this machine)

- Full pytest suite (80+ tests): mapping, note lifecycle, chords, sustain,
  panic, octave shift, capture toggle, F75 knob math, song loading, MIDI
  import, scoring, timing/speed conversion, config load/save, presets.
- Real audio hardware: all three instruments play through actual WASAPI
  output at 256/128/64-frame blocks with zero underruns; WASAPI exclusive
  mode achieves 8ms latency on this device.
- Real Win32 low-level keyboard hook installs/uninstalls cleanly.
- Full application (audio engine + keyboard hook + Qt UI) starts and shuts
  down cleanly with no exceptions.
- **Not verified in this environment**: actual physical F75 key/knob
  presses (no physical keyboard attached to the dev environment) -- run
  `tools\f75_input_probe.py` yourself and see `docs/F75_SETUP.md`.

## Project layout

See `docs/ARCHITECTURE.md` for the full breakdown. Top level:

```
src/qwerty_instrument/   application code (audio, input, music, songs, ui, persistence)
songs/                   data-driven song profiles (instant_crush/ included)
presets/                 instrument patches (Warm Analog, Electric Keys, Lead Guitar, ...)
tools/                   f75_input_probe.py, audio_diagnostics.py, import_midi.py
tests/                   pytest suite + manual hardware/UI smoke scripts
docs/                    F75_SETUP, LATENCY_TUNING, ADDING_SONGS, ARCHITECTURE
exports/                 generated practice sheets / keymaps (gitignored)
```

## Known limitations

- Instant Crush ships with zero verified notes -- infrastructure only.
- VST3 hosting (via Pedalboard) is experimental/best-effort; see
  `docs/ARCHITECTURE.md` for why it can't guarantee sample-accurate
  real-time streaming the way the internal instruments do.
- SoundFont (.sf2) playback is not implemented for V1 -- the internal
  Electric Piano synth is the supported fallback.
- No physical F75 was available in this dev environment; knob integration
  is built for its most likely default behavior (VK_VOLUME_UP/DOWN/MUTE)
  and needs confirmation via the probe tool on your actual hardware.

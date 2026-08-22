# Architecture

## Signal path

```
AULA F75 / QWERTY keyboard
        |
Win32 WH_KEYBOARD_LL hook (input/windows_hook.py)  <-- runs on its own thread
        |
raw (key_id, is_down, timestamp_ns)
        |
        +--> F75Knob (input/f75.py)          -- VOLUME_UP/DOWN/MUTE -> knob events
        +--> KeyboardInstrument (input/keyboard.py) -- everything else -> MusicEvent
        +--> SongTrainer.handle_performance_key -- Assist-mode keys, if a song is loaded
                |
                v
        MusicEvent (music/events.py)
                |
                v
        queue.Queue  (AudioEngine.event_queue)
                |
                v
        AudioEngine._callback (audio/engine.py) <-- PortAudio's own OS thread
                |
        drains queue -> instrument.note_on/note_off/set_param/pitch_bend_active
                |
        instrument.render(n_frames) -> Limiter -> outdata
```

Everything upstream of the audio callback only ever talks to it through
`AudioEngine.submit(event)`, which is a non-blocking `queue.Queue.put_nowait`.
This is the one piece of shared mutable state between threads, and it's
intentionally simple: Python's GIL plus a short critical section inside
`Queue` is fast enough here (put/get are sub-microsecond in practice), and
a genuinely lock-free ring buffer would add real complexity for a benefit
that doesn't show up in the measured callback timings (see
`docs/LATENCY_TUNING.md`). If profiling on a slower machine ever showed
queue contention as a real cost, that's the first thing to revisit.

## Threading model

| Thread | Owns | Never does |
|---|---|---|
| Win32 hook thread (`input/windows_hook.py`) | the LL hook + its own Win32 message pump | logging, allocation beyond a dict lookup, anything blocking |
| Qt/UI thread | all widgets, `QTimer` polling `engine.ui_feedback_queue`, `SongTrainer.tick()` | touching audio engine internals directly (only `engine.submit`) |
| PortAudio callback thread (`audio/engine.py`) | voice state, DSP, the output buffer | file I/O, network, UI calls, `time.sleep`, logging (except rate-limited warnings), large allocations |
| Background writer thread (`persistence/recording.py`) | WAV file writes, draining a queue the callback fed | anything synchronous with the callback |

The audio callback is the one piece of code in this project held to a hard
real-time discipline: no disk I/O, no plugin discovery, no blocking locks,
no `time.sleep`. `AudioEngine._warm_up()` exists specifically to move the
one-time costs (numpy ufunc dispatch, scipy import) out of the first
real-time callback and onto the calling thread before the stream opens --
measured without it, the very first callback took ~20ms against a 5.3ms
budget; every callback since (256-frame blocks) has been under budget with
zero underruns.

## Internal musical event bus

`music/events.py` defines `MusicEvent` (NOTE_ON/OFF, PITCH_BEND,
EXPRESSION, MODULATION, SUSTAIN, PROGRAM_CHANGE, MODE_CHANGE,
TRANSPORT_START/STOP, ALL_NOTES_OFF) with a `source` field (QWERTY,
F75_KNOB, MIDI_IN, SONG_PLAYBACK, UI, ASSIST_ENGINE). Nothing downstream
of this event type cares which source produced an event -- that's what
lets `input/midi.py` (a real MIDI keyboard) feed the exact same engine a
QWERTY key press does, with zero changes to `audio/engine.py` or the song
engine, satisfying the "future MIDI keyboard" requirement without any
speculative abstraction beyond this one dataclass.

## No-stuck-notes guarantee

Every active voice is owned by `MusicEvent.voice_id`, a
`(source, channel, key_id)` tuple (`audio/voices.py`). A NOTE_OFF can only
release the exact voice it belongs to -- two different physical keys
mapped to the same pitch can't steal each other's release, and switching
octave mid-hold doesn't strand a voice, because release routing never
looks at pitch at all.

Cleanup paths, all converging on `InstrumentBackend.all_notes_off()` /
`hard_silence()`:

- **panic** (Escape key, UI panic button): `all_notes_off()` on every
  registered instrument -- a fast, click-free release (each instrument
  can override how "fast": the guitar's normal release can take several
  seconds, so panic uses a distinct `panic_release()` path that scales the
  ring buffer down immediately and switches to aggressive damping,
  reaching silence in ~50ms instead of ~3s -- see `audio/guitar.py`).
- **instrument switch / PROGRAM_CHANGE**: `AudioEngine.panic()` runs
  first, then the active instrument changes, so a note started on one
  instrument can never be orphaned by switching to another.
- **capture toggle off** (CapsLock): `KeyboardInstrument.panic()` clears
  every physically-held key.
- **shutdown / device change**: `hard_silence()` unconditionally discards
  all voice state (used instead of the click-free path, since audio
  quality doesn't matter once the stream is closing).

## Instrument backends

`audio/voices.py` defines `Voice` (per-note synthesis + envelope state) and
`InstrumentBackend` (a fixed-size voice pool with allocation, oldest-voice
stealing when the pool is full, and the panic/cleanup paths above). Three
concrete instruments:

- **`audio/synth.py: SynthLead`** -- 1-3 detuned oscillators (saw/square/
  sine), a Chamberlin state-variable low-pass per voice, ADSR, saturation +
  chorus + delay + reverb on the mixed output.
- **`audio/synth.py: ElectricPiano`** -- sine carrier + a fast-decaying
  higher partial ("tine") for the bright attack, an exponential
  decay-while-held envelope layered on top of the ADSR (real electric
  pianos fade even while a key is held), tremolo, chorus.
- **`audio/guitar.py: GuitarLead`** -- extended Karplus-Strong: a
  per-voice ring buffer primed with filtered noise on pluck, read back
  through a fractional (linearly-interpolated) delay tap so the delay
  length -- and therefore pitch -- can be smoothly bent in real time (the
  F75 knob's pitch-bend/expression modes both drive this). This is the one
  voice type that runs a genuine per-sample Python loop rather than
  vectorized numpy, because the delay length *is* the pitch period and is
  usually well under one audio block at musical pitches -- it can't be
  vectorized the way the independent `Delay` effect can (see below).
  Compression, saturation, chorus, delay, reverb on the output.

All three share `audio/effects.py`: `ADSR` (block-vectorized via a closed-
form linear ramp, with correct multi-block progress tracking -- an earlier
version recomputed a full-duration ramp every block without tracking
elapsed samples across calls, which silently left released voices stuck in
the RELEASE stage forever; fixed and covered by
`tests/test_voices.py::test_note_off_eventually_frees_the_voice`),
`StateVariableFilter` (per-sample, the other deliberate exception to
vectorization, for the same reason as the guitar's delay tap), `Delay` /
`SchroederReverb` / `Chorus` (vectorized per block -- safe because their
delay times are always >= one block period, so a block's reads never
depend on writes from the same block), `saturate` (tanh soft-clip),
`Compressor` (block-rate envelope follower), `Limiter` (smoothed gain
reduction + a hard tanh ceiling as an absolute safety net -- this runs on
every instrument's output in `AudioEngine._callback`, independent of
anything upstream), and `smooth_ramp` (closed-form exponential smoothing
for knob-driven parameters, avoiding zipper noise).

## VST3 (optional, experimental)

`audio/vst.py` wraps Pedalboard's `VST3Plugin`. Investigated as required by
the spec, and worth being honest about: Pedalboard's plugin call is
fundamentally a *batch* renderer -- `plugin(midi_messages, duration,
sample_rate, reset=...)` renders a whole time span in one call; there is no
native "process this one 256-frame block" streaming API. This backend
approximates real-time streaming by calling it once per audio callback with
`reset=False` (which preserves internal plugin state / voice tails between
calls per Pedalboard's own docs), which works for many instruments but
isn't the same sample-accurate guarantee a native VST host gives. Because
of that, `VST3Backend.is_available` and plugin-reported latency
(`reported_latency_samples`) are surfaced rather than hidden, this backend
is entirely opt-in, and every other instrument has zero dependency on it
working. No VST3 plugin was available to test against in this dev
environment -- treat this backend as unverified until tried against a real
plugin.

## Song / practice engine

`songs/model.py` (data classes), `songs/loader.py` (YAML/JSON -> `Song`,
lenient about missing/malformed optional fields), `songs/midi_import.py`
(the only sanctioned way verified note data enters a song -- see
`docs/ADDING_SONGS.md`), `songs/trainer.py` (`SongTrainer`: Real/Guided/
Assist modes, section looping, `PracticeClock` speed scaling, metronome,
Assist-mode performance-key routing), `songs/scoring.py` (four-bucket
timing judgement: Perfect/Good/Early-or-Late/Miss, millisecond error kept
for diagnostics). `SongTrainer` never touches the audio engine directly --
same `MusicEvent`/`event_sink` pattern as the input layer.

`music/timing.py`'s `TempoMap` stores tempo in beats and converts to
wall-clock seconds (and back, via `seconds_to_beats`, an actual inverse
segment-walk, not an approximation) -- this is the one place practice-speed
scaling and tempo changes are handled, so nothing downstream needs to know
about either.

## Configuration

`config.py`: TOML in, TOML out, deep-merged onto `DEFAULT_CONFIG` so a
missing or malformed field never crashes startup -- it's dropped with a
logged warning and the default is kept. One real gotcha found while
building this: TOML has no null literal, so a field left at its Python
`None` default (`audio.device_index` meaning "use system default") made
`tomli_w.dump` raise; `save_config` now strips `None`-valued fields before
writing, and `load_config`'s merge restores them from `DEFAULT_CONFIG` on
the next load.

## Known engineering tradeoffs

- **No lock-free queue.** `queue.Queue` was measured, not assumed, to be
  fast enough (see the callback-timing numbers in
  `docs/LATENCY_TUNING.md`). Revisit only if profiling on real hardware
  shows otherwise.
- **Guitar polyphony capped at 6 by default**, synth/e-piano at 16 -- the
  per-sample Python loop in `GuitarVoice.render` is the most expensive
  code path in the engine; this default is conservative pending
  per-machine measurement via `tools/audio_diagnostics.py --stress`.
- **Metronome clicks reuse whichever melodic instrument is active** rather
  than a dedicated percussive click generator, to avoid a fourth
  always-resident instrument backend for V1. Functional, not ideal --
  a dedicated short click/noise voice would be the natural next step.
- **Assist-mode "multi-lane"** maps a fixed 4 keys (J/K/L/;) to a fixed 4
  layer names (melody/chords/bass/lead_guitar) rather than a fully
  configurable per-song lane mapping -- covers the spec's stated modes
  without a config surface that has no real content to exercise it yet
  (Instant Crush has no verified notes to route through it).

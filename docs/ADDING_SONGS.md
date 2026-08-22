# Adding Songs

The song engine is entirely data-driven (`songs/model.py`,
`songs/loader.py`) -- Instant Crush is not special-cased anywhere in
`src/qwerty_instrument/`, it's just one directory of data conforming to
this format. Adding a new song means creating a new directory under
`songs/`.

## Directory layout

```
songs/<song_id>/
    song.yaml       required: title, artist, time signature, tempo map
    sections.yaml   required: section list (id, name, beat range, difficulty)
    notes.json      optional: note/chord event data (see below)
    mappings.yaml   optional: song-specific keyboard/knob overrides
```

A song with `song.yaml` + `sections.yaml` but no `notes.json` loads fine --
every section just shows as having no verified notes yet (this is
Instant Crush's current state; see `songs/instant_crush/NOTES_STATUS.md`).
The loader is deliberately lenient: a malformed optional field is dropped
with a logged warning, not a crash.

## `song.yaml`

```yaml
title: "Song Title"
artist: "Artist Name"
time_signature: [4, 4]
tempo_events:
  - beat: 0
    bpm: 120.0
  - beat: 64          # a tempo change partway through, if needed
    bpm: 128.0
notes_disclaimer: "Optional free-text status note shown in the UI/practice sheet."
knob_actions:
  - beat: 396
    action: "mode_lead_guitar"    # or bend_up / bend_down / mode_normal
instrument_changes:
  - beat: 0
    instrument: "synth_lead"
  - beat: 396
    instrument: "guitar_lead"
```

Tempo is stored in **beats**, converted to wall-clock seconds by
`music/timing.py: TempoMap` -- this is also where practice-speed scaling
(50%-100%+) happens, in exactly one place, so nothing else needs to know
about it.

## `sections.yaml`

```yaml
sections:
  - id: chorus
    name: "Chorus"
    start_beat: 32
    end_beat: 96
    difficulty: medium      # free text, shown in the UI
    loop_default: true
```

## `notes.json`

```json
{
  "notes": [
    {"beat": 0.0, "duration_beats": 1.0, "note": 60, "velocity": 0.9, "layer": "melody", "section": "chorus", "verification": "verified"}
  ],
  "chords": [
    {"beat": 0.0, "duration_beats": 4.0, "name": "Cmaj", "notes": [48, 52, 55], "layer": "chords", "section": "chorus", "verification": "verified"}
  ]
}
```

- `note`/`notes` are **MIDI pitch numbers**, not note names -- unambiguous
  about octave/voicing.
- `layer` is free text (`melody`, `chords`, `bass`, `lead_guitar`, ...) --
  the practice engine's Assist multi-lane mode and the practice-sheet
  exporter both group by this field, and the trainer's `load_section()`
  takes a `layer` argument to select which one is currently playable.
- `section` matches a `sections.yaml` id; if omitted, the loader assigns
  the event to whichever section's beat range contains it.
- `verification`: `"verified"` (came from a real MIDI/reference import) or
  `"placeholder"` (default if omitted) -- this is what makes the UI and
  practice sheet honest about what's real. **Never hand-write
  `"verified": true` for a guess** -- that defeats the entire point of the
  field.

## Importing from a real MIDI file (the only sanctioned way to add verified notes)

This project does not download, scrape, or bundle copyrighted audio/MIDI.
If you have a MIDI file you're legally entitled to use (your own
transcription, a licensed file, etc.):

```
.venv\Scripts\python.exe tools\import_midi.py path\to\file.mid
```

Interactively, it will:

1. Show every track: name, channels, General MIDI instrument (if a
   `program_change` is present), note count.
2. Show every tempo event (beat position + BPM) and the file's total
   duration -- use this to correct `song.yaml`'s `tempo_events`.
3. Let you pick which track(s) to import, assign a `layer`, and an
   optional transpose (semitones).
4. Write (or append to) a `notes.json` in the song directory you specify,
   with `verification: "verified"` automatically set.

Non-interactive/scriptable form:
```
.venv\Scripts\python.exe tools\import_midi.py file.mid --non-interactive --tracks 1,2 --layer melody --section chorus --song-dir songs\my_song
```

After importing, correct `sections.yaml`'s `start_beat`/`end_beat` to
match the real tempo (the importer prints real tempo/duration to help with
this -- placeholder section boundaries computed from a guessed tempo will
be wrong once the real tempo is known).

## `mappings.yaml` (optional)

Song-specific keyboard/knob overrides, loaded via
`songs.loader.load_song_mapping_overrides()`. Not required -- omit it
entirely if the default keyboard mapping and knob behavior suit the song.

## Testing a new song without copyrighted data

`tests/test_song_loader.py` and `tests/test_midi_import.py` show the
pattern: build a synthetic song directory / MIDI file in a pytest
`tmp_path` fixture (or with `mido` directly, as `test_midi_import.py`
does) rather than committing any real song's data as a test fixture.

# Instant Crush -- data verification status

This file exists so it's never ambiguous what is and isn't real in this
song profile.

## What's real

- The song exists as a loadable profile (`song.yaml`, `sections.yaml`)
  with two sections matching the priorities in the project brief:
  **Chorus** and **Lead / Electric Guitar (~3:18)**.
- The Real/Guided/Assist/Autoplay practice engine, section looping, speed
  control, and practice-sheet exporter all work against this profile today.
- `~3:18` for the lead-guitar section is the timestamp you provided;
  everything else in `sections.yaml` is a placeholder computed from it.
- `tempo_events` uses 110 BPM, sourced from external song-metadata
  research (not measured against this project's own audio -- see
  `song.yaml`'s comment).

## What's NOT real (yet)

- **Chorus chords/bass/melody** (`notes.json`): an APPROXIMATE / REFERENCE
  SKELETON -- chords follow a documented chord-change sequence, the melody
  is an original musically-defensible sketch (not a transcription of the
  actual vocal line), and the bass follows the chord roots/inversions.
  Every event is explicitly `"verification": "placeholder"`, never
  `"verified"`. This is a development benchmark for Autoplay/Guided/
  Assist/Real, not a claim of matching the real recording note-for-note.
- **Lead-guitar section**: still fully unverified, no notes.json content
  at all (untouched by the chorus accuracy pass).
- **Section boundaries**: `start_beat`/`end_beat` in `sections.yaml` are
  still placeholders, not verified timestamps (the chorus's 32-beat/8-bar
  length is a development-benchmark choice, not a measured section length).

Nothing here was invented to "fill in" a transcription -- the project
explicitly refuses to teach notes that haven't been verified. See
`tools/reference_inspect.py` if you want to calibrate timing against a
legally-obtained local reference file (never committed -- see
`.gitignore`).

## How to add real data

1. Obtain a MIDI file or your own transcription **legally** (purchase,
   license, or your own manual transcription by ear). This project does
   not download, scrape, or bundle copyrighted audio or MIDI.
2. Run the importer:
   ```
   .venv\Scripts\python.exe tools\import_midi.py path\to\your_file.mid
   ```
   It will show you every track, channel, instrument, and tempo event in
   the file, let you pick which track(s) to use for melody/chords/bass/
   lead guitar, and can write a `notes.json` into this directory.
3. Correct `tempo_events` in `song.yaml` and `start_beat`/`end_beat` in
   `sections.yaml` to match what the MIDI file actually reports (the
   importer prints the real tempo and duration to help with this).
4. Re-run the practice-sheet exporter; it will now include the real notes
   instead of an empty/placeholder section.

See `docs/ADDING_SONGS.md` for the full song data format.

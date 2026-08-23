# Instant Crush -- data verification status

This file exists so it's never ambiguous what is and isn't real in this
song profile. **Accuracy pass v3** rebuilt this from scratch around a real
local reference recording -- read this before trusting anything below.

## What's real (measured from the local reference recording)

- **Tempo**: 112.35 BPM, measured via two independent librosa methods
  (beat-tracking + tempogram) that agreed within 1 BPM. Replaces the
  earlier 110/120 BPM guesses. See
  `reference_analysis/beat_grid.json`.
- **Chorus section timing**: 209.77s-227.23s, found via chroma
  self-similarity (0.999 match score -- the most self-repeating 32-beat
  window in the song). This is a heuristic ("chorus-like" = "repeats"),
  not a human-confirmed structural label. See `reference_analysis/sections.json`.
- **Lead-guitar section**: still the user-provided ~3:18 approximate
  timestamp, recomputed at the corrected tempo; its end has been trimmed
  to where the measured chorus candidate begins (an inference, not a
  verified boundary).

## What's NOT real (yet)

- **Melody, chords, bass content**: none. A full-mix analysis pass ran
  (`tools/analyze_reference.py`, no stem separation available -- Demucs/
  torch is a multi-GB dependency chain not installed in this environment)
  and produced candidate pitch/chord data, but confidence was too low to
  teach as fact:
  - melody: 43 candidates, mean confidence **0.01** (essentially noise --
    pyin found almost nothing it considered clearly voiced/pitched in the
    full mix)
  - bass: 69 candidates, mean confidence **0.06**, max **0.21**
  - harmony (chroma template matching): 31 candidates, mean confidence
    **0.32**
  
  See `reference_analysis/reports/analysis_report.txt` and
  `reference_analysis/candidates/*.json` for the raw numbers.
- **A previous pass's invented chord/melody data has been archived**
  (`archive/notes_v2_synthetic_reference.json`) and is **not loaded** as
  canonical data anymore. It was built from a documented chord-change
  sequence and an original melodic sketch -- not the actual recording --
  and user feedback was that it sounded like a different song. It will
  not be resurrected as canonical/learnable content.

Nothing here was invented to "fill in" a transcription. The canonical
`notes.json` is honestly empty for the chorus right now. `SongTrainer.has_playable_data()`
gates Autoplay/Guided/Assist/Real so the app tells you "REFERENCE
TRANSCRIPTION NOT READY" rather than silently playing/teaching nothing,
or worse, silently teaching a guess.

## How to add real data

**Path A -- MIDI import (preferred if you have/can make one):**
```
.venv\Scripts\python.exe tools\import_midi.py path\to\your_file.mid
```

**Path B -- improve the reference-analysis pipeline:**
1. The biggest lever is stem separation (isolating vocals/bass before
   pitch-tracking) -- not attempted here due to the Demucs/torch dependency
   size. If you can install it locally: separate `instant_crush.mp3`,
   re-run `tools/analyze_reference.py` pointed at the vocal/bass stems
   instead of the full mix, and confidence should improve substantially.
2. Or manually correct/hand-edit the low-confidence candidates in
   `reference_analysis/candidates/*.json` by ear, using
   `tools/reference_inspect.py --start SS --end EE --onsets` to check
   timing against the actual audio.
3. Once you're confident in some events, promote them:
   ```
   .venv\Scripts\python.exe tools\build_reference_song.py --section chorus --min-confidence 0.6
   ```
   This only writes events that clear the threshold, marked
   `"verification": "reference_derived"` (never `"verified"`) with their
   confidence and exact-seconds timing preserved.

See `docs/ADDING_SONGS.md` for the full song data format, and
`docs/ARCHITECTURE.md` for how reference-derived timing (seconds) and the
beat grid coexist.

# Instant Crush -- data verification status

This file exists so it's never ambiguous what is and isn't real in this
song profile. **Accuracy pass v3.1** added Demucs stem separation on top
of v3's measurement pipeline -- read this before trusting anything below.

## What's real (measured from the local reference recording)

- **Tempo**: 112.35 BPM, measured via two independent librosa methods
  (beat-tracking + tempogram) that agreed within 1 BPM. See
  `reference_analysis/beat_grid.json`.
- **Chorus section timing**: 209.77s-227.23s, found via chroma
  self-similarity (0.999 match score -- the most self-repeating 32-beat
  window in the song). A heuristic ("chorus-like" = "repeats"), not a
  human-confirmed structural label. See `reference_analysis/sections.json`.
- **Chorus CHORDS**: 14 events, `verification: "reference_derived"`,
  confidence 0.53-0.80. Derived by separating the track with Demucs
  (`htdemucs` model) and running chroma template matching against the
  isolated **"other" stem** (synths/guitars -- no vocals/drums/bass to
  muddy the chroma vector), then merging consecutive same-chord beats into
  sustained spans (so it doesn't re-attack every beat -- see accuracy pass
  v2's articulation fix for why that mattered). This measured a real,
  repeating **A#m - F#maj - D#m - G#maj (- brief Fmin passing chord)**
  progression -- notably, the same chord *letters* as the earlier archived
  v2 guess (Bbm-Gb-Ebm-Ab is enharmonically identical), now independently
  confirmed from actual audio, though v2's rhythm/voicing/timing were
  still invented and v3.1's aren't. See
  `reference_analysis/candidates/harmony.json` for the raw per-beat data
  before merging.
- **Lead-guitar section**: still the user-provided ~3:18 approximate
  timestamp, recomputed at the corrected tempo; its end has been trimmed
  to where the measured chorus candidate begins (an inference, not a
  verified boundary).

## What's NOT real (yet)

- **Melody**: 0 usable candidates. Demucs' vocal-isolation model produced
  a near-silent "vocals" stem for this track (RMS ~0.0026 vs ~0.11-0.12
  for the other three stems -- roughly 40x quieter, essentially noise
  floor). Most likely cause: Instant Crush's vocal has a distinctive
  vocoder/talk-box-style processing that doesn't match what htdemucs was
  trained to recognize as "vocals". Checked whether the processed vocal
  leaked into the "other" stem instead -- pyin found some content there
  too, but confidence stayed very low (mean 0.027, max 0.233), not usable.
- **Bass**: improved with stem separation (full-mix mean confidence 0.06
  -> bass-stem mean 0.21, max 0.53) but only **one single note** cleared
  the 0.5 promotion threshold -- nowhere near enough coverage for a
  coherent bass line, so nothing was promoted for this layer.
- **A previous pass's fully-invented chord/melody data has been archived**
  (`archive/notes_v2_synthetic_reference.json`) and is **not loaded** as
  canonical data. It will not be resurrected.

`SongTrainer.has_playable_data()` gates Autoplay/Guided/Assist/Real per
layer: chords now play (Autoplay -> Chords, or Full Chorus, which plays
real chords over silent/empty bass+melody), melody and bass still show
"REFERENCE TRANSCRIPTION NOT READY".

## How to improve further

**Melody** (the highest-value remaining gap):
- Try a different Demucs model variant (`--name` flag on the CLI, e.g. a
  model specifically tuned for vocal isolation) against the same
  `instant_crush.mp3` -- htdemucs' default vocal separation simply failed
  for this track's vocal processing style.
- Or accept that automatic extraction may not work here and transcribe
  the melody by ear using `tools/reference_inspect.py --start SS --end EE
  --onsets` to check timing against the actual audio, then hand-write the
  events with `"verification": "reference_derived"` (or import a licensed
  MIDI/sheet-music transcription via `tools/import_midi.py`).

**Bass**: the isolated bass stem is usable (`reference_analysis/stems/htdemucs/instant_crush/bass.wav`
if regenerated locally -- stems themselves are never committed); either
lower `--min-confidence` cautiously and manually verify each promoted
note by ear, or hand-correct the low-confidence candidates in
`reference_analysis/candidates/bass.json`.

**Regenerating everything:**
```
.venv\Scripts\python.exe -m demucs -o songs\instant_crush\reference_analysis\stems instant_crush.mp3
.venv\Scripts\python.exe tools\analyze_reference.py
.venv\Scripts\python.exe tools\build_reference_song.py --section chorus --min-confidence 0.5
```
(Stem separation takes a few minutes on CPU; the model weights download
once on first run.) `build_reference_song.py` only ever writes events
that clear the confidence threshold, marked `reference_derived`, never
`verified`.

See `docs/ADDING_SONGS.md` for the full song data format, and
`docs/ARCHITECTURE.md` for how reference-derived timing (seconds) and the
beat grid coexist.

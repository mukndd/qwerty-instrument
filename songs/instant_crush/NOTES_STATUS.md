# Instant Crush -- data verification status

This file exists so it's never ambiguous what is and isn't real in this
song profile. **Accuracy pass v3.3 corrected a wrong section pick** from
v3.1/v3.2 -- read this before trusting anything below.

## The correction (why the chorus data changed again)

v3.1/v3.2 identified a chorus candidate at 209.77s by finding the single
best-matching pair of repeated windows in the song via chroma
self-similarity. That's a weak test on its own: a strong LOCAL match
doesn't prove a section is structurally significant, only that it repeats
*once*, somewhere nearby. User feedback ("doesn't sound like the song at
all") turned out to be correct -- investigation found:

1. **The whole-song RMS energy contour** (computed in 2s windows) shows a
   long, sustained high-energy block from ~180s-260s with no clean
   boundary inside it -- and the user's own ~3:18 (198s) guitar-solo
   landmark falls right in the middle of that block, not at an edge. The
   209.77s pick was very likely catching solo-adjacent material.
2. **Scanning the whole track** for other occurrences of that 209.77s
   pattern found nothing else above ~0.85 similarity anywhere else in the
   song -- a real chorus should recur 2-3+ times at high similarity across
   a typical pop structure; this pattern basically didn't.

Fix: stopped trusting a single best pair. Computed the energy contour to
find genuinely isolated candidate peaks (choruses are almost always the
loudest/fullest sections), found a strong beat-aligned match between two
widely-separated peaks (~88s and ~228s, similarity 0.95), then scanned the
**entire track** for every occurrence of that pattern. Found **4 total**
(88.50s seed + 237.00s, 263.25s, 298.00s -- similarity 0.87-0.91 each, all
well outside the solo region) -- much stronger, genuinely structural
evidence. `tools/analyze_reference.py` now does this automatically for any
section via `find_all_occurrences()`, and averages chroma across all
confirmed occurrences before chord-matching via
`average_chroma_occurrences()` (cancels out per-occurrence noise/artifacts
that a single measurement can't).

## What's real (measured from the local reference recording)

- **Tempo**: 112.35 BPM, measured via two independent librosa methods
  (beat-tracking + tempogram) that agreed within 1 BPM. See
  `reference_analysis/beat_grid.json`.
- **Chorus section timing**: 88.50s-105.50s (8 bars, 17.0s), the CORRECTED
  location. Cross-validated against 3 additional independent occurrences
  elsewhere in the track (see above). See `reference_analysis/sections.json`
  for the full `cross_validated_occurrences` list.
- **Chorus CHORDS**: 14 events, `verification: "reference_derived"`,
  confidence 0.51-0.81 (mean 0.67 -- up from 0.32 on the original wrong
  section using the full mix, and from 0.63 on the wrong section using a
  single stem measurement). Derived by separating the track with Demucs
  (`htdemucs`), chroma template matching against the isolated **"other"
  stem** (synths/guitars -- no vocals/drums/bass) at 2 windows/beat, using
  the chroma **averaged across all 4 confirmed occurrences**, then merging
  consecutive same-estimate windows into sustained spans. The measured
  progression: **A#min - F#maj - D#min - (brief A#min) - G#maj - Fmin -
  C#maj**, repeating twice within the 8-bar chorus (a clear 4-bar unit).
  Notably the same chord *letters* as the original archived v2 guess
  (Bbm-Gb-Ebm-Ab is enharmonically identical to A#m-F#-D#m-G#), now
  independently confirmed from real audio across 4 separate recordings of
  the same phrase, though v2's rhythm/voicing/timing were invented and
  v3.3's aren't, and v3.3 adds real Fmin/C#maj passing chords v2 never had.
- **Lead-guitar section**: still the user-provided ~3:18 approximate
  timestamp, recomputed at the corrected 112.35 BPM. No longer adjacent to
  the chorus (which moved much earlier in the song), so its end_beat is
  back to a plain +32-beat placeholder.

## What's NOT real (yet)

- **Melody**: 0 usable candidates on the corrected section too. Demucs'
  vocal-isolation model produces near-silent output for this entire track
  (checked across the whole song, not just one section) -- most likely
  cause: Instant Crush's vocoder/talk-box-style vocal processing doesn't
  match what htdemucs was trained to recognize as "vocals".
- **Bass**: 0 of 44 candidates cleared the promotion threshold on the
  corrected (shorter, 17s) section -- worse coverage than the wrong
  section simply had more raw material to sample from, not because bass
  extraction itself improved or worsened.
- **A previous pass's fully-invented chord/melody data has been archived**
  (`archive/notes_v2_synthetic_reference.json`) and is **not loaded**.

`SongTrainer.has_playable_data()` gates Autoplay/Guided/Assist/Real per
layer: chords play, melody and bass show "REFERENCE TRANSCRIPTION NOT
READY".

## How to improve further

**Melody** (the highest-value remaining gap): try a different Demucs
model variant (`--name` flag) or a dedicated vocal-isolation model against
`instant_crush.mp3` -- htdemucs' default vocal separation fails for this
track's vocal processing style specifically, not for audio analysis in
general. Or transcribe by ear using
`tools/reference_inspect.py --start 88.50 --end 105.50 --onsets`, then
hand-write events with `"verification": "reference_derived"`.

**Regenerating everything** (stems are cached from the last run; only
`analyze_reference.py` needs re-running to reproduce the current data):
```
.venv\Scripts\python.exe tools\analyze_reference.py --chorus-start 88.50 --chorus-end 105.50
.venv\Scripts\python.exe tools\build_reference_song.py --section chorus --layers harmony --min-confidence 0.5
```
(Omitting `--chorus-start`/`--chorus-end` re-runs the automatic section
search, which may or may not land on the same corrected location --
`find_repeated_section_candidate()`'s base heuristic still just picks the
single best pair; the cross-validation step in `main()` now warns loudly
in the log if a candidate has no well-separated repeats elsewhere, which
is the signal that caught this bug -- but it doesn't yet automatically
retry a different candidate if the first one fails that check.)

See `docs/ADDING_SONGS.md` for the full song data format, and
`docs/ARCHITECTURE.md` for how reference-derived timing (seconds) and the
beat grid coexist.

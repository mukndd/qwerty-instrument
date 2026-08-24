"""Offline reference-audio analysis pipeline for Instant Crush.

Reads the local ./instant_crush.mp3 (never committed -- see .gitignore)
and writes measured/derived analysis data to
songs/instant_crush/reference_analysis/ (also gitignored). This is a
development tool only -- normal application startup has zero dependency
on it or on the reference file being present.

What this DOES measure with real confidence:
    - tempo / beat grid (cross-checked via two independent librosa methods)
    - onset times (energy/spectral-flux based)
    - a chroma-self-similarity chorus-section CANDIDATE (heuristic --
      confirm/correct by ear with tools/reference_inspect.py --start --end)

What this attempts but is HONESTLY LOW CONFIDENCE, because it runs on the
full mix with no stem separation (Demucs was investigated -- see
docs/ARCHITECTURE.md -- but not installed in this environment: torch +
demucs is a multi-GB dependency chain impractical to add here; this is a
documented limitation, not a silent gap):
    - melody pitch candidates (pyin on the full mix -- vocals are not
      isolated, so this is contaminated by every other instrument)
    - bass pitch candidates (pyin restricted to a low frequency band --
      contaminated by kick drum fundamental and sub content)
    - harmony/chord candidates (chroma template matching -- contaminated
      by melody/vocal pitch content bleeding into the chroma vector)

Every derived event carries a `confidence` value. Nothing here is ever
marked "verified" -- see music model NoteVerification.REFERENCE_DERIVED.
Low-confidence output is not hidden, just honestly labeled, so a human
(and tools/build_reference_song.py) can decide what's actually usable.

Usage:
    .venv\\Scripts\\python.exe tools\\analyze_reference.py
    .venv\\Scripts\\python.exe tools\\analyze_reference.py --audio instant_crush.mp3 --chorus-start 42.0 --chorus-end 59.1
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

try:
    import librosa
    import numpy as np

    LIBROSA_AVAILABLE = True
except ImportError:
    LIBROSA_AVAILABLE = False

SR = 22050  # analysis sample rate -- plenty for tempo/onset/pitch work, much faster than 44.1k
OUT_DIR = Path("songs/instant_crush/reference_analysis")


def log(msg: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


# ---- tempo / beat grid ------------------------------------------------------

def measure_tempo_and_beats(y: "np.ndarray", sr: int) -> dict:
    onset_env = librosa.onset.onset_strength(y=y, sr=sr)
    tempo_beat_track, beat_frames = librosa.beat.beat_track(y=y, sr=sr)
    tempo_tempogram = librosa.feature.tempo(onset_envelope=onset_env, sr=sr)

    beat_times = librosa.frames_to_time(beat_frames, sr=sr).tolist()
    intervals = np.diff(beat_times) if len(beat_times) > 1 else np.array([])
    median_bpm_from_intervals = float(60.0 / np.median(intervals)) if len(intervals) else None

    t1 = float(tempo_beat_track[0]) if hasattr(tempo_beat_track, "__len__") else float(tempo_beat_track)
    t2 = float(tempo_tempogram[0])
    agreement = abs(t1 - t2) < 1.0

    return {
        "beat_track_bpm": t1,
        "tempogram_bpm": t2,
        "median_beat_interval_bpm": median_bpm_from_intervals,
        "methods_agree_within_1bpm": bool(agreement),
        "confidence": "high" if agreement else "low -- methods disagree, inspect manually",
        "recommended_bpm": t1 if agreement else None,
        "beat_times_seconds": beat_times,
        "n_beats": len(beat_times),
        "first_beat_seconds": beat_times[0] if beat_times else None,
    }


# ---- onsets -------------------------------------------------------------------

def measure_onsets(y: "np.ndarray", sr: int) -> list[float]:
    onset_frames = librosa.onset.onset_detect(y=y, sr=sr, backtrack=True)
    return librosa.frames_to_time(onset_frames, sr=sr).tolist()


# ---- chorus-section candidate via chroma self-similarity ----------------------

def find_repeated_section_candidate(y: "np.ndarray", sr: int, beat_times: list[float], window_beats: int = 32) -> dict:
    """Heuristic, not a claim of ground truth: finds the window of
    `window_beats` beats (default 32 = 8 bars) whose chroma content best
    matches another non-overlapping window elsewhere in the song --
    repeated harmonic content is what makes a chorus a chorus. Always
    cross-check with tools/reference_inspect.py --start --end by ear.
    """
    if len(beat_times) < window_beats * 2:
        return {"found": False, "reason": "not enough beats detected for a reliable window search"}

    chroma = librosa.feature.chroma_cqt(y=y, sr=sr)
    chroma_times = librosa.frames_to_time(np.arange(chroma.shape[1]), sr=sr)

    def chroma_slice(t0: float, t1: float) -> "np.ndarray":
        idx = np.where((chroma_times >= t0) & (chroma_times < t1))[0]
        if len(idx) == 0:
            return np.zeros((12, 1))
        return chroma[:, idx]

    def similarity(a: "np.ndarray", b: "np.ndarray") -> float:
        n = min(a.shape[1], b.shape[1])
        if n < 4:
            return 0.0
        a2, b2 = a[:, :n], b[:, :n]
        num = np.sum(a2 * b2)
        den = (np.linalg.norm(a2) * np.linalg.norm(b2)) + 1e-9
        return float(num / den)

    n_windows = len(beat_times) - window_beats
    step = max(1, window_beats // 4)  # quarter-window hop for speed
    starts = list(range(0, n_windows, step))
    window_chromas = {i: chroma_slice(beat_times[i], beat_times[i + window_beats]) for i in starts}

    best = None
    for i in starts:
        for j in starts:
            if abs(i - j) < window_beats:  # skip overlapping/adjacent windows
                continue
            s = similarity(window_chromas[i], window_chromas[j])
            if best is None or s > best[0]:
                best = (s, i, j)

    if best is None:
        return {"found": False, "reason": "no non-overlapping window pair found"}

    score, i, j = best
    start_beat_idx = min(i, j)  # report the earlier occurrence as "the chorus" to teach from
    return {
        "found": True,
        "similarity_score": round(score, 4),
        "confidence": "medium" if score > 0.85 else ("low" if score > 0.7 else "very low -- manually verify"),
        "start_seconds": round(beat_times[start_beat_idx], 3),
        "end_seconds": round(beat_times[min(start_beat_idx + window_beats, len(beat_times) - 1)], 3),
        "window_beats": window_beats,
        "note": "heuristic repeated-section detection, not verified -- confirm by ear with tools/reference_inspect.py",
    }


# ---- melody / bass candidates (full-mix pyin -- honestly low confidence) ------

def pitch_track_pyin(y: "np.ndarray", sr: int, fmin: float, fmax: float, t0: float, t1: float, source_label: str) -> list[dict]:
    i0, i1 = int(t0 * sr), int(t1 * sr)
    segment = y[i0:i1]
    if len(segment) < sr * 0.1:
        return []
    f0, voiced_flag, voiced_prob = librosa.pyin(segment, fmin=fmin, fmax=fmax, sr=sr)
    hop_length = 512  # librosa.pyin default
    frame_times = librosa.frames_to_time(np.arange(len(f0)), sr=sr, hop_length=hop_length)

    notes = []
    cur = None  # (start_frame, midi_note, confidences)
    for i, (f, voiced, prob) in enumerate(zip(f0, voiced_flag, voiced_prob)):
        midi = librosa.hz_to_midi(f) if (voiced and f and f > 0) else None
        rounded = round(midi) if midi is not None else None
        if rounded is not None and cur is not None and rounded == cur["note"]:
            cur["confidences"].append(float(prob))
            cur["end_i"] = i
        else:
            if cur is not None:
                notes.append(cur)
            cur = {"note": rounded, "start_i": i, "end_i": i, "confidences": [float(prob)]} if rounded is not None else None
    if cur is not None:
        notes.append(cur)

    out = []
    min_frames = 2  # reject single-frame blips (breaths/attacks/noise)
    for n in notes:
        n_frames = n["end_i"] - n["start_i"] + 1
        if n_frames < min_frames:
            continue
        start_s = t0 + float(frame_times[n["start_i"]])
        end_s = t0 + float(frame_times[min(n["end_i"] + 1, len(frame_times) - 1)])
        out.append(
            {
                "start_seconds": round(start_s, 3),
                "end_seconds": round(end_s, 3),
                "duration_seconds": round(end_s - start_s, 3),
                "nearest_midi_note": int(n["note"]),
                "pitch_name": librosa.midi_to_note(n["note"]),
                "confidence": round(float(np.mean(n["confidences"])), 3),
                "source_stem": source_label,
            }
        )
    return out


# ---- harmony candidate (chroma template matching, per beat) -------------------

CHORD_TEMPLATES = {
    "maj": [1, 0, 0, 0, 1, 0, 0, 1, 0, 0, 0, 0],
    "min": [1, 0, 0, 1, 0, 0, 0, 1, 0, 0, 0, 0],
}
PITCH_CLASSES = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]


def harmony_candidates(y: "np.ndarray", sr: int, beat_times: list[float], t0: float, t1: float) -> list[dict]:
    chroma = librosa.feature.chroma_cqt(y=y, sr=sr)
    chroma_times = librosa.frames_to_time(np.arange(chroma.shape[1]), sr=sr)
    beats_in_range = [b for b in beat_times if t0 <= b < t1]
    out = []
    for i in range(len(beats_in_range) - 1):
        b0, b1 = beats_in_range[i], beats_in_range[i + 1]
        idx = np.where((chroma_times >= b0) & (chroma_times < b1))[0]
        if len(idx) == 0:
            continue
        vec = np.mean(chroma[:, idx], axis=1)
        vec = vec / (np.linalg.norm(vec) + 1e-9)
        best = None
        for root in range(12):
            for quality, template in CHORD_TEMPLATES.items():
                rotated = np.roll(template, root)
                rotated = np.array(rotated, dtype=float)
                rotated = rotated / (np.linalg.norm(rotated) + 1e-9)
                score = float(np.dot(vec, rotated))
                if best is None or score > best[0]:
                    best = (score, root, quality)
        score, root, quality = best
        out.append(
            {
                "start_seconds": round(b0, 3),
                "end_seconds": round(b1, 3),
                "root_pitch_class": PITCH_CLASSES[root],
                "quality": quality,
                "confidence": round(max(0.0, min(1.0, (score - 0.5) * 2)), 3),  # rough rescale, template match score is not a calibrated probability
                "source_stem": "full_mix",
            }
        )
    return out


def find_stems(audio_path: Path) -> dict[str, Path] | None:
    """Locate Demucs output for `audio_path` under OUT_DIR/stems/<model>/<track>/.
    Returns None (triggering the full-mix fallback) if not present -- stem
    separation is an offline, opt-in step (tools/analyze_reference.py's
    module docstring / README), never required for normal app startup.
    """
    stems_root = OUT_DIR / "stems"
    if not stems_root.exists():
        return None
    track_name = audio_path.stem
    for model_dir in stems_root.iterdir():
        candidate = model_dir / track_name
        if candidate.is_dir():
            found = {}
            for stem in ("vocals", "bass", "other", "drums"):
                p = candidate / f"{stem}.wav"
                if p.exists():
                    found[stem] = p
            if found:
                return found
    return None


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--audio", default="instant_crush.mp3")
    parser.add_argument("--chorus-start", type=float, default=None, help="seconds -- override auto-detected chorus start")
    parser.add_argument("--chorus-end", type=float, default=None, help="seconds -- override auto-detected chorus end")
    parser.add_argument("--no-stems", action="store_true", help="force full-mix analysis even if separated stems are present")
    args = parser.parse_args()

    if not LIBROSA_AVAILABLE:
        print("librosa is not installed. Run: pip install librosa")
        return 1
    audio_path = Path(args.audio)
    if not audio_path.exists():
        print(f"Reference audio not found: {audio_path}")
        return 1

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    (OUT_DIR / "candidates").mkdir(exist_ok=True)
    (OUT_DIR / "reports").mkdir(exist_ok=True)

    log(f"Loading {audio_path} at {SR}Hz mono...")
    y, sr = librosa.load(str(audio_path), sr=SR, mono=True)
    duration_s = len(y) / sr
    log(f"Loaded {duration_s:.2f}s")

    log("Measuring tempo/beat grid...")
    tempo_info = measure_tempo_and_beats(y, sr)
    log(f"  beat_track={tempo_info['beat_track_bpm']:.2f} tempogram={tempo_info['tempogram_bpm']:.2f} agree={tempo_info['methods_agree_within_1bpm']}")

    log("Detecting onsets...")
    onsets = measure_onsets(y, sr)
    log(f"  {len(onsets)} onsets detected")

    if args.chorus_start is not None and args.chorus_end is not None:
        section_info = {
            "found": True,
            "start_seconds": args.chorus_start,
            "end_seconds": args.chorus_end,
            "confidence": "user_specified",
            "note": "manually specified via --chorus-start/--chorus-end",
        }
    else:
        log("Searching for a repeated (chorus-like) section via chroma self-similarity...")
        section_info = find_repeated_section_candidate(y, sr, tempo_info["beat_times_seconds"])
        if section_info.get("found"):
            log(f"  candidate: {section_info['start_seconds']:.2f}s - {section_info['end_seconds']:.2f}s (score={section_info['similarity_score']}, confidence={section_info['confidence']})")
        else:
            log(f"  no candidate found: {section_info.get('reason')}")

    stems = None if args.no_stems else find_stems(audio_path)
    if stems:
        log(f"Found separated stems: {sorted(stems.keys())} -- using them for melody/bass/harmony instead of the full mix")
        stem_note = f"Demucs (htdemucs) stem separation WAS used -- melody from vocals.wav, bass from bass.wav, harmony from other.wav. Stems themselves are gitignored (never committed), only this JSON/text metadata is tracked. Available stems: {sorted(stems.keys())}."
    else:
        log("No separated stems found -- falling back to full-mix analysis (honestly low confidence, see module docstring)")
        stem_note = "not used for this run -- all pitch/harmony analysis below runs on the full mix and is honestly low-confidence as a result (see module docstring). Run tools/analyze_reference.py again after 'python -m demucs -o songs/instant_crush/reference_analysis/stems instant_crush.mp3' to use isolated stems."

    metadata = {
        "source_file": str(audio_path),
        "note": "instant_crush.mp3 itself is NEVER committed -- see .gitignore. This metadata contains no audio.",
        "duration_seconds": round(duration_s, 3),
        "analysis_sample_rate": sr,
        "analyzed_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "demucs_stem_separation": stem_note,
    }
    (OUT_DIR / "metadata.json").write_text(json.dumps(metadata, indent=2), encoding="utf-8")
    (OUT_DIR / "beat_grid.json").write_text(json.dumps(tempo_info, indent=2), encoding="utf-8")
    (OUT_DIR / "onset_map.json").write_text(json.dumps({"onset_times_seconds": onsets}, indent=2), encoding="utf-8")
    (OUT_DIR / "sections.json").write_text(json.dumps({"chorus_candidate": section_info}, indent=2), encoding="utf-8")

    if section_info.get("found"):
        t0, t1 = section_info["start_seconds"], section_info["end_seconds"]

        if stems and "vocals" in stems:
            log(f"Loading vocals stem for melody extraction...")
            y_vocals, _ = librosa.load(str(stems["vocals"]), sr=SR, mono=True)
            melody_source, melody_label = y_vocals, "vocals_stem"
        else:
            melody_source, melody_label = y, "full_mix"
        log(f"Extracting melody candidate ({melody_label} pyin, {t0:.2f}s-{t1:.2f}s)...")
        melody = pitch_track_pyin(melody_source, sr, fmin=130.0, fmax=1046.0, t0=t0, t1=t1, source_label=melody_label)
        log(f"  {len(melody)} melody note candidates ({melody_label})")
        (OUT_DIR / "candidates" / "melody.json").write_text(json.dumps(melody, indent=2), encoding="utf-8")

        if stems and "bass" in stems:
            log(f"Loading bass stem for bass extraction...")
            y_bass, _ = librosa.load(str(stems["bass"]), sr=SR, mono=True)
            bass_source, bass_label = y_bass, "bass_stem"
        else:
            bass_source, bass_label = y, "full_mix"
        log(f"Extracting bass candidate ({bass_label} pyin, low band)...")
        bass = pitch_track_pyin(bass_source, sr, fmin=30.0, fmax=200.0, t0=t0, t1=t1, source_label=bass_label)  # ~Bb0-G3
        log(f"  {len(bass)} bass note candidates ({bass_label})")
        (OUT_DIR / "candidates" / "bass.json").write_text(json.dumps(bass, indent=2), encoding="utf-8")

        if stems and "other" in stems:
            log(f"Loading 'other' stem (synths/guitars/harmonic bed) for harmony extraction...")
            y_other, _ = librosa.load(str(stems["other"]), sr=SR, mono=True)
            harmony_source, harmony_label = y_other, "other_stem"
        else:
            harmony_source, harmony_label = y, "full_mix"
        log(f"Extracting harmony candidate ({harmony_label}, chroma template matching)...")
        # beat_times were measured on the full mix (reliable); harmony content is read from harmony_source
        harmony = harmony_candidates(harmony_source, sr, tempo_info["beat_times_seconds"], t0, t1)
        for h in harmony:
            h["source_stem"] = harmony_label
        log(f"  {len(harmony)} harmony candidates ({harmony_label})")
        (OUT_DIR / "candidates" / "harmony.json").write_text(json.dumps(harmony, indent=2), encoding="utf-8")

        mean_melody_conf = round(float(np.mean([m["confidence"] for m in melody])), 3) if melody else None
        mean_bass_conf = round(float(np.mean([b["confidence"] for b in bass])), 3) if bass else None
        mean_harmony_conf = round(float(np.mean([h["confidence"] for h in harmony])), 3) if harmony else None
    else:
        melody, bass, harmony = [], [], []
        mean_melody_conf = mean_bass_conf = mean_harmony_conf = None

    report_lines = [
        "INSTANT CRUSH -- REFERENCE ANALYSIS REPORT",
        "=" * 60,
        f"Source: {audio_path} (never committed)",
        f"Duration: {duration_s:.2f}s ({duration_s / 60:.2f} min)",
        "",
        "TEMPO",
        f"  beat_track method: {tempo_info['beat_track_bpm']:.2f} BPM",
        f"  tempogram method:  {tempo_info['tempogram_bpm']:.2f} BPM",
        f"  methods agree (within 1 BPM): {tempo_info['methods_agree_within_1bpm']}",
        f"  confidence: {tempo_info['confidence']}",
        f"  beats detected: {tempo_info['n_beats']}",
        "",
        "ONSETS",
        f"  {len(onsets)} onsets detected across the full track",
        "",
        "CHORUS SECTION CANDIDATE",
        json.dumps(section_info, indent=2),
        "",
        "STEM SEPARATION",
        f"  {stem_note}",
        "",
        f"MELODY CANDIDATE ({melody_label if section_info.get('found') else 'n/a'} pyin)",
        f"  {len(melody)} note candidates, mean confidence {mean_melody_conf}",
        "",
        f"BASS CANDIDATE ({bass_label if section_info.get('found') else 'n/a'} pyin, low band)",
        f"  {len(bass)} note candidates, mean confidence {mean_bass_conf}",
        "",
        f"HARMONY CANDIDATE ({harmony_label if section_info.get('found') else 'n/a'}, chroma template matching)",
        f"  {len(harmony)} chord candidates, mean confidence {mean_harmony_conf}",
        "",
        "LIMITATIONS",
        "  - Melody/bass/harmony analysis quality depends entirely on whether",
        "    isolated stems were used (see STEM SEPARATION above) -- full-mix",
        "    pitch tracking is contaminated by every other instrument playing",
        "    simultaneously and should be treated as a rough starting point",
        "    at best, not a reliable transcription.",
        "  - Even with stems, pyin/chroma-template extraction is heuristic --",
        "    still not a claim of ground truth. Compare confidence numbers",
        "    above against the pre-stem-separation baseline to judge how",
        "    much stems actually helped for this track.",
        "  - Chorus section detection is a repeated-chroma heuristic, not a",
        "    verified structural analysis -- confirm by ear.",
    ]
    (OUT_DIR / "reports" / "analysis_report.txt").write_text("\n".join(report_lines), encoding="utf-8")
    log(f"Wrote analysis to {OUT_DIR}/")
    return 0


if __name__ == "__main__":
    sys.exit(main())

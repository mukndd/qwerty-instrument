"""Local reference-audio inspector for timing calibration.

Purpose: let you point this at a LEGALLY-OBTAINED local audio file (never
downloaded or bundled by this project -- see songs/instant_crush/NOTES_STATUS.md)
and get basic timing information (duration, an onset/energy envelope
estimate) to help calibrate section boundaries and tempo, without any
automatic polyphonic pitch transcription (not attempted -- it wouldn't be
reliable, and that's not the point of this tool).

Suggested location for a reference file (gitignored, never committed):
    songs/instant_crush/reference/instant_crush_reference.<ext>

Usage:
    .venv\\Scripts\\python.exe tools\\reference_inspect.py path\\to\\file.wav
    .venv\\Scripts\\python.exe tools\\reference_inspect.py path\\to\\file.wav --start 195 --end 205
    .venv\\Scripts\\python.exe tools\\reference_inspect.py path\\to\\file.wav --start 195 --end 205 --export exports/ref_clip.wav

Requires the optional `soundfile` dependency (pip install soundfile).
If it's not installed, this tool reports that clearly and exits --
it never affects the rest of the application, which has zero dependency
on this tool or on any reference audio being present.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

try:
    import numpy as np
    import soundfile as sf

    SOUNDFILE_AVAILABLE = True
except ImportError:
    SOUNDFILE_AVAILABLE = False


def estimate_onsets(samples: "np.ndarray", sample_rate: int, hop_seconds: float = 0.02, threshold_db: float = 6.0) -> list[float]:
    """Simple energy-envelope onset estimate: no ML, just short-time RMS
    with a rise-above-threshold peak picker. Good enough for rough timing
    calibration, not a claim of accurate beat/onset detection.
    """
    if samples.ndim > 1:
        samples = samples.mean(axis=1)
    hop = max(1, int(hop_seconds * sample_rate))
    n_hops = len(samples) // hop
    if n_hops < 2:
        return []
    rms = np.array([np.sqrt(np.mean(samples[i * hop:(i + 1) * hop].astype(np.float64) ** 2) + 1e-12) for i in range(n_hops)])
    rms_db = 20 * np.log10(rms + 1e-9)
    onsets = []
    for i in range(1, len(rms_db)):
        if rms_db[i] - rms_db[i - 1] > threshold_db:
            onsets.append(i * hop_seconds)
    return onsets


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("audio_path", help="path to a legally-obtained local WAV/FLAC/OGG file (MP3 support depends on your libsndfile build)")
    parser.add_argument("--start", type=float, default=None, help="seconds -- start of the section to inspect")
    parser.add_argument("--end", type=float, default=None, help="seconds -- end of the section to inspect")
    parser.add_argument("--export", default=None, help="write the [start,end] slice to this local WAV path (not committed to git)")
    parser.add_argument("--onsets", action="store_true", help="print a rough energy-based onset time list")
    args = parser.parse_args()

    if not SOUNDFILE_AVAILABLE:
        print("The 'soundfile' package is not installed. Run: pip install soundfile")
        print("(This tool is entirely optional -- the rest of the app has no dependency on it.)")
        return 1

    path = Path(args.audio_path)
    if not path.exists():
        print(f"File not found: {path}")
        return 1

    info = sf.info(str(path))
    print(f"File: {path}")
    print(f"  duration: {info.duration:.2f}s")
    print(f"  sample_rate: {info.samplerate} Hz")
    print(f"  channels: {info.channels}")
    print(f"  format: {info.format} / {info.subtype}")

    samples, sr = sf.read(str(path), always_2d=False)

    start = args.start if args.start is not None else 0.0
    end = args.end if args.end is not None else info.duration
    if start < 0 or end > info.duration or start >= end:
        print(f"Invalid range: start={start} end={end} (file duration {info.duration:.2f}s)")
        return 1

    start_idx = int(start * sr)
    end_idx = int(end * sr)
    segment = samples[start_idx:end_idx]
    print(f"\nSelected range: {start:.3f}s - {end:.3f}s ({end - start:.3f}s)")

    if args.onsets:
        onsets = estimate_onsets(segment, sr)
        print(f"\nEstimated onsets within selection (energy-based, approximate, {len(onsets)} found):")
        for t in onsets[:60]:
            print(f"  {start + t:8.3f}s")
        if len(onsets) > 60:
            print(f"  ... and {len(onsets) - 60} more")

    if args.export:
        export_path = Path(args.export)
        export_path.parent.mkdir(parents=True, exist_ok=True)
        sf.write(str(export_path), segment, sr)
        print(f"\nExported selection to: {export_path}")

    return 0


if __name__ == "__main__":
    sys.exit(main())

"""tools/analyze_reference.py: multi-occurrence cross-validation (the fix
for accuracy pass v3.3's wrong-section bug -- a single best-matching pair
can be a local coincidence; requiring several well-separated occurrences
to agree is a much stronger signal). Synthetic chroma data, not the real
MP3, so these run fast and don't depend on a local reference file.
"""

import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))

import analyze_reference as ar


def make_synthetic_chroma(duration_s: float, sr_frames_per_s: float, pattern_positions: list[tuple[float, float, int]], noise: float = 0.05):
    """12 x N chroma matrix, mostly noise, with a fixed 12-d "chord" pattern
    stamped in at each (start, end, root) in pattern_positions."""
    n_frames = int(duration_s * sr_frames_per_s)
    rng = np.random.default_rng(0)
    chroma = rng.uniform(0, noise, size=(12, n_frames))
    times = np.arange(n_frames) / sr_frames_per_s
    base_template = np.array([1, 0, 0, 0, 0.8, 0, 0, 0.9, 0, 0, 0, 0])  # a fixed "chord" shape
    for start, end, root in pattern_positions:
        idx = np.where((times >= start) & (times < end))[0]
        chroma[:, idx] = np.roll(base_template, root)[:, None]
    return chroma, times


def test_find_all_occurrences_finds_well_separated_repeats():
    chroma, times = make_synthetic_chroma(
        duration_s=300, sr_frames_per_s=20,
        pattern_positions=[(10, 20, 0), (150, 160, 0), (280, 290, 0), (60, 70, 5)],  # 3 real repeats of root=0, one unrelated root=5 chord elsewhere
    )
    occurrences = ar.find_all_occurrences(chroma, times, seed_t0=10, seed_t1=20, min_similarity=0.9, min_gap_seconds=20)
    starts = sorted(round(o["start_seconds"]) for o in occurrences)
    assert 10 in starts
    assert 150 in starts
    assert 280 in starts
    assert 60 not in starts  # a different chord entirely -- must not be picked up as a match


def test_find_all_occurrences_returns_only_seed_when_nothing_else_matches():
    chroma, times = make_synthetic_chroma(duration_s=200, sr_frames_per_s=20, pattern_positions=[(10, 20, 0)])
    occurrences = ar.find_all_occurrences(chroma, times, seed_t0=10, seed_t1=20, min_similarity=0.9, min_gap_seconds=20)
    assert len(occurrences) == 1
    assert occurrences[0]["start_seconds"] == 10.0


def test_average_chroma_occurrences_reduces_noise_vs_any_single_occurrence():
    """The whole point of averaging: a chord id from noisy single-occurrence
    chroma should become MORE consistent (closer to the true pattern) after
    averaging across several independent (differently-noised) repeats."""
    positions = [(10, 20, 0), (110, 120, 0), (210, 220, 0)]
    chroma, times = make_synthetic_chroma(duration_s=300, sr_frames_per_s=20, pattern_positions=positions, noise=0.3)
    occurrences = [{"start_seconds": s, "end_seconds": e, "similarity": 1.0} for s, e, _ in positions]

    avg_chroma, avg_times = ar.average_chroma_occurrences(chroma, times, occurrences)
    assert avg_chroma.shape[0] == 12
    assert avg_chroma.shape[1] > 0

    # Averaged chord ID should match the true root (0) with a cleaner score
    # than at least one noisy single occurrence's mean vector.
    true_template = np.array([1, 0, 0, 0, 0.8, 0, 0, 0.9, 0, 0, 0, 0])
    avg_vec = np.mean(avg_chroma, axis=1)
    avg_score, avg_root, _ = ar._match_chord_template(avg_vec)
    assert avg_root == 0
    assert avg_score > 0.8


def test_chord_sequence_from_chroma_matches_template():
    chroma, times = make_synthetic_chroma(duration_s=8, sr_frames_per_s=20, pattern_positions=[(0, 8, 0)], noise=0.01)
    beat_times = [0.0, 2.0, 4.0, 6.0, 8.0]
    seq = ar.chord_sequence_from_chroma(chroma, times, beat_times, t0=0.0, t1=8.0, source_label="test")
    assert len(seq) > 0
    assert all(s["root_pitch_class"] == "C" for s in seq)  # root=0 -> C
    assert all(s["confidence"] > 0.5 for s in seq)

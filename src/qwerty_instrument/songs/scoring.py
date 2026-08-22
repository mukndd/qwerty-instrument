"""Timing/pitch accuracy scoring for Guided and Real practice modes.

Deliberately simple, four-bucket judgement (Perfect/Good/Early-or-Late/Miss)
per spec section 18 -- millisecond error is still recorded for advanced
diagnostics, but the primary UI surface stays jargon-free.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

from .model import NoteEvent


class Judgement(Enum):
    PERFECT = "perfect"
    GOOD = "good"
    EARLY = "early"
    LATE = "late"
    MISS = "miss"
    WRONG_NOTE = "wrong_note"


@dataclass
class TimingWindows:
    perfect_ms: float = 40.0
    good_ms: float = 90.0
    late_ms: float = 150.0  # beyond this (in either direction) = miss


@dataclass
class NoteResult:
    expected: NoteEvent
    judgement: Judgement
    timing_error_ms: float
    played_note: int | None


@dataclass
class ScoreState:
    total_expected: int = 0
    resolved: int = 0
    perfect: int = 0
    good: int = 0
    early: int = 0
    late: int = 0
    miss: int = 0
    wrong_note: int = 0
    combo: int = 0
    max_combo: int = 0
    timing_errors_ms: list[float] = field(default_factory=list)

    @property
    def correct_hits(self) -> int:
        return self.perfect + self.good + self.early + self.late

    def timing_accuracy_percent(self) -> float:
        if self.total_expected == 0:
            return 100.0
        return 100.0 * (self.perfect + self.good) / self.total_expected

    def pitch_accuracy_percent(self) -> float:
        judged_pitch = self.correct_hits + self.wrong_note
        if judged_pitch == 0:
            return 100.0
        return 100.0 * self.correct_hits / judged_pitch

    def completion_percent(self) -> float:
        if self.total_expected == 0:
            return 0.0
        return 100.0 * self.resolved / self.total_expected

    def mean_timing_error_ms(self) -> float:
        if not self.timing_errors_ms:
            return 0.0
        return sum(self.timing_errors_ms) / len(self.timing_errors_ms)

    def to_dict(self) -> dict:
        return {
            "total_expected": self.total_expected,
            "resolved": self.resolved,
            "perfect": self.perfect,
            "good": self.good,
            "early": self.early,
            "late": self.late,
            "miss": self.miss,
            "wrong_note": self.wrong_note,
            "max_combo": self.max_combo,
            "timing_accuracy_percent": round(self.timing_accuracy_percent(), 1),
            "pitch_accuracy_percent": round(self.pitch_accuracy_percent(), 1),
            "completion_percent": round(self.completion_percent(), 1),
            "mean_timing_error_ms": round(self.mean_timing_error_ms(), 1),
        }


class ScoringEngine:
    def __init__(self, windows: TimingWindows | None = None, total_expected: int = 0):
        self.windows = windows or TimingWindows()
        self.state = ScoreState(total_expected=total_expected)

    def judge_hit(self, expected: NoteEvent, expected_time_s: float, played_note: int, played_time_s: float) -> NoteResult:
        error_ms = (played_time_s - expected_time_s) * 1000.0
        abs_err = abs(error_ms)
        s = self.state

        if played_note != expected.note:
            judgement = Judgement.WRONG_NOTE
            s.wrong_note += 1
            s.combo = 0
        else:
            if abs_err <= self.windows.perfect_ms:
                judgement = Judgement.PERFECT
                s.perfect += 1
            elif abs_err <= self.windows.good_ms:
                judgement = Judgement.GOOD
                s.good += 1
            elif abs_err <= self.windows.late_ms:
                if error_ms < 0:
                    judgement = Judgement.EARLY
                    s.early += 1
                else:
                    judgement = Judgement.LATE
                    s.late += 1
            else:
                judgement = Judgement.MISS
                s.miss += 1

            if judgement == Judgement.MISS:
                s.combo = 0
            else:
                s.combo += 1
                s.max_combo = max(s.max_combo, s.combo)

        s.resolved += 1
        s.timing_errors_ms.append(error_ms)
        return NoteResult(expected=expected, judgement=judgement, timing_error_ms=error_ms, played_note=played_note)

    def judge_miss(self, expected: NoteEvent) -> NoteResult:
        s = self.state
        s.miss += 1
        s.resolved += 1
        s.combo = 0
        return NoteResult(expected=expected, judgement=Judgement.MISS, timing_error_ms=float("inf"), played_note=None)

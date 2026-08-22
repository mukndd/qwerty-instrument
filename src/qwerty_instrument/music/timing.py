"""Tempo mapping, bar/beat <-> seconds conversion, and practice-speed scaling.

All song timing is stored in beats (tempo-independent). Converting to
wall-clock seconds happens here, and only here, so that practice-speed
scaling (50%-100%) is a single multiplier applied in one place rather than
scattered through the playback/scoring code.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(slots=True)
class TempoEvent:
    beat: float  # position in the song, in beats, where this tempo begins
    bpm: float


@dataclass
class TempoMap:
    time_signature: tuple[int, int] = (4, 4)
    events: list[TempoEvent] = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        if self.events is None:
            self.events = [TempoEvent(beat=0.0, bpm=120.0)]
        self.events.sort(key=lambda e: e.beat)

    def bpm_at_beat(self, beat: float) -> float:
        bpm = self.events[0].bpm
        for ev in self.events:
            if ev.beat > beat:
                break
            bpm = ev.bpm
        return bpm

    def beats_to_seconds(self, beat: float) -> float:
        """Integrate seconds-per-beat across tempo changes up to `beat`."""
        seconds = 0.0
        prev_beat = 0.0
        prev_bpm = self.events[0].bpm
        for ev in self.events[1:]:
            if ev.beat >= beat:
                break
            seconds += (ev.beat - prev_beat) * (60.0 / prev_bpm)
            prev_beat = ev.beat
            prev_bpm = ev.bpm
        seconds += (beat - prev_beat) * (60.0 / prev_bpm)
        return seconds

    def seconds_to_beats(self, seconds: float) -> float:
        """Inverse of beats_to_seconds: wall-clock seconds -> beat position."""
        elapsed = 0.0
        prev_beat = 0.0
        prev_bpm = self.events[0].bpm
        for ev in self.events[1:]:
            seg_seconds = (ev.beat - prev_beat) * (60.0 / prev_bpm)
            if elapsed + seg_seconds >= seconds:
                break
            elapsed += seg_seconds
            prev_beat = ev.beat
            prev_bpm = ev.bpm
        remaining_seconds = seconds - elapsed
        return prev_beat + remaining_seconds * (prev_bpm / 60.0)

    def beat_to_bar_beat(self, beat: float) -> tuple[int, float]:
        beats_per_bar = self.time_signature[0]
        bar = int(beat // beats_per_bar) + 1
        beat_in_bar = (beat % beats_per_bar) + 1
        return bar, beat_in_bar


@dataclass
class PracticeClock:
    """Wraps a TempoMap with a speed multiplier for practice playback.

    speed=1.0 is full tempo. speed=0.5 is half speed. This scales the
    wall-clock seconds returned for a given beat, without touching the
    underlying song data (pitch is never affected, only rate).
    """

    tempo_map: TempoMap
    speed: float = 1.0

    def beats_to_seconds(self, beat: float) -> float:
        return self.tempo_map.beats_to_seconds(beat) / max(0.05, self.speed)

    def seconds_to_beats(self, seconds: float) -> float:
        return self.tempo_map.seconds_to_beats(seconds * max(0.05, self.speed))

    def set_speed_percent(self, percent: float) -> None:
        self.speed = max(0.1, min(2.0, percent / 100.0))

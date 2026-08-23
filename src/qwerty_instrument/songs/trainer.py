"""Practice/training engine: Real, Guided, and Assist modes over a Song.

Not audio-thread code -- this runs on a control-rate `tick()` driven by
the UI (a ~30-60Hz QTimer) or a plain loop in headless/test contexts. It
only ever talks to the audio engine through MusicEvents via `event_sink`,
same as the QWERTY/F75 input layers, keeping the audio engine ignorant of
practice-mode concepts entirely.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Callable

from ..music.events import EventType, MusicEvent, Source
from ..music.mapping import KeyboardMapping
from ..music.timing import PracticeClock
from .model import ChordEvent, NoteEvent, Section, Song
from .scoring import Judgement, NoteResult, ScoringEngine, TimingWindows

logger = logging.getLogger("qwerty_instrument.songs.trainer")

MusicEventSink = Callable[[MusicEvent], None]


class PracticeMode(Enum):
    REAL = "real"
    GUIDED = "guided"
    ASSIST = "assist"
    AUTOPLAY = "autoplay"


class AssistStyle(Enum):
    SINGLE_BUTTON = "single_button"
    MULTI_LANE = "multi_lane"


DEFAULT_ASSIST_KEYS = ["J", "K", "L", "SEMICOLON"]
DEFAULT_ASSIST_LANES = ["melody", "chords", "bass", "lead_guitar"]

METRONOME_CLICK_NOTE = 84  # fixed pitch used for the click "blip" (short + quiet, not tonal)


@dataclass
class UpcomingNote:
    event: NoteEvent | ChordEvent
    beat: float
    key_hint: str | None  # suggested physical key, from KeyboardMapping.find_key_for_note


class SongTrainer:
    def __init__(
        self,
        song: Song,
        mapping: KeyboardMapping,
        event_sink: MusicEventSink,
        timing_windows: TimingWindows | None = None,
    ):
        self.song = song
        self.mapping = mapping
        self.event_sink = event_sink
        self.clock = PracticeClock(song.tempo_map)
        self.mode = PracticeMode.REAL
        self.assist_style = AssistStyle.SINGLE_BUTTON
        self.assist_strict = True
        self.assist_keys = list(DEFAULT_ASSIST_KEYS)

        self.current_section: Section | None = None
        self.loop_enabled = False
        self.metronome_enabled = False
        self.metronome_volume = 0.5

        self.playing = False
        self._start_perf_time: float | None = None
        self._start_beat = 0.0
        self._last_metronome_beat_int = -1
        self._active_layer = "melody"

        self.timing_windows = timing_windows or TimingWindows()
        self.scoring = ScoringEngine(self.timing_windows)
        self._pending_events: list[NoteEvent | ChordEvent] = []  # not yet judged, sorted by beat
        self._active_song_voices: dict[int, str] = {}  # note -> key_id used for the emitted event

        # Autoplay (spec: song data -> scheduler -> MusicEvent -> existing
        # AudioEngine/instrument/FX -- same path QWERTY playing uses).
        self.autoplay_layers: list[str] = ["chords", "bass"]
        self._autoplay_queue: list[NoteEvent | ChordEvent] = []
        self._autoplay_active: list[tuple[NoteEvent | ChordEvent, float, list[int], list[str]]] = []
        self.autoplay_sounding_notes: set[int] = set()  # for UI key-highlighting via find_key_for_note()
        self.autoplay_sounding_by_layer: dict[str, set[int]] = {}  # for layer-differentiated UI highlighting

        # Which instrument each song layer sounds through -- lets chords,
        # bass, and melody play through different backends simultaneously
        # (AudioEngine routes on MusicEvent.metadata["target_instrument"];
        # NOTE_OFF carries the same value, so ownership stays correct --
        # see docs/ARCHITECTURE.md).
        self.layer_routes: dict[str, str] = {
            "chords": "synth_lead",
            "bass": "bass_synth",
            "melody": "synth_lead",
            "lead_guitar": "guitar_lead",
        }

        self.on_note_result: Callable[[NoteResult], None] | None = None
        self.on_section_looped: Callable[[], None] | None = None

    # ---- setup ----------------------------------------------------------------

    def load_section(self, section_id: str, layer: str = "melody") -> bool:
        section = self.song.section_by_id(section_id)
        if section is None:
            return False
        self.current_section = section
        self._active_layer = layer
        self.stop()
        self.scoring = ScoringEngine(self.timing_windows, total_expected=len([e for e in section.notes if e.layer == layer]))
        return True

    def set_speed_percent(self, percent: float) -> None:
        self.clock.set_speed_percent(percent)

    def set_autoplay_layers(self, layers: list[str]) -> None:
        self.autoplay_layers = list(layers)

    def set_layer_routes(self, routes: dict[str, str]) -> None:
        self.layer_routes.update(routes)

    def set_mode(self, mode: PracticeMode) -> None:
        if self.mode == PracticeMode.AUTOPLAY and mode != PracticeMode.AUTOPLAY:
            self._stop_autoplay_notes()  # changing mode must stop outstanding autoplay voices
        self.mode = mode

    # ---- transport --------------------------------------------------------------

    def start(self, from_beat: float | None = None) -> None:
        if self.current_section is None:
            return
        self._stop_autoplay_notes()  # defensive: never leave a previous run's voices dangling
        self._start_beat = self.current_section.start_beat if from_beat is None else from_beat
        self._start_perf_time = time.perf_counter()
        self._last_metronome_beat_int = int(self._start_beat) - 1

        if self.mode == PracticeMode.AUTOPLAY:
            self._autoplay_queue = sorted(
                (e for e in self.current_section.notes if e.layer in self.autoplay_layers and e.beat >= self._start_beat),
                key=lambda e: e.beat,
            )
        else:
            self._pending_events = sorted(
                (e for e in self.current_section.notes if e.layer == self._active_layer and e.beat >= self._start_beat),
                key=lambda e: e.beat,
            )
        self.playing = True
        self.event_sink(MusicEvent(type=EventType.TRANSPORT_START, timestamp_ns=time.perf_counter_ns(), source=Source.SONG_PLAYBACK))

    def stop(self) -> None:
        if self.playing:
            self.event_sink(MusicEvent(type=EventType.TRANSPORT_STOP, timestamp_ns=time.perf_counter_ns(), source=Source.SONG_PLAYBACK))
        self._stop_autoplay_notes()
        self.playing = False
        self._start_perf_time = None

    def current_beat(self) -> float:
        """Wall-clock elapsed time since start(), converted back to a beat
        position via the tempo map (correct even across tempo changes,
        and scaled by practice speed).
        """
        if self._start_perf_time is None:
            return self._start_beat
        elapsed = time.perf_counter() - self._start_perf_time
        target_song_seconds = self.clock.tempo_map.beats_to_seconds(self._start_beat) + elapsed * self.clock.speed
        return self.clock.tempo_map.seconds_to_beats(target_song_seconds)

    # ---- control-rate tick, drives metronome + auto-restart + assist timeout ----

    def tick(self) -> None:
        if not self.playing or self.current_section is None:
            return
        beat = self.current_beat()

        if self.metronome_enabled:
            beat_int = int(beat)
            if beat_int > self._last_metronome_beat_int:
                self._last_metronome_beat_int = beat_int
                self._emit_metronome_click()

        if self.mode == PracticeMode.AUTOPLAY:
            self._tick_autoplay(beat)
            if beat >= self.current_section.end_beat:
                if self.loop_enabled:
                    if self.on_section_looped:
                        self.on_section_looped()
                    self.start(from_beat=self.current_section.start_beat)  # start() clears/rebuilds cleanly, no stuck notes
                else:
                    self.stop()
            return

        if beat >= self.current_section.end_beat:
            if self.loop_enabled:
                if self.on_section_looped:
                    self.on_section_looped()
                self.start(from_beat=self.current_section.start_beat)
            else:
                self.stop()
            return

        if self.mode != PracticeMode.ASSIST:
            self._check_for_misses(beat)

    # ---- Autoplay: song data -> scheduler -> MusicEvent -> existing engine ----

    def _tick_autoplay(self, beat: float) -> None:
        still_active = []
        for entry in self._autoplay_active:
            event, end_beat, notes, key_ids = entry
            if beat >= end_beat:
                self._emit_autoplay_off(event.layer, notes, key_ids)
            else:
                still_active.append(entry)
        self._autoplay_active = still_active

        while self._autoplay_queue and self._autoplay_queue[0].beat <= beat:
            event = self._autoplay_queue.pop(0)
            notes = list(event.notes) if isinstance(event, ChordEvent) else [event.note]
            velocity = event.velocity if isinstance(event, NoteEvent) else 0.75
            target_instrument = self.layer_routes.get(event.layer)
            ts = time.perf_counter_ns()
            key_ids = [f"autoplay_{event.layer}_{event.beat}_{n}" for n in notes]
            layer_set = self.autoplay_sounding_by_layer.setdefault(event.layer, set())
            for n, key_id in zip(notes, key_ids):
                self.event_sink(
                    MusicEvent(
                        type=EventType.NOTE_ON,
                        note=n,
                        velocity=velocity,
                        timestamp_ns=ts,
                        source=Source.SONG_PLAYBACK,
                        metadata={"key_id": key_id, "target_instrument": target_instrument, "layer": event.layer},
                    )
                )
                self.autoplay_sounding_notes.add(n)
                layer_set.add(n)
            self._autoplay_active.append((event, event.beat + event.duration_beats, notes, key_ids))

    def _emit_autoplay_off(self, layer: str, notes: list[int], key_ids: list[str]) -> None:
        target_instrument = self.layer_routes.get(layer)
        ts = time.perf_counter_ns()
        layer_set = self.autoplay_sounding_by_layer.setdefault(layer, set())
        for n, key_id in zip(notes, key_ids):
            self.event_sink(
                MusicEvent(
                    type=EventType.NOTE_OFF,
                    note=n,
                    timestamp_ns=ts,
                    source=Source.SONG_PLAYBACK,
                    metadata={"key_id": key_id, "target_instrument": target_instrument, "layer": layer},
                )
            )
            self.autoplay_sounding_notes.discard(n)
            layer_set.discard(n)

    def _stop_autoplay_notes(self) -> None:
        for event, end_beat, notes, key_ids in self._autoplay_active:
            self._emit_autoplay_off(event.layer, notes, key_ids)
        self._autoplay_active = []
        self._autoplay_queue = []
        self.autoplay_sounding_notes = set()
        self.autoplay_sounding_by_layer = {}

    def _check_for_misses(self, current_beat: float) -> None:
        late_beats = self.timing_windows.late_ms / 1000.0 * (self.clock.tempo_map.bpm_at_beat(current_beat) / 60.0)
        while self._pending_events and self._pending_events[0].beat < current_beat - late_beats:
            missed = self._pending_events.pop(0)
            if isinstance(missed, NoteEvent):
                result = self.scoring.judge_miss(missed)
                if self.on_note_result:
                    self.on_note_result(result)

    def _emit_metronome_click(self) -> None:
        key_id = f"metronome_{self._last_metronome_beat_int}"
        ts = time.perf_counter_ns()
        self.event_sink(
            MusicEvent(
                type=EventType.NOTE_ON,
                note=METRONOME_CLICK_NOTE,
                velocity=self.metronome_volume,
                timestamp_ns=ts,
                source=Source.SONG_PLAYBACK,
                metadata={"key_id": key_id, "metronome": True},
            )
        )
        self.event_sink(
            MusicEvent(type=EventType.NOTE_OFF, note=METRONOME_CLICK_NOTE, timestamp_ns=ts, source=Source.SONG_PLAYBACK, metadata={"key_id": key_id})
        )

    # ---- Real/Guided: the player's own key presses are judged against `_pending_events` ----

    def judge_played_note(self, note: int, ts_ns: int) -> NoteResult | None:
        """Call from the app's NOTE_ON handler (Real/Guided modes only) to score a played note."""
        if not self.playing or not self._pending_events:
            return None
        played_time_s = ts_ns / 1e9
        now_beat = self.current_beat()
        window_beats = self.timing_windows.late_ms / 1000.0 * (self.clock.tempo_map.bpm_at_beat(now_beat) / 60.0)
        candidates = [e for e in self._pending_events if abs(e.beat - now_beat) <= window_beats and isinstance(e, NoteEvent)]
        if not candidates:
            return None
        target = min(candidates, key=lambda e: abs(e.beat - now_beat))
        expected_time_s = self._beat_to_wall_time_s(target.beat)
        result = self.scoring.judge_hit(target, expected_time_s, note, played_time_s)
        self._pending_events.remove(target)
        if self.on_note_result:
            self.on_note_result(result)
        return result

    def _beat_to_wall_time_s(self, beat: float) -> float:
        assert self._start_perf_time is not None
        return self._start_perf_time + self.clock.beats_to_seconds(beat) - self.clock.beats_to_seconds(self._start_beat)

    def upcoming_notes(self, count: int = 5) -> list[UpcomingNote]:
        out = []
        for e in self._pending_events[:count]:
            key_hint = None
            if isinstance(e, NoteEvent):
                found = self.mapping.find_key_for_note(e.note)
                key_hint = found[0] if found else None
            out.append(UpcomingNote(event=e, beat=e.beat, key_hint=key_hint))
        return out

    # ---- Assist mode: performance keys trigger the next correct note/chord ----

    def handle_performance_key(self, key_id: str, is_down: bool, ts_ns: int) -> bool:
        if self.mode != PracticeMode.ASSIST or not self.playing:
            return False
        if key_id not in self.assist_keys:
            return False
        if not is_down:
            self._release_assist_voice(key_id)
            return True

        if self.assist_style == AssistStyle.MULTI_LANE:
            lane_idx = self.assist_keys.index(key_id)
            lane = DEFAULT_ASSIST_LANES[lane_idx] if lane_idx < len(DEFAULT_ASSIST_LANES) else self._active_layer
            candidates = [e for e in self._pending_events if e.layer == lane]
        else:
            candidates = list(self._pending_events)

        if not candidates:
            return True
        target = candidates[0]

        if self.assist_strict:
            now_beat = self.current_beat()
            window_beats = self.timing_windows.late_ms / 1000.0 * (self.clock.tempo_map.bpm_at_beat(now_beat) / 60.0)
            if abs(target.beat - now_beat) > window_beats:
                return True  # too early/late: swallow the key press, don't advance

        self._pending_events.remove(target)
        notes = target.notes if isinstance(target, ChordEvent) else [target.note]
        target_instrument = self.layer_routes.get(target.layer)
        for n in notes:
            self.event_sink(
                MusicEvent(
                    type=EventType.NOTE_ON,
                    note=n,
                    velocity=0.85,
                    timestamp_ns=ts_ns,
                    source=Source.ASSIST_ENGINE,
                    metadata={"key_id": f"assist_{key_id}_{n}", "target_instrument": target_instrument},
                )
            )
        self._active_song_voices[hash(key_id)] = key_id
        self._assist_active_notes = getattr(self, "_assist_active_notes", {})
        self._assist_active_notes[key_id] = (notes, target_instrument)

        if self.on_note_result and isinstance(target, NoteEvent):
            result = self.scoring.judge_hit(target, self._beat_to_wall_time_s(target.beat), target.note, ts_ns / 1e9)
            self.on_note_result(result)
        return True

    def _release_assist_voice(self, key_id: str) -> None:
        entry = getattr(self, "_assist_active_notes", {}).pop(key_id, None)
        if not entry:
            return
        notes, target_instrument = entry
        for n in notes:
            self.event_sink(
                MusicEvent(
                    type=EventType.NOTE_OFF,
                    note=n,
                    timestamp_ns=time.perf_counter_ns(),
                    source=Source.ASSIST_ENGINE,
                    metadata={"key_id": f"assist_{key_id}_{n}", "target_instrument": target_instrument},
                )
            )

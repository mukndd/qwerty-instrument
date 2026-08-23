"""Real-time audio engine: owns the sounddevice stream and the audio callback.

Threading model (see docs/ARCHITECTURE.md for the full picture):
    - Input threads (keyboard hook, F75, MIDI) push MusicEvent onto
      `self.event_queue` (a stdlib queue.Queue) and never touch audio state
      directly.
    - The sounddevice callback runs on PortAudio's own OS thread. Each call
      drains the event queue (non-blocking), updates voice state, renders
      audio, and returns -- no I/O, no logging, no allocation of anything
      large, no Qt calls.
    - UI feedback (current notes, knob value, underrun counts) flows out
      through `self.ui_feedback_queue`, polled by the UI thread on a timer.
      The callback never calls into UI code directly.
"""

from __future__ import annotations

import logging
import queue
import threading
import time
from dataclasses import dataclass, field

import numpy as np
import sounddevice as sd

from ..music.events import EventType, MusicEvent, Source
from . import devices
from .effects import Limiter
from .voices import InstrumentBackend

logger = logging.getLogger("qwerty_instrument.audio")


@dataclass
class AudioConfig:
    sample_rate: int = 48000
    block_size: int = 256
    channels: int = 2
    device_index: int | None = None  # None = system default output
    use_wasapi_exclusive: bool = False


@dataclass
class CallbackStats:
    callback_count: int = 0
    underrun_count: int = 0
    overrun_count: int = 0
    last_duration_ms: float = 0.0
    ema_duration_ms: float = 0.0
    max_duration_ms: float = 0.0
    reported_latency_s: float = 0.0
    max_simultaneous_notes: int = 0


class AudioEngine:
    def __init__(self, config: AudioConfig):
        self.config = config
        self.event_queue: queue.Queue[MusicEvent] = queue.Queue(maxsize=2048)
        self.ui_feedback_queue: queue.Queue[dict] = queue.Queue(maxsize=512)
        self.instruments: dict[str, InstrumentBackend] = {}
        self.active_instrument_name: str | None = None
        self.limiter = Limiter()
        self.stats = CallbackStats()
        self.stream: sd.OutputStream | None = None
        self._sustain_on = False
        self._pending_release: dict[tuple, tuple[MusicEvent, str]] = {}
        self._log_suppress_until = 0.0
        self._lock = threading.Lock()  # guards instrument dict mutation from non-audio threads
        self._recording = False
        self._record_queue: queue.Queue[np.ndarray] | None = None

    # ---- setup (never called from the audio thread) ----------------------

    def register_instrument(self, backend: InstrumentBackend) -> None:
        with self._lock:
            self.instruments[backend.name] = backend
            if self.active_instrument_name is None:
                self.active_instrument_name = backend.name

    def set_active_instrument(self, name: str) -> bool:
        with self._lock:
            if name not in self.instruments:
                return False
            self.panic()
            self.active_instrument_name = name
            return True

    def _warm_up(self) -> None:
        """Force first-touch costs (numpy ufunc dispatch, scipy import/JIT-ish
        caches, Python bytecode caching) to happen here, on the calling
        thread before the stream opens, rather than on the very first
        real-time callback. Measured without this: first callback ~20ms
        against a 5.3ms/256-frame budget, purely a one-time warm-up cost
        with zero underruns after -- but paying it before the deadline
        matters is strictly better and cheap to do.
        """
        for inst in self.instruments.values():
            warm_event = MusicEvent(type=EventType.NOTE_ON, note=60, velocity=0.001, source=Source.UI, metadata={"key_id": "__warmup__"})
            inst.note_on(warm_event)
            for _ in range(3):
                inst.render(self.config.block_size)
            inst.hard_silence()

    def start(self) -> None:
        self._warm_up()

        device = self.config.device_index
        if device is None:
            # sd.OutputStream(device=None) resolves to sounddevice's global
            # cross-hostapi default, which on Windows is frequently an MME
            # device with a large default buffer (~150-200ms). Prefer the
            # WASAPI default output explicitly so "no device configured"
            # still means "low latency" rather than silently falling back
            # to the worst available host API.
            device = devices.default_wasapi_output_device()

        exclusive_requested = self.config.use_wasapi_exclusive
        try:
            self._open_stream(device, exclusive=exclusive_requested)
        except Exception as exc:
            if not exclusive_requested:
                raise
            # Not every device/driver accepts exclusive mode (spec section
            # 2: "do not assume exclusive mode always works"). Fall back to
            # shared mode rather than failing to produce any sound at all.
            logger.warning("WASAPI exclusive mode failed (%s); falling back to shared mode", exc)
            self.config.use_wasapi_exclusive = False
            self._open_stream(device, exclusive=False)

        self.stream.start()
        try:
            self.stats.reported_latency_s = float(self.stream.latency)
        except Exception:
            self.stats.reported_latency_s = 0.0
        logger.info(
            "audio stream started: sr=%d block=%d device=%s exclusive=%s latency=%.1fms",
            self.config.sample_rate,
            self.config.block_size,
            device,
            self.config.use_wasapi_exclusive,
            self.stats.reported_latency_s * 1000,
        )

    def _open_stream(self, device: int | None, exclusive: bool) -> None:
        extra_settings = devices.make_wasapi_settings(exclusive=True) if exclusive else None
        self.stream = sd.OutputStream(
            samplerate=self.config.sample_rate,
            blocksize=self.config.block_size,
            channels=self.config.channels,
            dtype="float32",
            device=device,
            latency="low",
            callback=self._callback,
            extra_settings=extra_settings,
        )

    def stop(self) -> None:
        self.hard_silence_all()
        if self.stream is not None:
            self.stream.stop()
            self.stream.close()
            self.stream = None
            logger.info("audio stream stopped")

    # ---- event submission (called from input threads) ---------------------

    def submit(self, event: MusicEvent) -> None:
        try:
            self.event_queue.put_nowait(event)
        except queue.Full:
            logger.warning("event queue full; dropping event %s", event.type)

    def panic(self) -> None:
        """Soft, click-free all-notes-off across every registered instrument."""
        for inst in self.instruments.values():
            inst.all_notes_off()
        self._pending_release.clear()
        self._sustain_on = False

    def hard_silence_all(self) -> None:
        """Immediate, unconditional silence for shutdown/device-change."""
        for inst in self.instruments.values():
            inst.hard_silence()
        self._pending_release.clear()
        self._sustain_on = False

    # ---- recording tap (see persistence/recording.py) ---------------------

    def start_recording(self, sink_queue: "queue.Queue[np.ndarray]") -> None:
        self._record_queue = sink_queue
        self._recording = True

    def stop_recording(self) -> None:
        self._recording = False
        self._record_queue = None

    # ---- the real-time callback --------------------------------------------

    def _callback(self, outdata: np.ndarray, frames: int, time_info, status) -> None:
        t0 = time.perf_counter_ns()
        if status:
            self._handle_status(status)

        while True:
            try:
                event = self.event_queue.get_nowait()
            except queue.Empty:
                break
            self._process_event(event)

        active_name = self.active_instrument_name
        if active_name is None or active_name not in self.instruments:
            outdata[:] = 0.0
            return

        audio = self.instruments[active_name].render(frames)
        audio = self.limiter.process(audio)
        outdata[:] = audio

        if self._recording and self._record_queue is not None:
            try:
                self._record_queue.put_nowait(audio.copy())
            except queue.Full:
                pass

        total_active = sum(i.active_voice_count for i in self.instruments.values())
        self.stats.max_simultaneous_notes = max(self.stats.max_simultaneous_notes, total_active)

        dt_ms = (time.perf_counter_ns() - t0) / 1e6
        self.stats.callback_count += 1
        self.stats.last_duration_ms = dt_ms
        self.stats.ema_duration_ms = 0.9 * self.stats.ema_duration_ms + 0.1 * dt_ms
        self.stats.max_duration_ms = max(self.stats.max_duration_ms, dt_ms)

        try:
            self.ui_feedback_queue.put_nowait(
                {
                    "type": "stats",
                    "active_notes": total_active,
                    "callback_ms": dt_ms,
                    "underruns": self.stats.underrun_count,
                }
            )
        except queue.Full:
            pass

    def _handle_status(self, status) -> None:
        now = time.monotonic()
        if status.output_underflow:
            self.stats.underrun_count += 1
        if status.output_overflow:
            self.stats.overrun_count += 1
        if now > self._log_suppress_until:
            logger.warning("audio callback status: %s", status)
            self._log_suppress_until = now + 1.0  # rate-limit repetitive warnings

    def _process_event(self, event: MusicEvent) -> None:
        et = event.type
        # NOTE_ON/OFF/PITCH_BEND/EXPRESSION may target a specific instrument
        # via metadata["target_instrument"] (used by autoplay layer routing:
        # chords/bass/melody/lead_guitar can each sound through a different
        # backend simultaneously) instead of "whatever is currently active".
        # Manual QWERTY events never set this, so active_instrument_name
        # keeps working exactly as before for them.
        target = event.metadata.get("target_instrument") if event.metadata else None
        active_name = target if (target and target in self.instruments) else self.active_instrument_name
        if et == EventType.NOTE_ON:
            if active_name and active_name in self.instruments:
                self.instruments[active_name].note_on(event)
                try:
                    self.ui_feedback_queue.put_nowait(
                        {"type": "note_on", "note": event.note, "source": event.source.name, "metadata": event.metadata}
                    )
                except queue.Full:
                    pass
        elif et == EventType.NOTE_OFF:
            vid = event.voice_id
            if self._sustain_on:
                self._pending_release[vid] = (event, active_name)
            elif active_name and active_name in self.instruments:
                self.instruments[active_name].note_off(event)
            try:
                self.ui_feedback_queue.put_nowait({"type": "note_off", "note": event.note, "metadata": event.metadata})
            except queue.Full:
                pass
        elif et == EventType.SUSTAIN:
            on = bool(event.metadata.get("on", False))
            self._sustain_on = on
            if not on:
                for vid, (ev, inst_name) in list(self._pending_release.items()):
                    if inst_name and inst_name in self.instruments:
                        self.instruments[inst_name].note_off(ev)
                self._pending_release.clear()
        elif et == EventType.PITCH_BEND:
            cents = float(event.metadata.get("cents", 0.0))
            if active_name and active_name in self.instruments:
                self.instruments[active_name].pitch_bend_active(cents)
        elif et in (EventType.EXPRESSION, EventType.MODULATION):
            param = event.metadata.get("param", "expression")
            value = event.metadata.get("value", 0.0)
            if active_name and active_name in self.instruments:
                self.instruments[active_name].set_param(param, value)
        elif et == EventType.PROGRAM_CHANGE:
            name = event.metadata.get("instrument")
            if name and name in self.instruments:
                self.panic()
                self.active_instrument_name = name
        elif et == EventType.ALL_NOTES_OFF:
            self.panic()
        # MODE_CHANGE / TRANSPORT_* are consumed by higher-level engines
        # (song/practice); the audio engine deliberately ignores them.

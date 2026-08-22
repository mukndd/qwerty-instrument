"""Non-blocking recording: a background thread drains audio blocks the
engine pushes onto a queue (see AudioEngine.start_recording) and writes
them to a WAV file. The audio callback only ever does a non-blocking
queue put -- never file I/O -- so recording cannot introduce underruns.

Performance (event) recording is separate and even simpler: it just
appends the lightweight dicts the engine already publishes on
`ui_feedback_queue` for UI consumption, so capturing a performance is a
matter of also handing that queue's contents to a PerformanceRecorder.
"""

from __future__ import annotations

import json
import logging
import queue
import threading
import time
import wave
from pathlib import Path

import numpy as np

logger = logging.getLogger("qwerty_instrument.persistence")


class AudioRecorder:
    def __init__(self, sample_rate: int, channels: int = 2):
        self.sample_rate = sample_rate
        self.channels = channels
        self._queue: "queue.Queue[np.ndarray]" = queue.Queue(maxsize=1000)
        self._thread: threading.Thread | None = None
        self._wav_file: wave.Wave_write | None = None
        self._running = False

    def start(self, out_path: Path) -> "queue.Queue[np.ndarray]":
        out_path.parent.mkdir(parents=True, exist_ok=True)
        self._wav_file = wave.open(str(out_path), "wb")
        self._wav_file.setnchannels(self.channels)
        self._wav_file.setsampwidth(2)  # 16-bit PCM file; engine blocks are float32 internally
        self._wav_file.setframerate(self.sample_rate)
        self._running = True
        self._thread = threading.Thread(target=self._writer_loop, daemon=True, name="qwerty-wav-writer")
        self._thread.start()
        logger.info("recording started -> %s", out_path)
        return self._queue

    def _writer_loop(self) -> None:
        while self._running or not self._queue.empty():
            try:
                block = self._queue.get(timeout=0.2)
            except queue.Empty:
                continue
            pcm = np.clip(block, -1.0, 1.0)
            pcm16 = (pcm * 32767.0).astype(np.int16)
            if self._wav_file is not None:
                self._wav_file.writeframes(pcm16.tobytes())

    def stop(self) -> None:
        self._running = False
        if self._thread is not None:
            self._thread.join(timeout=3.0)
            self._thread = None
        if self._wav_file is not None:
            self._wav_file.close()
            self._wav_file = None
        logger.info("recording stopped")


class PerformanceRecorder:
    """Captures the note-on/off/etc feedback stream for later analysis or
    export -- not audio, just the event timeline."""

    def __init__(self):
        self.events: list[dict] = []
        self._start_time = time.perf_counter()

    def on_feedback(self, feedback: dict) -> None:
        entry = dict(feedback)
        entry["t"] = round(time.perf_counter() - self._start_time, 6)
        self.events.append(entry)

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(self.events, f, indent=2)

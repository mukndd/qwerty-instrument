"""Application wiring: config -> mapping -> input backends -> audio engine.

This module has no UI dependency -- it's the same wiring the PySide6 UI
(ui/main_window.py) drives, and what tools/audio_diagnostics.py and tests
use to exercise the real signal path without a GUI.
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path

from . import config as config_mod
from .audio.engine import AudioConfig, AudioEngine
from .audio.guitar import GuitarLead
from .audio.synth import ElectricPiano, SynthLead
from .input.f75 import F75Knob, KnobConfig
from .input.keyboard import KeyboardInstrument
from .input.windows_hook import WindowsLowLevelKeyboardHook
from .music.events import EventType, MusicEvent, Source
from .music.mapping import KeyboardMapping
from .songs.trainer import PracticeMode, SongTrainer

logger = logging.getLogger("qwerty_instrument.app")


def setup_logging(log_dir: Path | None = None) -> None:
    handlers: list[logging.Handler] = [logging.StreamHandler(sys.stdout)]
    if log_dir is not None:
        log_dir.mkdir(parents=True, exist_ok=True)
        handlers.append(logging.FileHandler(log_dir / "application.log", encoding="utf-8"))
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s", handlers=handlers)


class Application:
    """Owns the full non-UI signal path: mapping, input, audio engine."""

    def __init__(self, cfg: dict | None = None):
        self.cfg = cfg or config_mod.load_config()
        self.mapping = KeyboardMapping.from_config(self.cfg["keyboard"])

        audio_cfg = self.cfg["audio"]
        self.engine = AudioEngine(
            AudioConfig(
                sample_rate=audio_cfg["sample_rate"],
                block_size=audio_cfg["block_size"],
                device_index=audio_cfg["device_index"],
                use_wasapi_exclusive=audio_cfg["use_wasapi_exclusive"],
            )
        )

        poly = self.cfg["instruments"]["polyphony"]
        self.engine.register_instrument(SynthLead(audio_cfg["sample_rate"], audio_cfg["block_size"], polyphony=poly["synth_lead"]))
        self.engine.register_instrument(ElectricPiano(audio_cfg["sample_rate"], audio_cfg["block_size"], polyphony=poly["electric_piano"]))
        self.engine.register_instrument(GuitarLead(audio_cfg["sample_rate"], audio_cfg["block_size"], polyphony=poly["guitar_lead"]))
        self.engine.set_active_instrument(self.cfg["instruments"]["default"])

        knob_cfg = self.cfg["knob"]
        self.knob = F75Knob(
            event_sink=self.engine.submit,
            config=KnobConfig(
                mode=knob_cfg["mode"],
                bend_step_cents=knob_cfg["bend_step_cents"],
                max_bend_cents=knob_cfg["max_bend_cents"],
                expression_step=knob_cfg["expression_step"],
                spring_return=knob_cfg["spring_return"],
                return_delay_s=knob_cfg["return_delay_s"],
                return_duration_s=knob_cfg["return_duration_s"],
                short_press_max_s=knob_cfg["short_press_max_s"],
            ),
            on_short_press=self._on_knob_short_press,
        )

        kb_cfg = self.cfg["keyboard"]
        self._hook = WindowsLowLevelKeyboardHook()
        self.keyboard = KeyboardInstrument(
            mapping=self.mapping,
            input_source=self._hook,
            event_sink=self._on_note_played,
            instrument_names=self.cfg["instruments"]["cycle_order"],
            sustain_mode=kb_cfg["sustain_mode"],
            base_velocity=kb_cfg["base_velocity"],
            humanize_amount=kb_cfg["humanize_amount"],
        )
        self._hook.set_suppress_predicate(self._should_suppress)
        self._lead_guitar_mode = False

        # Optional: set via load_song()/self.trainer. When a song is loaded
        # and Assist mode is active, performance keys are intercepted here
        # before falling through to normal QWERTY note mapping; in Real/
        # Guided mode we instead just observe NOTE_ON events for scoring.
        self.trainer: SongTrainer | None = None

    # ---- song/practice wiring -------------------------------------------------

    def load_trainer(self, trainer: SongTrainer) -> None:
        self.trainer = trainer

    def unload_trainer(self) -> None:
        if self.trainer is not None:
            self.trainer.stop()
        self.trainer = None

    # ---- wiring helpers -----------------------------------------------------

    def _should_suppress(self, key_id: str) -> bool:
        if key_id in ("VOLUME_UP", "VOLUME_DOWN", "VOLUME_MUTE"):
            return True  # always intercept the knob's multimedia VKs while capture is on
        if self.trainer is not None and self.trainer.mode == PracticeMode.ASSIST and key_id in self.trainer.assist_keys:
            return True
        return self.keyboard.suppress_predicate(key_id)

    def _dispatch_raw_key(self, key_id: str, is_down: bool, ts_ns: int) -> None:
        """The hook's single entry point: F75 knob, then Assist performance keys, then QWERTY."""
        if self.knob.handle_key(key_id, is_down, ts_ns):
            return
        if self.trainer is not None and self.trainer.handle_performance_key(key_id, is_down, ts_ns):
            return
        self.keyboard.handle_key_event(key_id, is_down, ts_ns)

    def _on_note_played(self, event: MusicEvent) -> None:
        """Wraps engine.submit so Real/Guided modes can score the player's own notes."""
        self.engine.submit(event)
        if (
            self.trainer is not None
            and self.trainer.mode in (PracticeMode.REAL, PracticeMode.GUIDED)
            and event.type == EventType.NOTE_ON
        ):
            self.trainer.judge_played_note(event.note, event.timestamp_ns)

    def _on_knob_short_press(self) -> None:
        self._lead_guitar_mode = not self._lead_guitar_mode
        target = "guitar_lead" if self._lead_guitar_mode else self.cfg["instruments"]["default"]
        self.engine.submit(
            MusicEvent(type=EventType.PROGRAM_CHANGE, source=Source.F75_KNOB, metadata={"instrument": target})
        )
        logger.info("F75 knob short-press: lead guitar mode = %s", self._lead_guitar_mode)

    # ---- lifecycle ------------------------------------------------------------

    def start(self) -> None:
        self.engine.start()
        self._hook.start(self._dispatch_raw_key)
        self._hook.set_capture_enabled(self.keyboard.capture_enabled)
        logger.info("application started: capture=%s active_instrument=%s", self.keyboard.capture_enabled, self.engine.active_instrument_name)

    def stop(self) -> None:
        self.keyboard.stop()
        self.engine.stop()
        logger.info("application stopped")

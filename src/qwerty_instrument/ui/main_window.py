"""Main application window.

Threading note (spec section 38): this file only ever runs on the Qt/UI
thread. It never touches audio-engine or input-hook internals directly
except through thread-safe entry points (engine.submit, which just does a
queue.put_nowait) and a QTimer polling `engine.ui_feedback_queue`, which
the audio callback populates via non-blocking puts. No Qt widget is ever
touched from the audio thread or the keyboard-hook thread.
"""

from __future__ import annotations

import queue
import time
from pathlib import Path

from PySide6.QtCore import QTimer, Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QSlider,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from ..music.events import EventType, MusicEvent, Source
from ..songs.export import write_all_exports
from ..songs.loader import list_available_songs, load_song
from ..songs.trainer import AssistStyle, PracticeMode, SongTrainer
from .diagnostics import DiagnosticsWidget
from .keyboard_widget import KeyboardWidget
from .settings import AudioSettingsDialog
from .timeline_widget import TimelineWidget

STYLE = """
QMainWindow, QWidget { background-color: #17171b; color: #dcdce0; }
QGroupBox { border: 1px solid #3a3a42; border-radius: 4px; margin-top: 8px; padding-top: 6px; }
QGroupBox::title { subcontrol-origin: margin; left: 8px; }
QPushButton { background-color: #2a2a30; border: 1px solid #444; border-radius: 4px; padding: 6px 10px; }
QPushButton:hover { background-color: #35353d; }
QComboBox, QSlider { padding: 3px; }
"""


class MainWindow(QMainWindow):
    def __init__(self, app):
        super().__init__()
        self.app = app
        self.setWindowTitle("QWERTY Instrument -- Instant Crush Engine")
        self.resize(980, 720)
        self.setStyleSheet(STYLE)

        self.trainer: SongTrainer | None = None
        self._songs_root = Path("songs")
        self._exports_root = Path("exports")

        self._build_ui()
        self._wire_defaults()

        self.timer = QTimer(self)
        self.timer.timeout.connect(self._tick)
        self.timer.start(33)  # ~30Hz UI refresh

    # ---- UI construction ---------------------------------------------------

    def _build_ui(self) -> None:
        central = QWidget()
        self.setCentralWidget(central)
        root = QVBoxLayout(central)

        root.addLayout(self._build_top_bar())

        self.lead_guitar_banner = QLabel("LEAD GUITAR")
        self.lead_guitar_banner.setAlignment(Qt.AlignCenter)
        self.lead_guitar_banner.setStyleSheet("background-color:#7a3fb0; color:white; font-size:20px; font-weight:bold; padding:6px;")
        self.lead_guitar_banner.hide()
        root.addWidget(self.lead_guitar_banner)

        self.autoplay_banner = QLabel("AUTOPLAY -- APPROXIMATE REFERENCE (not a verified transcription)")
        self.autoplay_banner.setAlignment(Qt.AlignCenter)
        self.autoplay_banner.setStyleSheet("background-color:#3f5f7a; color:white; font-size:16px; font-weight:bold; padding:5px;")
        self.autoplay_banner.hide()
        root.addWidget(self.autoplay_banner)

        self.timeline = TimelineWidget()
        root.addWidget(self.timeline)

        self.keyboard_widget = KeyboardWidget()
        root.addWidget(self.keyboard_widget, stretch=1)

        tabs = QTabWidget()
        tabs.addTab(self._build_practice_panel(), "Practice")
        self.diagnostics = DiagnosticsWidget(self.app)
        tabs.addTab(self.diagnostics, "Diagnostics")
        root.addWidget(tabs)

        root.addWidget(self._build_status_bar())

    def _build_top_bar(self) -> QHBoxLayout:
        bar = QHBoxLayout()

        bar.addWidget(QLabel("Instrument:"))
        self.instrument_combo = QComboBox()
        for name in self.app.cfg["instruments"]["cycle_order"]:
            self.instrument_combo.addItem(name)
        self.instrument_combo.currentTextChanged.connect(self._on_instrument_selected)
        bar.addWidget(self.instrument_combo)

        bar.addWidget(QLabel("Song:"))
        self.song_combo = QComboBox()
        self.song_combo.addItem("(none)")
        for song_id in list_available_songs(self._songs_root):
            self.song_combo.addItem(song_id)
        self.song_combo.currentTextChanged.connect(self._on_song_selected)
        bar.addWidget(self.song_combo)

        bar.addWidget(QLabel("Preset:"))
        self.preset_combo = QComboBox()
        self._preset_paths: dict[str, Path] = {}
        self._reload_presets_list()
        self.preset_combo.currentTextChanged.connect(self._on_preset_selected)
        bar.addWidget(self.preset_combo)

        bar.addStretch(1)

        self.capture_btn = QPushButton("Capture: ON")
        self.capture_btn.clicked.connect(self._toggle_capture)
        bar.addWidget(self.capture_btn)

        audio_btn = QPushButton("Audio Settings")
        audio_btn.clicked.connect(self._open_audio_settings)
        bar.addWidget(audio_btn)

        return bar

    def _build_practice_panel(self) -> QWidget:
        panel = QWidget()
        layout = QHBoxLayout(panel)

        mode_group = QGroupBox("Mode")
        mode_layout = QVBoxLayout(mode_group)
        self.mode_combo = QComboBox()
        self.mode_combo.addItems(["Real", "Guided", "Assist", "Autoplay"])
        self.mode_combo.currentTextChanged.connect(self._on_mode_selected)
        mode_layout.addWidget(self.mode_combo)
        self.autoplay_layer_combo = QComboBox()
        self.autoplay_layer_combo.addItems(["Chords", "Bass", "Chords + Bass"])
        self.autoplay_layer_combo.setCurrentText("Chords + Bass")
        self.autoplay_layer_combo.currentTextChanged.connect(self._on_autoplay_layer_selected)
        mode_layout.addWidget(self.autoplay_layer_combo)
        reload_preset_btn = QPushButton("Reload Preset")
        reload_preset_btn.clicked.connect(lambda: self._on_preset_selected(self.preset_combo.currentText()))
        mode_layout.addWidget(reload_preset_btn)
        layout.addWidget(mode_group)

        section_group = QGroupBox("Section")
        section_layout = QVBoxLayout(section_group)
        self.section_combo = QComboBox()
        self.section_combo.currentTextChanged.connect(self._on_section_selected)
        section_layout.addWidget(self.section_combo)
        self.loop_check = QCheckBox("Loop section")
        self.loop_check.toggled.connect(self._on_loop_toggled)
        section_layout.addWidget(self.loop_check)
        self.metronome_check = QCheckBox("Metronome")
        self.metronome_check.toggled.connect(self._on_metronome_toggled)
        section_layout.addWidget(self.metronome_check)
        layout.addWidget(section_group)

        speed_group = QGroupBox("Practice Speed")
        speed_layout = QVBoxLayout(speed_group)
        self.speed_label = QLabel("100%")
        speed_layout.addWidget(self.speed_label)
        self.speed_slider = QSlider(Qt.Horizontal)
        self.speed_slider.setRange(50, 100)
        self.speed_slider.setValue(100)
        self.speed_slider.setTickInterval(10)
        self.speed_slider.valueChanged.connect(self._on_speed_changed)
        speed_layout.addWidget(self.speed_slider)
        layout.addWidget(speed_group)

        transport_group = QGroupBox("Transport")
        transport_layout = QVBoxLayout(transport_group)
        self.start_btn = QPushButton("Start")
        self.start_btn.clicked.connect(self._start_practice)
        transport_layout.addWidget(self.start_btn)
        self.stop_btn = QPushButton("Stop")
        self.stop_btn.clicked.connect(self._stop_practice)
        transport_layout.addWidget(self.stop_btn)
        export_btn = QPushButton("Export Practice Sheet")
        export_btn.clicked.connect(self._export_practice_sheet)
        transport_layout.addWidget(export_btn)
        layout.addWidget(transport_group)

        self.score_label = QLabel("")
        layout.addWidget(self.score_label, stretch=1)

        return panel

    def _build_status_bar(self) -> QWidget:
        bar = QWidget()
        layout = QHBoxLayout(bar)
        self.mode_status = QLabel("MODE: --")
        self.instrument_status = QLabel("INSTRUMENT: --")
        self.bpm_status = QLabel("BPM: --")
        self.speed_status = QLabel("SPEED: 100%")
        self.latency_status = QLabel("LATENCY: --")
        self.buffer_status = QLabel("BUFFER: --")
        self.knob_status = QLabel("KNOB: --")
        for w in (self.mode_status, self.instrument_status, self.bpm_status, self.speed_status, self.latency_status, self.buffer_status, self.knob_status):
            w.setStyleSheet("color:#9fd3ff; padding:2px 8px;")
            layout.addWidget(w)
        layout.addStretch(1)
        return bar

    def _wire_defaults(self) -> None:
        self.keyboard_widget.label_mode = self.app.cfg["ui"]["note_label_mode"]
        note_labels = {}
        for key_id, note in self.app.mapping.note_map.items():
            from ..music.notes import midi_to_name

            note_labels[key_id] = midi_to_name(note)
        self.keyboard_widget.set_note_labels(note_labels)
        self.instrument_combo.setCurrentText(self.app.engine.active_instrument_name or "")

    # ---- event handlers ------------------------------------------------------

    def _on_instrument_selected(self, name: str) -> None:
        if not name:
            return
        self.app.engine.submit(MusicEvent(type=EventType.PROGRAM_CHANGE, source=Source.UI, metadata={"instrument": name}))

    def _toggle_capture(self) -> None:
        self.app.keyboard.toggle_capture()

    def _open_audio_settings(self) -> None:
        dlg = AudioSettingsDialog(self.app, self)
        dlg.exec()

    def _on_song_selected(self, song_id: str) -> None:
        if not song_id or song_id == "(none)":
            self.app.unload_trainer()
            self.trainer = None
            self.section_combo.clear()
            return
        song = load_song(self._songs_root / song_id)
        self.trainer = SongTrainer(song, self.app.mapping, self.app.engine.submit)
        self.trainer.on_note_result = self._on_note_result
        self.app.load_trainer(self.trainer)
        self.section_combo.clear()
        for s in song.sections:
            self.section_combo.addItem(s.id)
        if song.notes_disclaimer:
            QMessageBox.information(self, "Song data status", song.notes_disclaimer)

    def _on_section_selected(self, section_id: str) -> None:
        if self.trainer and section_id:
            self.trainer.load_section(section_id)
            self.trainer.loop_enabled = self.loop_check.isChecked()

    def _on_mode_selected(self, mode_text: str) -> None:
        if self.trainer:
            self.trainer.set_mode(PracticeMode(mode_text.lower()))

    def _on_autoplay_layer_selected(self, text: str) -> None:
        layers = {"Chords": ["chords"], "Bass": ["bass"], "Chords + Bass": ["chords", "bass"]}.get(text, ["chords", "bass"])
        if self.trainer:
            self.trainer.set_autoplay_layers(layers)

    def _reload_presets_list(self) -> None:
        from ..audio.presets import load_preset

        self.preset_combo.clear()
        self._preset_paths.clear()
        for p in sorted(Path("presets").glob("*.json")):
            preset = load_preset(p)
            if preset:
                self.preset_combo.addItem(preset.name)
                self._preset_paths[preset.name] = p

    def _on_preset_selected(self, name: str) -> None:
        from ..audio.presets import apply_preset, load_preset

        path = self._preset_paths.get(name)
        if not path:
            return
        preset = load_preset(path)  # reload from disk every time -- fast iteration on the JSON file
        if not preset:
            return
        backend = self.app.engine.instruments.get(preset.instrument)
        if backend:
            apply_preset(backend, preset)

    def _on_loop_toggled(self, checked: bool) -> None:
        if self.trainer:
            self.trainer.loop_enabled = checked

    def _on_metronome_toggled(self, checked: bool) -> None:
        if self.trainer:
            self.trainer.metronome_enabled = checked

    def _on_speed_changed(self, value: int) -> None:
        self.speed_label.setText(f"{value}%")
        if self.trainer:
            self.trainer.set_speed_percent(value)

    def _start_practice(self) -> None:
        if not self.trainer:
            return
        if self.trainer.mode == PracticeMode.AUTOPLAY and self.song_combo.currentText() == "instant_crush":
            # Ensure the tuned Instant Crush patch is actually applied, not
            # just sitting unused in presets/instant_crush_synth.json.
            if "Instant Crush Synth" in self._preset_paths:
                self.preset_combo.setCurrentText("Instant Crush Synth")
                self._on_preset_selected("Instant Crush Synth")
        self.trainer.start()

    def _stop_practice(self) -> None:
        if self.trainer:
            self.trainer.stop()

    def _export_practice_sheet(self) -> None:
        if not self.trainer:
            QMessageBox.warning(self, "No song loaded", "Load a song first.")
            return
        paths = write_all_exports(self.trainer.song, self.app.mapping, self._exports_root, speed_percent=self.speed_slider.value(), filename_stem=self.song_combo.currentText())
        QMessageBox.information(self, "Exported", "Wrote:\n" + "\n".join(str(p) for p in paths.values()))

    def _on_note_result(self, result) -> None:
        s = self.trainer.scoring.state
        self.score_label.setText(
            f"Perfect: {s.perfect}  Good: {s.good}  Early: {s.early}  Late: {s.late}  Miss: {s.miss}  Wrong: {s.wrong_note}\n"
            f"Combo: {s.combo} (max {s.max_combo})  Pitch acc: {s.pitch_accuracy_percent():.0f}%  Timing acc: {s.timing_accuracy_percent():.0f}%"
        )

    # ---- polling tick (UI thread only) ---------------------------------------

    def _tick(self) -> None:
        self._drain_feedback()
        is_autoplay = False
        if self.trainer:
            self.trainer.tick()
            self.timeline.update_upcoming(self.trainer.upcoming_notes())
            is_autoplay = self.trainer.mode == PracticeMode.AUTOPLAY
            if is_autoplay:
                mapped_keys = set()
                for note in self.trainer.autoplay_sounding_notes:
                    found = self.app.mapping.find_key_for_note(note)
                    if found:
                        mapped_keys.add(found[0])
                self.keyboard_widget.set_state(held=mapped_keys)

        self.capture_btn.setText(f"Capture: {'ON' if self.app.keyboard.capture_enabled else 'OFF'}")
        self.lead_guitar_banner.setVisible(getattr(self.app, "_lead_guitar_mode", False))
        self.autoplay_banner.setVisible(is_autoplay)

        self.mode_status.setText(f"MODE: {self.mode_combo.currentText().upper()}")
        self.instrument_status.setText(f"INSTRUMENT: {self.app.engine.active_instrument_name}")
        self.speed_status.setText(f"SPEED: {self.speed_slider.value()}%")
        self.latency_status.setText(f"LATENCY: {self.app.engine.stats.reported_latency_s * 1000:.1f} ms")
        self.buffer_status.setText(f"BUFFER: {self.app.engine.config.block_size}")
        if self.trainer:
            bpm = self.trainer.song.tempo_map.bpm_at_beat(self.trainer.current_beat())
            self.bpm_status.setText(f"BPM: {bpm:.0f}")

        knob = self.app.knob
        if knob.config.mode == "pitch_bend":
            self.knob_status.setText(f"KNOB: PITCH BEND {knob.value:+.0f}c")
        else:
            self.knob_status.setText(f"KNOB: {knob.config.mode.upper()} {knob.value * 100:.0f}%")

        self.diagnostics.refresh_stats()

    def _drain_feedback(self) -> None:
        held = set(self.keyboard_widget.held_keys)
        wrong_hit = None
        while True:
            try:
                fb = self.app.engine.ui_feedback_queue.get_nowait()
            except queue.Empty:
                break
            self.diagnostics.on_feedback(fb)
            key_id = fb.get("metadata", {}).get("key_id") if "metadata" in fb else None
            if fb.get("type") == "note_on" and key_id:
                held.add(key_id)
            elif fb.get("type") == "note_off" and key_id:
                held.discard(key_id)
        if held != self.keyboard_widget.held_keys:
            self.keyboard_widget.set_state(held=held)

    def closeEvent(self, event) -> None:  # noqa: N802 -- Qt override
        self.app.stop()
        super().closeEvent(event)

"""Audio Settings dialog: device/buffer selection, WASAPI mode, and the
Test Tone / Latency Test / Stress Test buttons from spec section 24.
"""

from __future__ import annotations

import time

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
)

from ..audio import devices as devmod
from ..music.events import EventType, MusicEvent, Source

BLOCK_SIZES = [64, 128, 256, 512]


class AudioSettingsDialog(QDialog):
    def __init__(self, app, parent=None):
        super().__init__(parent)
        self.app = app
        self.setWindowTitle("Audio Settings")
        self.setMinimumWidth(480)

        layout = QVBoxLayout(self)

        device_group = QGroupBox("Output Device")
        device_layout = QVBoxLayout(device_group)
        self.device_combo = QComboBox()
        self._device_indices: list[int | None] = [None]
        self.device_combo.addItem("System default (WASAPI)")
        for d in devmod.list_output_devices():
            self.device_combo.addItem(f"[{d.hostapi_name}] {d.name}")
            self._device_indices.append(d.index)
        device_layout.addWidget(self.device_combo)
        layout.addWidget(device_group)

        buffer_group = QGroupBox("Buffer / Sample Rate")
        buffer_layout = QHBoxLayout(buffer_group)
        buffer_layout.addWidget(QLabel("Block size:"))
        self.block_combo = QComboBox()
        for b in BLOCK_SIZES:
            self.block_combo.addItem(f"{b} frames ({b / 48000 * 1000:.2f} ms @ 48kHz)", b)
        self.block_combo.setCurrentIndex(BLOCK_SIZES.index(self.app.engine.config.block_size))
        buffer_layout.addWidget(self.block_combo)
        self.exclusive_check = QCheckBox("WASAPI exclusive mode")
        self.exclusive_check.setChecked(self.app.engine.config.use_wasapi_exclusive)
        buffer_layout.addWidget(self.exclusive_check)
        layout.addWidget(buffer_group)

        apply_btn = QPushButton("Apply (reopens audio stream)")
        apply_btn.clicked.connect(self._apply)
        layout.addWidget(apply_btn)

        status_group = QGroupBox("Status")
        status_layout = QVBoxLayout(status_group)
        self.status_label = QLabel()
        status_layout.addWidget(self.status_label)
        layout.addWidget(status_group)
        self._refresh_status()

        test_group = QGroupBox("Tests")
        test_layout = QHBoxLayout(test_group)
        tone_btn = QPushButton("Test Tone")
        tone_btn.clicked.connect(self._test_tone)
        latency_btn = QPushButton("Latency Test")
        latency_btn.clicked.connect(self._latency_test)
        stress_btn = QPushButton("Stress Test")
        stress_btn.clicked.connect(self._stress_test)
        test_layout.addWidget(tone_btn)
        test_layout.addWidget(latency_btn)
        test_layout.addWidget(stress_btn)
        layout.addWidget(test_group)

        close_btn = QPushButton("Close")
        close_btn.clicked.connect(self.accept)
        layout.addWidget(close_btn, alignment=Qt.AlignRight)

    def _refresh_status(self) -> None:
        s = self.app.engine.stats
        cfg = self.app.engine.config
        self.status_label.setText(
            f"sample_rate={cfg.sample_rate}  block={cfg.block_size}  exclusive={cfg.use_wasapi_exclusive}\n"
            f"reported latency: {s.reported_latency_s * 1000:.2f} ms\n"
            f"underruns: {s.underrun_count}   overruns: {s.overrun_count}\n"
            f"avg callback: {s.ema_duration_ms:.3f} ms   max: {s.max_duration_ms:.3f} ms"
        )

    def _apply(self) -> None:
        device_index = self._device_indices[self.device_combo.currentIndex()]
        block_size = self.block_combo.currentData()
        exclusive = self.exclusive_check.isChecked()

        self.app.engine.stop()
        self.app.engine.config.device_index = device_index
        self.app.engine.config.block_size = block_size
        self.app.engine.config.use_wasapi_exclusive = exclusive
        try:
            self.app.engine.start()
        except Exception as exc:
            QMessageBox.critical(self, "Audio Error", f"Failed to open audio stream:\n{exc}")
            return
        self._refresh_status()

    def _test_tone(self) -> None:
        engine = self.app.engine
        prev = engine.active_instrument_name
        engine.set_active_instrument("synth_lead")
        engine.submit(MusicEvent(type=EventType.NOTE_ON, note=69, velocity=0.5, timestamp_ns=time.perf_counter_ns(), source=Source.UI, metadata={"key_id": "settings_tone"}))

        def stop_tone():
            engine.submit(MusicEvent(type=EventType.NOTE_OFF, timestamp_ns=time.perf_counter_ns(), source=Source.UI, metadata={"key_id": "settings_tone"}))
            if prev:
                engine.set_active_instrument(prev)

        from PySide6.QtCore import QTimer

        QTimer.singleShot(800, stop_tone)

    def _latency_test(self) -> None:
        self._refresh_status()
        QMessageBox.information(
            self,
            "Latency",
            f"Reported stream latency: {self.app.engine.stats.reported_latency_s * 1000:.2f} ms\n\n"
            "This is the software-path latency PortAudio reports for the current "
            "device/buffer combination, not a loopback-measured acoustic latency.",
        )

    def _stress_test(self) -> None:
        import random

        engine = self.app.engine
        prev = engine.active_instrument_name
        engine.set_active_instrument("synth_lead")
        for i in range(40):
            note = random.randint(48, 84)
            engine.submit(MusicEvent(type=EventType.NOTE_ON, note=note, velocity=0.7, timestamp_ns=time.perf_counter_ns(), source=Source.UI, metadata={"key_id": f"stress{i}"}))
        engine.submit(MusicEvent(type=EventType.ALL_NOTES_OFF, source=Source.UI))
        if prev:
            engine.set_active_instrument(prev)

        from PySide6.QtCore import QTimer

        QTimer.singleShot(500, self._refresh_status)

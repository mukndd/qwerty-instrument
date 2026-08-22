"""First-run setup wizard (spec section 41): audio output, a latency/
buffer check, F75 knob detection guidance, and a test scale -- then
saves the resulting config so it doesn't run again.
"""

from __future__ import annotations

import time

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QComboBox,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWizard,
    QWizardPage,
)

from .. import config as config_mod
from ..audio import devices as devmod
from ..music.events import EventType, MusicEvent, Source


class AudioOutputPage(QWizardPage):
    def __init__(self, app):
        super().__init__()
        self.app = app
        self.setTitle("1. Audio Output")
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("Choose the output device the instrument should play through:"))
        self.combo = QComboBox()
        self._indices: list[int | None] = [None]
        self.combo.addItem("System default (WASAPI)")
        for d in devmod.list_output_devices():
            self.combo.addItem(f"[{d.hostapi_name}] {d.name}")
            self._indices.append(d.index)
        layout.addWidget(self.combo)

    def selected_device(self) -> int | None:
        return self._indices[self.combo.currentIndex()]


class KeyboardCapturePage(QWizardPage):
    def __init__(self, app):
        super().__init__()
        self.setTitle("2. Keyboard Input")
        layout = QVBoxLayout(self)
        layout.addWidget(
            QLabel(
                "Instrument Capture is now on: normal QWERTY keys play notes\n"
                "instead of typing. Press CapsLock at any time to toggle capture\n"
                "off and get normal typing back.\n\n"
                "Try pressing a few letter keys now -- you should hear notes."
            )
        )


class KnobPage(QWizardPage):
    def __init__(self, app):
        super().__init__()
        self.app = app
        self.setTitle("3. F75 Knob")
        layout = QVBoxLayout(self)
        self.status = QLabel("Rotate the knob clockwise, then counter-clockwise, then press it.")
        layout.addWidget(self.status)
        self.detected = QLabel("Detected: (nothing yet)")
        layout.addWidget(self.detected)
        layout.addWidget(
            QLabel(
                "If nothing is detected here, your F75's knob may not be sending\n"
                "the VK_VOLUME_* keys this app listens for by default. Run\n"
                "tools\\f75_input_probe.py for a full diagnostic -- see\n"
                "docs\\F75_SETUP.md."
            )
        )
        self._last_value = 0.0
        self._presses = 0
        self.app.knob.on_short_press = self._on_press

    def _on_press(self) -> None:
        self._presses += 1
        self.detected.setText(f"Detected: knob press x{self._presses}, last rotation value={self.app.knob.value:.1f}")

    def initializePage(self) -> None:
        pass


class LatencyTestPage(QWizardPage):
    def __init__(self, app):
        super().__init__()
        self.app = app
        self.setTitle("4. Latency / Buffer Test")
        layout = QVBoxLayout(self)
        self.result_label = QLabel("Click Test to check current buffer settings.")
        layout.addWidget(self.result_label)
        btn = QPushButton("Test")
        btn.clicked.connect(self._run_test)
        layout.addWidget(btn)

    def _run_test(self) -> None:
        s = self.app.engine.stats
        cfg = self.app.engine.config
        self.result_label.setText(
            f"block={cfg.block_size} frames @ {cfg.sample_rate}Hz "
            f"({cfg.block_size / cfg.sample_rate * 1000:.2f} ms/callback)\n"
            f"reported latency: {s.reported_latency_s * 1000:.1f} ms\n"
            f"underruns so far: {s.underrun_count}"
        )


class TestScalePage(QWizardPage):
    def __init__(self, app):
        super().__init__()
        self.app = app
        self.setTitle("5. Test Scale")
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("Click Play to hear a C-major scale on the default synth."))
        btn = QPushButton("Play Scale")
        btn.clicked.connect(self._play)
        layout.addWidget(btn)

    def _play(self) -> None:
        engine = self.app.engine
        engine.set_active_instrument("synth_lead")
        for i, note in enumerate([60, 62, 64, 65, 67, 69, 71, 72]):
            key_id = f"wizard_scale_{i}"
            engine.submit(MusicEvent(type=EventType.NOTE_ON, note=note, velocity=0.7, timestamp_ns=time.perf_counter_ns(), source=Source.UI, metadata={"key_id": key_id}))

            def make_off(kid=key_id, n=note):
                return lambda: engine.submit(MusicEvent(type=EventType.NOTE_OFF, note=n, timestamp_ns=time.perf_counter_ns(), source=Source.UI, metadata={"key_id": kid}))

            from PySide6.QtCore import QTimer

            QTimer.singleShot(200 * (i + 1), make_off())


class FirstRunWizard(QWizard):
    def __init__(self, app, parent=None):
        super().__init__(parent)
        self.app = app
        self.setWindowTitle("QWERTY Instrument -- First-Run Setup")
        self.audio_page = AudioOutputPage(app)
        self.addPage(self.audio_page)
        self.addPage(KeyboardCapturePage(app))
        self.addPage(KnobPage(app))
        self.addPage(LatencyTestPage(app))
        self.addPage(TestScalePage(app))
        self.finished.connect(self._on_finished)

    def _on_finished(self, result: int) -> None:
        if result != QWizard.Accepted:
            return
        device = self.audio_page.selected_device()
        self.app.cfg["audio"]["device_index"] = device
        config_mod.save_config(self.app.cfg)

"""Live diagnostics panel: callback stats + a Performance Test mode
(mash-key stress counters, spec section 26) with a PANIC button."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QGroupBox, QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget


class DiagnosticsWidget(QWidget):
    def __init__(self, app, parent=None):
        super().__init__(parent)
        self.app = app
        self.note_on_count = 0
        self.note_off_count = 0
        self.max_simultaneous = 0

        layout = QVBoxLayout(self)

        stats_group = QGroupBox("Audio Callback")
        stats_layout = QVBoxLayout(stats_group)
        self.stats_label = QLabel("(waiting for audio to start)")
        stats_layout.addWidget(self.stats_label)
        layout.addWidget(stats_group)

        perf_group = QGroupBox("Performance Test")
        perf_layout = QVBoxLayout(perf_group)
        self.perf_label = QLabel("note-ons: 0   note-offs: 0   max simultaneous: 0")
        perf_layout.addWidget(self.perf_label)
        reset_btn = QPushButton("Reset counters")
        reset_btn.clicked.connect(self.reset_counters)
        perf_layout.addWidget(reset_btn)
        layout.addWidget(perf_group)

        panic_btn = QPushButton("PANIC -- ALL NOTES OFF")
        panic_btn.setStyleSheet("background-color:#8c2f2f; color:white; font-weight:bold; padding:8px;")
        panic_btn.clicked.connect(self._panic)
        layout.addWidget(panic_btn)

        layout.addStretch(1)

    def _panic(self) -> None:
        self.app.keyboard.panic()

    def reset_counters(self) -> None:
        self.note_on_count = 0
        self.note_off_count = 0
        self.max_simultaneous = 0
        self._refresh_perf_label()

    def on_feedback(self, feedback: dict) -> None:
        t = feedback.get("type")
        if t == "note_on":
            self.note_on_count += 1
        elif t == "note_off":
            self.note_off_count += 1
        elif t == "stats":
            self.max_simultaneous = max(self.max_simultaneous, feedback.get("active_notes", 0))
        self._refresh_perf_label()

    def _refresh_perf_label(self) -> None:
        self.perf_label.setText(f"note-ons: {self.note_on_count}   note-offs: {self.note_off_count}   max simultaneous: {self.max_simultaneous}")

    def refresh_stats(self) -> None:
        s = self.app.engine.stats
        budget_ms = self.app.engine.config.block_size / self.app.engine.config.sample_rate * 1000
        self.stats_label.setText(
            f"callbacks: {s.callback_count}   underruns: {s.underrun_count}   overruns: {s.overrun_count}\n"
            f"avg: {s.ema_duration_ms:.3f} ms   max: {s.max_duration_ms:.3f} ms   budget: {budget_ms:.3f} ms\n"
            f"reported latency: {s.reported_latency_s * 1000:.1f} ms"
        )

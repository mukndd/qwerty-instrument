"""Upcoming-notes display for Guided mode (spec section 19/21): a big
NEXT indicator plus a short queue of what follows."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QLabel, QVBoxLayout, QWidget

from ..music.notes import midi_to_name
from ..songs.model import ChordEvent, NoteEvent
from ..songs.trainer import UpcomingNote


class TimelineWidget(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)

        self.next_key_label = QLabel("NEXT: --")
        self.next_key_label.setAlignment(Qt.AlignCenter)
        self.next_key_label.setStyleSheet("font-size: 28px; font-weight: bold; color: #e0b84c;")
        layout.addWidget(self.next_key_label)

        self.next_note_label = QLabel("")
        self.next_note_label.setAlignment(Qt.AlignCenter)
        self.next_note_label.setStyleSheet("font-size: 18px; color: #9fd3ff;")
        layout.addWidget(self.next_note_label)

        self.upcoming_label = QLabel("")
        self.upcoming_label.setAlignment(Qt.AlignCenter)
        self.upcoming_label.setStyleSheet("font-size: 13px; color: #999;")
        layout.addWidget(self.upcoming_label)

    def update_upcoming(self, upcoming: list[UpcomingNote]) -> None:
        if not upcoming:
            self.next_key_label.setText("NEXT: --")
            self.next_note_label.setText("")
            self.upcoming_label.setText("")
            return

        first = upcoming[0]
        if isinstance(first.event, ChordEvent):
            key_text = first.key_hint or "?"
            note_text = f"{first.event.name}: " + " ".join(midi_to_name(n) for n in first.event.notes)
        else:
            key_text = first.key_hint or "?"
            note_text = midi_to_name(first.event.note)

        self.next_key_label.setText(f"NEXT: {key_text}")
        self.next_note_label.setText(note_text)

        rest = []
        for u in upcoming[1:]:
            if isinstance(u.event, ChordEvent):
                rest.append(u.key_hint or "?")
            else:
                rest.append(u.key_hint or "?")
        self.upcoming_label.setText("then: " + "  ".join(rest) if rest else "")

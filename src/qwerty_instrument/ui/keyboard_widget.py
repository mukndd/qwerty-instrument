"""Live QWERTY keyboard visualization: highlights held keys, the next
expected key (Guided mode), and wrong presses -- spec section 20.

Drawn directly with QPainter rather than one child widget per key: a
fixed ~60-key layout redrawn on state change is simpler and cheaper than
managing that many widgets, and this repaints on every note event so it
needs to stay light.
"""

from __future__ import annotations

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QFont, QPainter, QPen
from PySide6.QtWidgets import QWidget

from ..music.mapping import BLOCK1_BLACK, BLOCK1_WHITE, BLOCK2_BLACK, BLOCK2_WHITE, RESERVED_FREE_KEYS

KEY_LABELS = {
    "COMMA": ",", "PERIOD": ".", "SLASH": "/", "SEMICOLON": ";", "APOSTROPHE": "'",
    "MINUS": "-", "EQUALS": "=", "LBRACKET": "[", "RBRACKET": "]", "BACKSLASH": "\\", "GRAVE": "`",
    "SPACE": "SPACE", "ESCAPE": "ESC", "TAB": "TAB", "CAPSLOCK": "CAPS", "SHIFT": "SHIFT",
    "CTRL": "CTRL", "ALT": "ALT", "ENTER": "ENTER", "BACKSPACE": "BKSP",
    "PAGEUP": "PGUP", "PAGEDOWN": "PGDN", "HOME": "HOME", "END": "END", "INSERT": "INS", "DELETE": "DEL",
    "UP": "^", "DOWN": "v", "LEFT": "<", "RIGHT": ">",
}

# Rows approximate a 75%-layout physical arrangement, each entry (key_id, width_units).
ROW_NUMBERS = [("GRAVE", 1), *[(str(d), 1) for d in range(1, 10)], ("0", 1), ("MINUS", 1), ("EQUALS", 1), ("BACKSPACE", 2)]
ROW_QWERTY = [("TAB", 1.5), *[(k, 1) for k in "QWERTYUIOP"], ("LBRACKET", 1), ("RBRACKET", 1), ("BACKSLASH", 1.5)]
ROW_HOME = [("CAPSLOCK", 1.75), *[(k, 1) for k in "ASDFGHJKL"], ("SEMICOLON", 1), ("APOSTROPHE", 1), ("ENTER", 1.75)]
ROW_BOTTOM = [("SHIFT", 2.25), *[(k, 1) for k in "ZXCVBNM"], ("COMMA", 1), ("PERIOD", 1), ("SLASH", 1), ("SHIFT", 2.25)]
ROW_SPACE = [("CTRL", 1.5), ("ALT", 1.25), ("SPACE", 6.25), ("ALT", 1.25), ("F5", 1), ("F6", 1), ("PAGEUP", 1), ("PAGEDOWN", 1)]

ROWS = [ROW_NUMBERS, ROW_QWERTY, ROW_HOME, ROW_BOTTOM, ROW_SPACE]

_NOTE_KEYS = set(BLOCK1_WHITE) | set(BLOCK1_BLACK) | set(BLOCK2_WHITE) | set(BLOCK2_BLACK)
_FREE_KEYS = set(RESERVED_FREE_KEYS)

COLOR_BG = QColor("#1a1a1f")
COLOR_KEY_NOTE = QColor("#2a3a4a")
COLOR_KEY_CONTROL = QColor("#2a2a30")
COLOR_KEY_FREE = QColor("#222226")
COLOR_HELD = QColor("#4caf7d")
COLOR_NEXT = QColor("#e0b84c")
COLOR_WRONG = QColor("#d9534f")
COLOR_TEXT = QColor("#dcdce0")
COLOR_BORDER = QColor("#3a3a42")


class KeyboardWidget(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumHeight(200)
        self.held_keys: set[str] = set()
        self.next_keys: set[str] = set()
        self.wrong_keys: set[str] = set()
        self.note_labels: dict[str, str] = {}  # key_id -> label to show under the key letter
        self.label_mode = "qwerty_and_note"  # qwerty_only | note_only | qwerty_and_note

    def set_state(self, held: set[str] | None = None, next_keys: set[str] | None = None, wrong: set[str] | None = None) -> None:
        if held is not None:
            self.held_keys = held
        if next_keys is not None:
            self.next_keys = next_keys
        if wrong is not None:
            self.wrong_keys = wrong
        self.update()

    def flash_wrong(self, key_id: str) -> None:
        self.wrong_keys = {key_id}
        self.update()

    def set_note_labels(self, labels: dict[str, str]) -> None:
        self.note_labels = labels
        self.update()

    def paintEvent(self, event) -> None:  # noqa: N802 -- Qt override
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.fillRect(self.rect(), COLOR_BG)

        margin = 8
        row_gap = 4
        available_w = self.width() - 2 * margin
        row_h = (self.height() - 2 * margin - row_gap * (len(ROWS) - 1)) / len(ROWS)
        unit_w = available_w / 15.0  # widest row (numbers row) is ~15 units

        y = margin
        for row in ROWS:
            x = margin
            for key_id, width_units in row:
                w = width_units * unit_w
                self._draw_key(painter, x, y, w - 3, row_h - 3, key_id)
                x += w
            y += row_h + row_gap

        painter.end()

    def _draw_key(self, painter: QPainter, x: float, y: float, w: float, h: float, key_id: str) -> None:
        rect = QRectF(x, y, w, h)

        if key_id in self.wrong_keys:
            fill = COLOR_WRONG
        elif key_id in self.held_keys:
            fill = COLOR_HELD
        elif key_id in self.next_keys:
            fill = COLOR_NEXT
        elif key_id in _NOTE_KEYS:
            fill = COLOR_KEY_NOTE
        elif key_id in _FREE_KEYS:
            fill = COLOR_KEY_FREE
        else:
            fill = COLOR_KEY_CONTROL

        painter.setPen(QPen(COLOR_BORDER, 1))
        painter.setBrush(fill)
        painter.drawRoundedRect(rect, 4, 4)

        painter.setPen(COLOR_TEXT)
        label = KEY_LABELS.get(key_id, key_id)
        note_label = self.note_labels.get(key_id)

        if self.label_mode == "note_only" and note_label:
            self._draw_centered_text(painter, rect, note_label, bold=True)
        elif self.label_mode == "qwerty_and_note" and note_label:
            top_rect = QRectF(rect.x(), rect.y() + 2, rect.width(), rect.height() * 0.55)
            bottom_rect = QRectF(rect.x(), rect.y() + rect.height() * 0.5, rect.width(), rect.height() * 0.48)
            self._draw_centered_text(painter, top_rect, label, bold=False, point_size=8)
            self._draw_centered_text(painter, bottom_rect, note_label, bold=True, point_size=7, color=QColor("#9fd3ff"))
        else:
            self._draw_centered_text(painter, rect, label, bold=False)

    def _draw_centered_text(self, painter: QPainter, rect: QRectF, text: str, bold: bool, point_size: int = 8, color: QColor | None = None) -> None:
        font = QFont()
        font.setPointSize(point_size)
        font.setBold(bold)
        painter.setFont(font)
        if color is not None:
            prev_pen = painter.pen()
            painter.setPen(color)
            painter.drawText(rect, Qt.AlignCenter, text)
            painter.setPen(prev_pen)
        else:
            painter.drawText(rect, Qt.AlignCenter, text)

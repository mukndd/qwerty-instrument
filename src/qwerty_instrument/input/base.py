"""Common interface for input backends (keyboard hook, F75 knob, MIDI-in).

Every backend ultimately calls back with (key_id: str, is_down: bool,
timestamp_ns: int) for keys, or emits MusicEvent directly for
non-keyboard-shaped input (knob rotation, MIDI). Keeping this interface
narrow is what lets a MIDI keyboard later plug into the same musical
mapping/event bus without touching the song or audio engine (spec #30).
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Callable

KeyEventCallback = Callable[[str, bool, int], None]


class InputSource(ABC):
    """A source of raw key-shaped events (key_id, is_down, timestamp_ns)."""

    @abstractmethod
    def start(self, on_event: KeyEventCallback) -> None: ...

    @abstractmethod
    def stop(self) -> None: ...

    def set_capture_enabled(self, enabled: bool) -> None:
        """Override in backends where capture can be toggled without stop()."""

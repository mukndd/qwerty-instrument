"""Tests for the QWERTY orchestration logic (note lifecycle, repeat
suppression, chords/polyphony via multiple keys, sustain, panic, octave
shift, capture toggle). Uses a stub InputSource since the real Win32
low-level hook can only be exercised by physical key presses on Windows --
see docs/F75_SETUP.md's manual test checklist for that part.
"""

from qwerty_instrument.input.base import InputSource
from qwerty_instrument.input.keyboard import KeyboardInstrument
from qwerty_instrument.music.events import EventType
from qwerty_instrument.music.mapping import KeyboardMapping


class StubInputSource(InputSource):
    def __init__(self):
        self.started_callback = None
        self.capture_enabled = True

    def start(self, on_event):
        self.started_callback = on_event

    def stop(self):
        self.started_callback = None

    def set_capture_enabled(self, enabled):
        self.capture_enabled = enabled


def make_instrument():
    events = []
    src = StubInputSource()
    instrument = KeyboardInstrument(mapping=KeyboardMapping(), input_source=src, event_sink=events.append, instrument_names=["synth_lead", "guitar_lead"])
    instrument.start()
    return instrument, events


def test_keydown_produces_note_on_keyup_produces_note_off():
    instrument, events = make_instrument()
    instrument.handle_key_event("Z", True, 1000)
    instrument.handle_key_event("Z", False, 2000)
    assert [e.type for e in events] == [EventType.NOTE_ON, EventType.NOTE_OFF]
    assert events[0].note == 48  # C3


def test_os_key_repeat_does_not_retrigger():
    """Simulates what would happen if the OS sent multiple raw keydowns for
    a held key; the *hook* normally filters this, but KeyboardInstrument
    also defensively ignores a second down for an already-active key."""
    instrument, events = make_instrument()
    instrument.handle_key_event("Z", True, 1000)
    instrument.handle_key_event("Z", True, 1050)  # simulated repeat
    instrument.handle_key_event("Z", True, 1100)  # simulated repeat
    note_ons = [e for e in events if e.type == EventType.NOTE_ON]
    assert len(note_ons) == 1


def test_chord_polyphony_multiple_keys():
    instrument, events = make_instrument()
    for k in ["Z", "C", "B"]:  # C3, E3, G3
        instrument.handle_key_event(k, True, 1000)
    note_ons = [e.note for e in events if e.type == EventType.NOTE_ON]
    assert note_ons == [48, 52, 55]
    for k in ["Z", "C", "B"]:
        instrument.handle_key_event(k, False, 2000)
    assert len([e for e in events if e.type == EventType.NOTE_OFF]) == 3


def test_sustain_hold_emits_sustain_on_then_note_off_in_order():
    """KeyboardInstrument reports the physical facts (sustain engaged, key
    released) as they happen; it's the audio engine that actually defers
    voice release while sustain is on (see tests/test_engine.py)."""
    instrument, events = make_instrument()
    instrument.handle_key_event("SPACE", True, 500)  # sustain on
    instrument.handle_key_event("Z", True, 1000)
    instrument.handle_key_event("Z", False, 1500)  # key released while sustained
    types_in_order = [e.type for e in events]
    assert types_in_order == [EventType.SUSTAIN, EventType.NOTE_ON, EventType.NOTE_OFF]
    assert events[0].metadata.get("on") is True


def test_panic_key_emits_all_notes_off_and_clears_active_keys():
    instrument, events = make_instrument()
    instrument.handle_key_event("Z", True, 1000)
    instrument.handle_key_event("ESCAPE", True, 1500)
    assert events[-1].type == EventType.ALL_NOTES_OFF
    assert instrument._active_keys == {}


def test_octave_up_down_shifts_subsequent_notes():
    instrument, events = make_instrument()
    instrument.handle_key_event("PAGEUP", True, 1000)
    instrument.handle_key_event("Z", True, 2000)
    assert events[-1].note == 60  # C3 + 12 = C4


def test_capture_toggle_releases_held_notes_and_stops_new_ones():
    instrument, events = make_instrument()
    instrument.handle_key_event("Z", True, 1000)
    instrument.handle_key_event("CAPSLOCK", True, 1500)  # toggle capture off
    assert events[-1].type == EventType.ALL_NOTES_OFF
    assert instrument.capture_enabled is False
    n_events_before = len(events)
    instrument.handle_key_event("X", True, 2000)  # should be ignored while capture is off
    assert len(events) == n_events_before


def test_instrument_cycling_emits_program_change():
    instrument, events = make_instrument()
    instrument.handle_key_event("TAB", True, 1000)
    program_changes = [e for e in events if e.type == EventType.PROGRAM_CHANGE]
    assert len(program_changes) == 1
    assert program_changes[0].metadata["instrument"] == "guitar_lead"

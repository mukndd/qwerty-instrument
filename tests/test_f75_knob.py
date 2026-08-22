import time

from qwerty_instrument.input.f75 import F75Knob, KnobConfig
from qwerty_instrument.music.events import EventType


def make_knob(mode="pitch_bend", spring_return=False, **kwargs):
    events = []
    cfg = KnobConfig(mode=mode, spring_return=spring_return, **kwargs)
    knob = F75Knob(event_sink=events.append, config=cfg)
    return knob, events


def test_volume_up_down_recognized_as_knob_rotation():
    knob, events = make_knob()
    assert knob.handle_key("VOLUME_UP", True, 1000) is True
    assert knob.handle_key("VOLUME_DOWN", True, 1000) is True
    assert knob.handle_key("Z", True, 1000) is False  # not a knob key


def test_clockwise_increases_pitch_bend_cents():
    knob, events = make_knob(mode="pitch_bend", bend_step_cents=25.0)
    knob.handle_key("VOLUME_UP", True, time.perf_counter_ns())
    assert events[-1].type == EventType.PITCH_BEND
    assert events[-1].metadata["cents"] == 25.0


def test_counter_clockwise_decreases_pitch_bend_cents():
    knob, events = make_knob(mode="pitch_bend", bend_step_cents=25.0)
    knob.handle_key("VOLUME_DOWN", True, time.perf_counter_ns())
    assert events[-1].metadata["cents"] == -25.0


def test_bend_clamps_to_max_range():
    knob, events = make_knob(mode="pitch_bend", bend_step_cents=50.0, max_bend_cents=100.0)
    for _ in range(10):
        knob.handle_key("VOLUME_UP", True, time.perf_counter_ns())
    assert knob.value == 100.0


def test_expression_mode_stays_normalized_0_to_1():
    knob, events = make_knob(mode="expression", expression_step=0.3)
    for _ in range(10):
        knob.handle_key("VOLUME_UP", True, time.perf_counter_ns())
    assert knob.value == 1.0
    assert events[-1].type == EventType.EXPRESSION
    assert 0.0 <= events[-1].metadata["normalized"] <= 1.0


def test_short_press_triggers_callback():
    fired = []
    events = []
    knob = F75Knob(event_sink=events.append, config=KnobConfig(short_press_max_s=0.5), on_short_press=lambda: fired.append(True))
    knob.handle_key("VOLUME_MUTE", True, time.perf_counter_ns())
    knob.handle_key("VOLUME_MUTE", False, time.perf_counter_ns())
    assert fired == [True]


def test_long_press_does_not_trigger_short_press_callback():
    fired = []
    events = []
    knob = F75Knob(event_sink=events.append, config=KnobConfig(short_press_max_s=0.05), on_short_press=lambda: fired.append(True))
    knob.handle_key("VOLUME_MUTE", True, time.perf_counter_ns())
    time.sleep(0.1)
    knob.handle_key("VOLUME_MUTE", False, time.perf_counter_ns())
    assert fired == []

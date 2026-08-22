import math

from qwerty_instrument.music.timing import PracticeClock, TempoEvent, TempoMap


def test_constant_tempo_beats_to_seconds():
    tm = TempoMap(events=[TempoEvent(beat=0.0, bpm=120.0)])
    assert math.isclose(tm.beats_to_seconds(4.0), 2.0, rel_tol=1e-9)  # 4 beats @ 120bpm = 2s


def test_seconds_to_beats_is_inverse_of_beats_to_seconds():
    tm = TempoMap(events=[TempoEvent(beat=0.0, bpm=100.0)])
    for beat in [0.0, 1.0, 5.5, 20.0]:
        seconds = tm.beats_to_seconds(beat)
        assert math.isclose(tm.seconds_to_beats(seconds), beat, rel_tol=1e-9, abs_tol=1e-9)


def test_tempo_change_mid_song():
    tm = TempoMap(events=[TempoEvent(beat=0.0, bpm=120.0), TempoEvent(beat=8.0, bpm=60.0)])
    t_at_8 = tm.beats_to_seconds(8.0)
    assert math.isclose(t_at_8, 4.0, rel_tol=1e-9)  # 8 beats @ 120bpm = 4s
    t_at_10 = tm.beats_to_seconds(10.0)
    assert math.isclose(t_at_10, 4.0 + 2 * (60.0 / 60.0), rel_tol=1e-9)  # +2 beats @ 60bpm = +2s
    assert math.isclose(tm.seconds_to_beats(t_at_10), 10.0, rel_tol=1e-6)


def test_practice_clock_speed_scaling():
    tm = TempoMap(events=[TempoEvent(beat=0.0, bpm=120.0)])
    clock = PracticeClock(tm, speed=1.0)
    full_speed_seconds = clock.beats_to_seconds(4.0)
    clock.set_speed_percent(50)
    half_speed_seconds = clock.beats_to_seconds(4.0)
    assert math.isclose(half_speed_seconds, full_speed_seconds * 2, rel_tol=1e-9)


def test_bar_beat_conversion():
    tm = TempoMap(time_signature=(4, 4))
    bar, beat = tm.beat_to_bar_beat(0.0)
    assert (bar, beat) == (1, 1.0)
    bar, beat = tm.beat_to_bar_beat(4.0)
    assert (bar, beat) == (2, 1.0)
    bar, beat = tm.beat_to_bar_beat(5.5)
    assert (bar, beat) == (2, 2.5)

import math

from qwerty_instrument.music.notes import clamp_midi, midi_to_freq, midi_to_name, name_to_midi


def test_a4_is_440hz():
    assert math.isclose(midi_to_freq(69), 440.0, rel_tol=1e-9)


def test_octave_doubles_frequency():
    assert math.isclose(midi_to_freq(81), midi_to_freq(69) * 2, rel_tol=1e-9)


def test_bend_cents_shifts_pitch():
    freq_up = midi_to_freq(69, bend_cents=100)  # +1 semitone
    assert math.isclose(freq_up, midi_to_freq(70), rel_tol=1e-6)


def test_name_roundtrip():
    assert midi_to_name(60) == "C4"
    assert name_to_midi("C4") == 60
    assert name_to_midi("C#4") == 61
    assert name_to_midi("Db4") == 61


def test_clamp():
    assert clamp_midi(-5) == 0
    assert clamp_midi(200) == 127

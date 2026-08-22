from qwerty_instrument.music.mapping import KeyboardMapping, default_note_map


def test_default_map_matches_piano_black_white_pattern():
    m = default_note_map()
    assert m["Z"] == 48  # C3
    assert m["S"] == 49  # C#3
    assert m["X"] == 50  # D3
    assert m["C"] == 52  # E3
    assert m["V"] == 53  # F3 -- no black key between E and F
    assert "F" not in m  # the gap key is left free, not assigned E-F#
    assert m["M"] == 59  # B3
    assert "K" not in m  # no black key between B and C
    assert m["COMMA"] == 60  # C4


def test_block2_is_one_octave_above_block1_start():
    m = default_note_map()
    assert m["Q"] == m["COMMA"]  # both C4
    assert m["Q"] - m["Z"] == 12


def test_reserved_keys_are_free():
    m = default_note_map()
    for k in ["A", "F", "K", "APOSTROPHE", "1", "4", "8"]:
        assert k not in m


def test_octave_shift_clamps_and_transposes():
    mapping = KeyboardMapping()
    base = mapping.resolve_note("Z")
    mapping.shift_octave(1)
    assert mapping.resolve_note("Z") == base + 12
    for _ in range(10):
        mapping.shift_octave(1)
    assert mapping.octave_shift == mapping.max_octave_shift


def test_unmapped_key_resolves_to_none():
    mapping = KeyboardMapping()
    assert mapping.resolve_note("F1") is None


def test_find_key_for_note_prefers_closest_octave_shift():
    mapping = KeyboardMapping()
    key_id, shift = mapping.find_key_for_note(48)  # C3, exact base
    assert shift == 0
    mapping.octave_shift = 1
    # C4 is reachable at shift 0 (via 'Q'/'COMMA') and shift -1 isn't needed;
    # since current shift is 1, the closest valid shift to reach 48 (C3) is 0.
    key_id, shift = mapping.find_key_for_note(48)
    assert shift == 0
    assert mapping.note_map[key_id] == 48


def test_control_overrides_from_config():
    mapping = KeyboardMapping.from_config({"control_overrides": {"F7": "panic"}})
    assert mapping.resolve_control("F7") == "panic"
    assert mapping.resolve_control("ESCAPE") == "panic"  # default retained

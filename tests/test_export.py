from qwerty_instrument.music.mapping import KeyboardMapping
from qwerty_instrument.songs.export import export_keymap_json, export_keymap_txt, generate_practice_sheet, write_all_exports
from qwerty_instrument.songs.model import ChordEvent, NoteEvent, NoteVerification, Section, Song
from qwerty_instrument.music.timing import TempoMap


def make_song_with_notes():
    section = Section(id="verse", name="Verse", start_beat=0, end_beat=8, difficulty="easy")
    section.notes = [
        NoteEvent(beat=0.0, duration_beats=1.0, note=48, layer="melody", verification=NoteVerification.VERIFIED),  # Z
        NoteEvent(beat=1.0, duration_beats=1.0, note=50, layer="melody", verification=NoteVerification.VERIFIED),  # X
        ChordEvent(beat=0.0, duration_beats=4.0, name="Cmaj", notes=[48, 52, 55], layer="chords", verification=NoteVerification.VERIFIED),
    ]
    return Song(title="Test Song", artist="Nobody", tempo_map=TempoMap(events=None), sections=[section])


def test_practice_sheet_contains_real_keys_not_placeholders():
    song = make_song_with_notes()
    mapping = KeyboardMapping()
    sheet = generate_practice_sheet(song, mapping)
    assert "Z(C3)" in sheet
    assert "X(D3)" in sheet
    assert "[Z+C+B]" in sheet  # C3+E3+G3 chord
    assert "verified" in sheet.lower()


def test_practice_sheet_marks_empty_section_as_placeholder():
    section = Section(id="chorus", name="Chorus", start_beat=0, end_beat=8)
    song = Song(title="Empty Song", artist="?", tempo_map=TempoMap(events=None), sections=[section], notes_disclaimer="Nothing verified yet.")
    sheet = generate_practice_sheet(song, KeyboardMapping())
    assert "No verified notes" in sheet
    assert "Nothing verified yet." in sheet


def test_keymap_json_and_txt_export():
    mapping = KeyboardMapping()
    data = export_keymap_json(mapping)
    assert data["notes"]["Z"]["midi"] == 48
    assert data["notes"]["Z"]["name"] == "C3"
    txt = export_keymap_txt(mapping)
    assert "Z" in txt and "C3" in txt


def test_write_all_exports_creates_files(tmp_path):
    song = make_song_with_notes()
    mapping = KeyboardMapping()
    paths = write_all_exports(song, mapping, tmp_path, filename_stem="test_song")
    assert paths["practice_sheet"].exists()
    assert paths["keymap_json"].exists()
    assert paths["keymap_txt"].exists()
    assert "Z(C3)" in paths["practice_sheet"].read_text(encoding="utf-8")

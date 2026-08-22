from qwerty_instrument.songs.model import NoteEvent
from qwerty_instrument.songs.scoring import Judgement, ScoringEngine, TimingWindows


def make_engine():
    return ScoringEngine(TimingWindows(perfect_ms=40, good_ms=90, late_ms=150), total_expected=4)


def test_perfect_hit():
    eng = make_engine()
    note = NoteEvent(beat=0.0, duration_beats=1.0, note=60)
    result = eng.judge_hit(note, expected_time_s=10.0, played_note=60, played_time_s=10.01)
    assert result.judgement == Judgement.PERFECT
    assert eng.state.combo == 1


def test_wrong_note_breaks_combo():
    eng = make_engine()
    note = NoteEvent(beat=0.0, duration_beats=1.0, note=60)
    eng.judge_hit(note, 10.0, 60, 10.0)
    assert eng.state.combo == 1
    eng.judge_hit(note, 11.0, 61, 11.0)
    assert eng.state.combo == 0
    assert eng.state.wrong_note == 1


def test_late_and_early_classification():
    eng = make_engine()
    note = NoteEvent(beat=0.0, duration_beats=1.0, note=60)
    early = eng.judge_hit(note, 10.0, 60, 9.90)  # -100ms
    assert early.judgement == Judgement.EARLY
    late = eng.judge_hit(note, 10.0, 60, 10.10)  # +100ms
    assert late.judgement == Judgement.LATE


def test_beyond_late_window_is_miss():
    eng = make_engine()
    note = NoteEvent(beat=0.0, duration_beats=1.0, note=60)
    result = eng.judge_hit(note, 10.0, 60, 10.5)  # +500ms
    assert result.judgement == Judgement.MISS
    assert eng.state.combo == 0


def test_judge_miss_direct():
    eng = make_engine()
    note = NoteEvent(beat=0.0, duration_beats=1.0, note=60)
    eng.judge_hit(note, 10.0, 60, 10.0)
    eng.judge_miss(NoteEvent(beat=1.0, duration_beats=1.0, note=62))
    assert eng.state.miss == 1
    assert eng.state.combo == 0


def test_accuracy_percentages():
    eng = make_engine()
    n = NoteEvent(beat=0.0, duration_beats=1.0, note=60)
    eng.judge_hit(n, 10.0, 60, 10.0)  # perfect
    eng.judge_hit(n, 11.0, 60, 11.0)  # perfect
    eng.judge_miss(n)
    eng.judge_miss(n)
    assert eng.state.resolved == 4
    assert eng.state.timing_accuracy_percent() == 50.0
    assert eng.state.completion_percent() == 100.0

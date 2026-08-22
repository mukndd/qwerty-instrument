import time
import wave

import numpy as np

from qwerty_instrument.persistence.recording import AudioRecorder, PerformanceRecorder
from qwerty_instrument.persistence.sessions import SessionRecord, best_accuracy, practice_history, record_session


def test_session_record_roundtrip(tmp_path):
    db = tmp_path / "sessions.sqlite3"
    record_session(db, SessionRecord(date_iso="2026-08-22", song="test_song", section="chorus", mode="guided", speed_percent=80, duration_seconds=45.0, pitch_accuracy_percent=92.5))
    history = practice_history(db, song="test_song")
    assert len(history) == 1
    assert history[0]["pitch_accuracy_percent"] == 92.5


def test_best_accuracy_across_sessions(tmp_path):
    db = tmp_path / "sessions.sqlite3"
    for acc in [70.0, 95.0, 82.0]:
        record_session(db, SessionRecord(date_iso="2026-08-22", song="s", section="c", mode="real", speed_percent=100, duration_seconds=10, pitch_accuracy_percent=acc))
    assert best_accuracy(db, "s", "c") == 95.0


def test_best_accuracy_missing_db_returns_none(tmp_path):
    assert best_accuracy(tmp_path / "does_not_exist.sqlite3", "s", "c") is None


def test_audio_recorder_writes_valid_wav(tmp_path):
    rec = AudioRecorder(sample_rate=48000, channels=2)
    out_path = tmp_path / "test.wav"
    q = rec.start(out_path)
    block = np.zeros((256, 2), dtype=np.float32)
    block[:, 0] = 0.5
    for _ in range(4):
        q.put(block)
    time.sleep(0.3)
    rec.stop()

    with wave.open(str(out_path), "rb") as wf:
        assert wf.getnchannels() == 2
        assert wf.getframerate() == 48000
        assert wf.getnframes() == 256 * 4


def test_performance_recorder_captures_and_saves(tmp_path):
    rec = PerformanceRecorder()
    rec.on_feedback({"type": "note_on", "note": 60})
    rec.on_feedback({"type": "note_off", "note": 60})
    out_path = tmp_path / "performance.json"
    rec.save(out_path)
    assert out_path.exists()
    import json

    data = json.loads(out_path.read_text())
    assert len(data) == 2
    assert data[0]["type"] == "note_on"
    assert "t" in data[0]

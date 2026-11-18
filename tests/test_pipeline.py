import asyncio
import io
import time
import wave

import numpy as np
import pytest
from fastapi.testclient import TestClient

from backend import main
from backend.asr import clean_segments
from backend.audio import Chunk, Chunker, normalized_pcm, write_wav
from backend.schemas import Settings
from backend.service import TranscriptionService
from backend.store import Store, markdown


def wav_bytes(duration=1.3, rate=16000, channels=1):
    pcm = (np.sin(np.arange(int(duration * rate)) * 2 * np.pi * 440 / rate) * 5000).astype("<i2")
    if channels == 2:
        pcm = np.column_stack((pcm, pcm)).ravel()
    output = io.BytesIO()
    with wave.open(output, "wb") as audio:
        audio.setparams((channels, 2, rate, 0, "NONE", "not compressed"))
        audio.writeframes(pcm.tobytes())
    return output.getvalue()


def test_chunker_preserves_tail_and_context_without_missing_samples(tmp_path):
    chunker = Chunker(tmp_path)
    jobs = []
    frame = (np.ones(1600) * 3000).astype("<i2").tobytes()
    for _ in range(253):
        if chunk := chunker.feed(frame):
            jobs.append(chunk)
    jobs.append(chunker.finish())
    assert len(jobs) == 2
    assert jobs[0].offset == 0
    assert jobs[0].end == 24
    assert jobs[1].offset == 23
    assert jobs[1].cutoff == 24
    assert jobs[1].end == 25.3
    with wave.open(str(jobs[1].path)) as audio:
        assert audio.getnframes() == 36800
    assert chunker.finish() is None


def test_pause_boundary_and_no_overlap_only_tail(tmp_path):
    chunker = Chunker(tmp_path)
    result = None
    for _ in range(80):
        result = chunker.feed(bytes(3200))
    assert result is not None
    assert result.end == 8
    assert chunker.finish() is None


def test_word_overlap_trimming_and_confidence(tmp_path):
    chunk = Chunk(tmp_path / "audio.wav", offset=23, cutoff=24, end=26)
    raw = [
        {
            "start": 0,
            "end": 7,
            "text": "旧内容新的知识",
            "avg_logprob": -0.9,
            "words": [{"start": 0, "end": 0.8, "word": "旧内容"}, {"start": 1, "end": 3, "word": "新的知识"}],
        }
    ]
    assert clean_segments(raw, chunk) == [{"start": 24, "end": 26, "text": "新的知识", "review": True}]


def test_decode_resamples_stereo_44100_to_mono_16000(tmp_path):
    source = tmp_path / "stereo.wav"
    source.write_bytes(wav_bytes(duration=1.3, rate=44100, channels=2))
    pcm = b"".join(normalized_pcm(source))
    assert abs(len(pcm) // 2 - 20800) <= 1
    normalized = tmp_path / "normalized.wav"
    write_wav(normalized, pcm)
    with wave.open(str(normalized)) as audio:
        assert (audio.getnchannels(), audio.getframerate(), audio.getsampwidth()) == (1, 16000, 2)


def test_database_restart_marks_running_records_interrupted(tmp_path):
    store = Store(tmp_path)
    sid = store.create(Settings(title="课程"), "system")
    store.update(sid, status="recording", duration=8)
    restarted = Store(tmp_path)
    assert restarted.get(sid)["status"] == "interrupted"
    assert restarted.get(sid)["duration"] == 8
    with pytest.raises(ValueError):
        store.audio_path("../../secret")


class FixtureRecognizer:
    def __init__(self):
        self.state = {"status": "ready", "message": "fixture", "engine": "faster-whisper", "model": "small"}

    def prepare(self, settings):
        return "faster-whisper"

    def transcribe(self, chunk, settings):
        with wave.open(str(chunk.path)) as audio:
            assert audio.getnframes() > 0
        return [{"start": chunk.cutoff, "end": chunk.end, "text": "课程内容 Test.", "review": True}]


@pytest.fixture
def client(tmp_path, monkeypatch):
    store = Store(tmp_path / "records")
    service = TranscriptionService(store)
    service.recognizer = FixtureRecognizer()
    monkeypatch.setattr(main, "store", store)
    monkeypatch.setattr(main, "service", service)
    monkeypatch.setattr(main, "prepare_task", None)
    monkeypatch.setattr(main, "DATA", tmp_path)
    with TestClient(main.app) as client:
        yield client, store, service


def wait_completed(client, sid):
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        session = client.get(f"/api/sessions/{sid}").json()
        if session["status"] in {"completed", "failed"}:
            return session
        time.sleep(0.01)
    pytest.fail("Session did not complete")


def test_upload_transcribe_edit_export_and_audio_roundtrip(client):
    browser, store, service = client
    response = browser.post(
        "/api/sessions/upload",
        data={"settings": Settings(title="中英双语课程", model="small").model_dump_json()},
        files={"file": ("lesson.wav", wav_bytes(25.3), "audio/wav")},
    )
    assert response.status_code == 201
    sid = response.json()["id"]
    session = wait_completed(browser, sid)
    assert session["status"] == "completed"
    assert session["duration"] == pytest.approx(25.3)
    assert session["processed"] == pytest.approx(25.3)
    assert len(session["segments"]) == 2
    assert browser.get("/api/sessions").json()[0]["segment_count"] == 2
    segment_id = session["segments"][0]["id"]
    assert (
        browser.patch(
            f"/api/sessions/{sid}/segments/{segment_id}", json={"text": "已校对 FastAPI"}
        ).status_code
        == 200
    )
    edited = browser.get(f"/api/sessions/{sid}").json()
    assert edited["segments"][0]["review"] == 0
    assert edited["segments"][0]["original_text"] == "课程内容 Test."
    export = browser.get(f"/api/sessions/{sid}/markdown")
    assert export.status_code == 200
    assert "已校对 FastAPI" in export.text
    assert "### 00:00:24" in export.text
    assert "filename*=UTF-8''" in export.headers["content-disposition"]
    recording = browser.get(f"/api/sessions/{sid}/audio")
    assert recording.status_code == 200
    with wave.open(io.BytesIO(recording.content)) as audio:
        assert audio.getnframes() == 404800
    assert browser.get(f"/api/sessions/{sid}/audio", headers={"Range": "bytes=0-43"}).status_code == 206
    assert not list(store.root.glob("*/chunk-*.wav"))


def test_upload_invalid_audio_fails_with_error_instead_of_silent_success(client):
    browser, _, _ = client
    response = browser.post(
        "/api/sessions/upload",
        data={"settings": Settings().model_dump_json()},
        files={"file": ("invalid.mp3", b"this is not audio", "audio/mpeg")},
    )
    session = wait_completed(browser, response.json()["id"])
    assert session["status"] == "failed"
    assert session["error"]


def test_invalid_settings_and_empty_upload(client):
    browser, _, _ = client
    assert browser.post("/api/sessions", json={"model": "invalid"}).status_code == 422
    assert (
        browser.post(
            "/api/sessions/upload", data={"settings": "{}"}, files={"file": ("empty.wav", b"", "audio/wav")}
        ).status_code
        == 400
    )
    assert (
        browser.post(
            "/api/sessions/upload",
            data={"settings": "not-json"},
            files={"file": ("file.wav", b"not-empty", "audio/wav")},
        ).status_code
        == 422
    )


def test_unknown_session_and_segment(client):
    browser, store, _ = client
    assert browser.get("/api/sessions/missing").status_code == 404
    sid = store.create(Settings(), "file")
    assert browser.patch(f"/api/sessions/{sid}/segments/999", json={"text": "test"}).status_code == 404
    assert browser.get(f"/api/sessions/{sid}/audio").status_code == 409


def test_refuse_cross_origin_recording_and_host_rebinding(client):
    browser, _, _ = client
    assert (
        browser.post("/api/sessions", json={}, headers={"Origin": "https://evil.example"}).status_code == 403
    )
    assert browser.get("/api/status", headers={"Host": "evil.example"}).status_code == 403
    assert (
        browser.get(
            "/api/status", headers={"Host": "127.0.0.1:8765", "Origin": "http://127.0.0.1:8765"}
        ).status_code
        == 200
    )


def test_markdown_preserves_uncertainty_and_formats_time(tmp_path):
    store = Store(tmp_path)
    sid = store.create(Settings(title="Lecture"), "file")
    store.add_segments(sid, [{"start": 3661, "end": 3663, "text": "中文 English", "review": True}])
    exported = markdown(store.get(sid))
    assert "### 01:01:01（待校对）" in exported
    assert "中文 English" in exported


def test_stopping_capture_drains_last_partial_chunk(tmp_path, monkeypatch):
    async def run():
        store = Store(tmp_path)
        service = TranscriptionService(store)
        service.recognizer = FixtureRecognizer()

        async def capture(sid, queue):
            store.update(sid, status="recording")
            chunker = Chunker(tmp_path / sid)
            with wave.open(io.BytesIO(wav_bytes(1.3))) as audio:
                pcm = audio.readframes(audio.getnframes())
            chunker.feed(pcm)
            write_wav(store.audio_path(sid), pcm)
            store.update(sid, duration=1.3)
            service.stop()
            queue.put_nowait(chunker.finish())

        monkeypatch.setattr(service, "capture_system", capture)
        monkeypatch.setattr("backend.service.CAPTURE_BINARY", tmp_path)
        monkeypatch.setattr("backend.service.platform.system", lambda: "Darwin")
        sid = service.launch(Settings(), "system")
        with pytest.raises(ValueError):
            service.launch(Settings(), "system")
        await service.task
        record = store.get(sid)
        assert record["status"] == "completed"
        assert record["processed"] == 1.3
        assert record["segments"][0]["text"] == "课程内容 Test."
        await service.close()

    asyncio.run(run())


def test_stop_while_preparing_does_not_start_capture(tmp_path):
    async def run():
        store = Store(tmp_path)
        service = TranscriptionService(store)
        service.recognizer = FixtureRecognizer()
        sid = service.launch(Settings(), "file", uploaded=tmp_path / "unused")
        service.stop()
        await service.task
        record = store.get(sid)
        assert record["status"] == "completed"
        assert record["duration"] == 0
        assert record["segments"] == []
        await service.close()

    asyncio.run(run())

import asyncio
import io
import struct
import time
import wave
import zipfile
import zlib

import numpy as np
import pytest
from fastapi.testclient import TestClient

from backend import main
from backend.asr import FASTER_WHISPER_MODELS, MLX_MODELS, cached_model_path, cached_models, clean_segments
from backend.audio import Chunk, Chunker
from backend.schemas import Settings
from backend.service import TranscriptionService
from backend.store import Store, markdown


def wav_bytes(duration=1.3, rate=16000):
    pcm = (np.sin(np.arange(int(duration * rate)) * 2 * np.pi * 440 / rate) * 5000).astype("<i2")
    output = io.BytesIO()
    with wave.open(output, "wb") as audio:
        audio.setparams((1, 2, rate, 0, "NONE", "not compressed"))
        audio.writeframes(pcm.tobytes())
    return output.getvalue()


def png_bytes():
    def chunk(kind, payload):
        return struct.pack(">I", len(payload)) + kind + payload + struct.pack(">I", zlib.crc32(kind + payload))

    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", 2, 1, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(b"\x00\x42\x63\xeb\xff\xff\xff")) + chunk(b"IEND", b""))


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


@pytest.mark.parametrize("state", ["preparing", "recording", "pausing", "paused", "resuming", "stopping"])
def test_database_restart_marks_running_records_interrupted(tmp_path, state):
    store = Store(tmp_path)
    sid = store.create(Settings(title="课程"))
    store.update(sid, status=state, duration=8)
    restarted = Store(tmp_path)
    assert restarted.get(sid)["status"] == "interrupted"
    assert restarted.get(sid)["duration"] == 8


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
    monkeypatch.setattr("backend.service.CAPTURE_BINARY", tmp_path)
    monkeypatch.setattr("backend.service.platform.system", lambda: "Darwin")

    async def capture(sid, queue):
        store.update(sid, status="recording")
        chunker = Chunker(store.root / sid)
        with wave.open(io.BytesIO(wav_bytes(25.3))) as audio:
            pcm = audio.readframes(audio.getnframes())
        for offset in range(0, len(pcm), 3200):
            if chunk := chunker.feed(pcm[offset : offset + 3200]):
                queue.put_nowait(chunk)
        if chunk := chunker.finish():
            queue.put_nowait(chunk)
        store.update(sid, duration=chunker.total / 16000)

    monkeypatch.setattr(service, "capture_system", capture)
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


def test_system_capture_transcribe_edit_and_export_without_original_audio(client):
    browser, store, service = client
    response = browser.post(
        "/api/sessions",
        json=Settings(title="中英双语课程", model="small").model_dump(),
    )
    assert response.status_code == 201
    sid = response.json()["id"]
    session = wait_completed(browser, sid)
    assert session["status"] == "completed"
    assert session["source"] == "system"
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
    assert "### 00:00:24" not in export.text
    assert "### " not in export.text
    assert "filename*=UTF-8''" in export.headers["content-disposition"]
    assert browser.get(f"/api/sessions/{sid}/audio").status_code == 404
    assert "/api/sessions/{sid}/audio" not in browser.get("/openapi.json").json()["paths"]
    assert not list(store.root.glob("*/*.wav"))


def test_media_upload_is_not_available(client):
    browser, _, _ = client
    assert "/api/sessions/upload" not in browser.get("/openapi.json").json()["paths"]
    response = browser.post("/api/sessions/upload", files={"file": ("lesson.wav", wav_bytes(), "audio/wav")})
    assert response.status_code in {404, 405}
    assert browser.get("/api/sessions").json() == []


def test_markdown_edits_persist_exactly_without_changing_transcript(client):
    browser, store, _ = client
    sid = store.create(Settings(title="可编辑文稿"))
    store.add_segments(sid, [{"start": 0, "end": 1, "text": "原始转写", "review": True}])
    original = store.get(sid)["segments"]
    assert "原始转写" in browser.get(f"/api/sessions/{sid}/markdown").text
    for content in ["# 自定义标题\n\n- **已修改** English words\n\n", ""]:
        response = browser.patch(f"/api/sessions/{sid}/markdown", json={"content": content})
        assert response.status_code == 200
        assert response.json()["title"] == "自定义标题"
        assert browser.get("/api/sessions").json()[0]["title"] == "自定义标题"
        assert browser.get(f"/api/sessions/{sid}").json()["markdown_content"] == content
        assert browser.get(f"/api/sessions/{sid}/markdown").text == content
        assert Store(store.root).get(sid)["markdown_content"] == content
        assert store.get(sid)["segments"] == original
    assert browser.patch("/api/sessions/missing/markdown", json={"content": "x"}).status_code == 404
    assert browser.patch(f"/api/sessions/{sid}/markdown", json={"content": None}).status_code == 422


def test_history_name_uses_existing_markdown_heading_and_keeps_name_without_heading(client):
    browser, store, _ = client
    sid = store.create(Settings(title="学习记录 2026/11/19"))
    # A document saved before title synchronization was introduced.
    content = "\n  # 测试学习记录 2026/11/19 ##\n\n正文"
    with store.connect() as db:
        db.execute("UPDATE sessions SET markdown_content=? WHERE id=?", (content, sid))
    assert browser.get("/api/sessions").json()[0]["title"] == "测试学习记录 2026/11/19"
    assert browser.get(f"/api/sessions/{sid}").json()["title"] == "测试学习记录 2026/11/19"
    response = browser.patch(f"/api/sessions/{sid}/markdown", json={"content": "# 新名称\n\n正文"})
    assert response.json()["title"] == "新名称"
    response = browser.patch(f"/api/sessions/{sid}/markdown", json={"content": "## 正文小节\n无一级标题"})
    assert response.json()["title"] == "新名称"
    assert Store(store.root).get(sid)["title"] == "新名称"


def test_existing_database_adds_markdown_column_without_losing_records(tmp_path):
    import sqlite3

    store = Store(tmp_path)
    sid = store.create(Settings(title="旧记录"))
    with sqlite3.connect(store.path) as db:
        db.execute("ALTER TABLE sessions DROP COLUMN markdown_content")
    migrated = Store(tmp_path)
    assert migrated.get(sid)["title"] == "旧记录"
    assert migrated.get(sid)["markdown_content"] is None
    assert "# 旧记录" in markdown(migrated.get(sid))


def test_invalid_settings(client):
    browser, _, _ = client
    assert browser.post("/api/sessions", json={"model": "invalid"}).status_code == 422
    assert browser.post("/api/sessions", json={"audio_source": "invalid"}).status_code == 422


def test_polish_apply_undo_preserves_original_transcript_photos_and_export(client):
    browser, store, _ = client
    sid = store.create(Settings(title="AI 润色验证"))
    store.update(sid, status="completed", duration=12, processed=12)
    store.add_segments(sid, [{"start": 0, "end": 3, "text": "这个就是前面的内容。", "review": False}])
    image = png_bytes()
    uploaded = browser.post(f"/api/sessions/{sid}/screenshots?at=4", content=image,
                            headers={"Content-Type": "image/png"})
    assert uploaded.status_code == 201
    picture = uploaded.json()["segments"][-1]
    store.add_segments(sid, [{"start": 6, "end": 9, "text": "然后这个就是后面的内容。", "review": False}])
    before = store.get(sid)
    original = browser.get(f"/api/sessions/{sid}/markdown").text
    polished = original.replace("这个就是前面的内容。", "这是前面的内容。").replace("然后这个就是后面的内容。", "这是后面的内容。")
    applied = browser.post(f"/api/sessions/{sid}/polish/apply", json={"content": polished, "base_content": original})
    assert applied.status_code == 200
    revision = applied.json()["polish_revision"]
    assert revision and applied.json()["markdown_content"] == polished
    assert applied.json()["segments"] == before["segments"]
    assert browser.get(f"/api/sessions/{sid}/markdown").text == polished
    filename = picture["photo_filename"]
    assert browser.get(f"/api/sessions/{sid}/photo/{filename}").content == image
    assert Store(store.root).get(sid)["polish_revision"] == revision
    undone = browser.post(f"/api/sessions/{sid}/polish/undo", json={"revision_id": revision, "base_content": polished})
    assert undone.status_code == 200 and undone.json()["markdown_content"] == original
    assert undone.json()["segments"] == before["segments"] and undone.json()["polish_revision"] is None
    assert browser.get(f"/api/sessions/{sid}/photo/{filename}").content == image
    assert browser.post(f"/api/sessions/{sid}/polish/undo", json={"revision_id": revision, "base_content": original}).status_code == 409


def test_polish_accepts_changed_photo_references_and_still_preserves_parallel_edits(client):
    browser, store, _ = client
    sid = store.create(Settings())
    store.update(sid, status="completed")
    original = "# 标题\n\n正文。\n\n![图](photo/original.png)\n"
    store.save_markdown(sid, original)
    accepted = browser.post(f"/api/sessions/{sid}/polish/apply", json={"content": "# 自由结果", "base_content": original})
    assert accepted.status_code == 200
    assert browser.post(f"/api/sessions/{sid}/polish/undo", json={
        "revision_id": accepted.json()["polish_revision"], "base_content": "# 自由结果",
    }).status_code == 200
    polished = original.replace("正文。", "润色正文。")
    revision = browser.post(f"/api/sessions/{sid}/polish/apply", json={"content": polished, "base_content": original}).json()["polish_revision"]
    store.save_markdown(sid, polished + "\n手动新内容。")
    assert browser.post(f"/api/sessions/{sid}/polish/apply", json={"content": polished, "base_content": original}).status_code == 409
    assert browser.post(f"/api/sessions/{sid}/polish/undo", json={"revision_id": revision, "base_content": polished}).status_code == 409
    assert store.get(sid)["markdown_content"].endswith("手动新内容。")
    assert store.get(sid)["polish_revision"] is None
    assert browser.post("/api/sessions/missing/polish/apply", json={"content": "x", "base_content": ""}).status_code == 404


@pytest.mark.parametrize("state", ["recording", "paused", "preparing", "stopping"])
def test_polish_cannot_apply_while_transcribing(client, state):
    browser, store, _ = client
    sid = store.create(Settings())
    store.update(sid, status=state)
    original = browser.get(f"/api/sessions/{sid}/markdown").text
    assert browser.post(f"/api/sessions/{sid}/polish/apply", json={"content": original + "x", "base_content": original}).status_code == 409
    assert store.get(sid)["markdown_content"] is None


def test_polish_history_is_removed_with_the_record(client):
    browser, store, _ = client
    sid = store.create(Settings())
    store.update(sid, status="completed")
    store.save_markdown(sid, "# 原文\n\n正文。")
    assert browser.post(f"/api/sessions/{sid}/polish/apply", json={"base_content": "# 原文\n\n正文。", "content": "# 原文\n\n润色正文。"}).status_code == 200
    assert browser.delete(f"/api/sessions/{sid}").status_code == 200
    with store.connect() as db:
        assert db.execute("SELECT COUNT(*) FROM markdown_polishes WHERE session_id=?", (sid,)).fetchone()[0] == 0


@pytest.mark.parametrize("source", ["microphone", "system", "mixed"])
def test_audio_source_is_saved_and_returned_for_new_records(client, source):
    browser, store, _ = client
    response = browser.post("/api/sessions", json={"audio_source": source, "model": "small"})
    assert response.status_code == 201
    sid = response.json()["id"]
    result = wait_completed(browser, sid)
    assert result["audio_source"] == source
    assert result["source"] == source
    assert browser.get("/api/sessions").json()[0]["audio_source"] == source
    assert Store(store.root).get(sid)["audio_source"] == source


def test_existing_records_default_to_system_audio_without_losing_content(tmp_path):
    store = Store(tmp_path)
    sid = store.create(Settings(title="旧版本机录音"))
    store.update(sid, status="completed")
    store.add_segments(sid, [{"start": 0, "end": 2, "text": "保留的原文", "review": False}])
    with store.connect() as db:
        db.execute("ALTER TABLE sessions DROP COLUMN audio_source")
    migrated = Store(tmp_path)
    record = migrated.get(sid)
    assert record["audio_source"] == Settings(**record).audio_source == "system"
    assert record["segments"][0]["text"] == "保留的原文"


def test_screenshot_is_inserted_at_capture_time_and_exports_with_photo_folder(client):
    browser, store, _ = client
    sid = store.create(Settings(title="截图课程"))
    store.update(sid, status="completed", duration=20, processed=20)
    store.add_segments(sid, [{"start": 0, "end": 3, "text": "截图前的文字", "review": False}])
    image = png_bytes()
    response = browser.post(f"/api/sessions/{sid}/screenshots?at=8.5", content=image,
                            headers={"Content-Type": "image/png"})
    assert response.status_code == 201
    picture = response.json()["segments"][-1]
    assert picture["kind"] == "image"
    assert picture["start"] == picture["end"] == 8.5
    filename = picture["photo_filename"]
    store.add_segments(sid, [{"start": 5, "end": 7, "text": "稍后识别出的早期文字", "review": False},
                             {"start": 10, "end": 12, "text": "截图后的文字", "review": False}])
    session = browser.get(f"/api/sessions/{sid}").json()
    assert [item["start"] for item in session["segments"]] == [0, 5, 8.5, 10]
    exported = browser.get(f"/api/sessions/{sid}/markdown").text
    link = f"![截图 00:00:08](photo/{filename})"
    assert link in exported
    assert exported.index("稍后识别出的早期文字") < exported.index(link) < exported.index("截图后的文字")
    photo = browser.get(f"/api/sessions/{sid}/photo/{filename}")
    assert photo.status_code == 200 and photo.content == image
    assert photo.headers["content-type"] == "image/png"
    edited = "# 手动编辑的标题\n\n自定义内容\n\n" + link
    bundle = browser.post(f"/api/sessions/{sid}/bundle", json={"content": edited})
    assert bundle.status_code == 200
    with zipfile.ZipFile(io.BytesIO(bundle.content)) as archive:
        assert archive.read("截图课程/截图课程.md").decode() == edited
        assert archive.read("截图课程/photo/" + filename) == image
    other = store.create(Settings())
    assert browser.get(f"/api/sessions/{other}/photo/{filename}").status_code == 404
    assert browser.get(f"/api/sessions/{sid}/photo/not-a-photo.png").status_code == 404
    assert browser.patch(f"/api/sessions/{sid}/segments/{picture['id']}", json={"text": "x"}).status_code == 400
    assert Store(store.root).get(sid)["segments"][2]["photo_filename"] == filename
    replacement = png_bytes()
    replaced = browser.post(
        f"/api/sessions/{sid}/segments/{picture['id']}/photo",
        content=replacement,
        headers={"Content-Type": "image/png"},
    )
    assert replaced.status_code == 200
    updated = next(item for item in replaced.json()["segments"] if item["id"] == picture["id"])
    assert updated["photo_filename"] != filename
    assert updated["photo_filename"] in updated["text"]
    assert not (store.root / sid / "photo" / filename).exists()
    assert browser.get(f"/api/sessions/{sid}/photo/{updated['photo_filename']}").content == replacement
    assert browser.delete(f"/api/sessions/{sid}/segments/{updated['id']}").status_code == 200
    remaining = browser.get(f"/api/sessions/{sid}").json()
    assert all(item["kind"] != "image" for item in remaining["segments"])
    assert f"photo/{updated['photo_filename']}" not in browser.get(f"/api/sessions/{sid}/markdown").text
    assert not (store.root / sid / "photo" / updated["photo_filename"]).exists()
    text_id = remaining["segments"][0]["id"]
    assert browser.delete(f"/api/sessions/{sid}/segments/{text_id}").status_code == 400
    assert browser.delete(f"/api/sessions/{sid}").status_code == 200
    assert not (store.root / sid).exists()


def test_screenshot_appends_to_edited_markdown_and_survives_concurrent_save(client):
    browser, store, _ = client
    sid = store.create(Settings())
    store.update(sid, duration=10)
    baseline = "# 保留用户标题\n\n已经整理的笔记"
    store.save_markdown(sid, baseline)
    response = browser.post(f"/api/sessions/{sid}/screenshots?at=100", content=png_bytes(),
                            headers={"Content-Type": "image/png"})
    assert response.status_code == 201
    session = response.json()
    picture = session["segments"][0]
    assert picture["start"] == 10
    assert session["markdown_content"].startswith(baseline)
    response = browser.patch(f"/api/sessions/{sid}/markdown",
                             json={"content": baseline + "\n用户继续输入", "base_content": baseline})
    assert response.status_code == 200
    assert "用户继续输入" in response.json()["content"]
    assert "photo/" + picture["photo_filename"] in response.json()["content"]


@pytest.mark.parametrize("invalid", [b"not a PNG", png_bytes()[:30], png_bytes()[:-1] + b"x"])
def test_invalid_screenshot_does_not_change_record_or_create_photos(client, invalid):
    browser, store, _ = client
    sid = store.create(Settings())
    response = browser.post(f"/api/sessions/{sid}/screenshots", content=invalid,
                            headers={"Content-Type": "image/png"})
    assert response.status_code == 400
    assert store.get(sid)["segments"] == []
    assert not (store.root / sid / "photo").exists()


def test_screenshot_upload_limits_and_missing_record(client, monkeypatch):
    browser, store, _ = client
    sid = store.create(Settings())
    assert browser.post(f"/api/sessions/{sid}/screenshots", content=png_bytes()).status_code == 415
    assert browser.post(f"/api/sessions/{sid}/screenshots?at=nan", content=png_bytes(),
                        headers={"Content-Type": "image/png"}).status_code == 422
    assert browser.post("/api/sessions/missing/screenshots", content=png_bytes(),
                        headers={"Content-Type": "image/png"}).status_code == 404
    monkeypatch.setattr(main, "MAX_SCREENSHOT_BYTES", 16)
    assert browser.post(f"/api/sessions/{sid}/screenshots", content=png_bytes(),
                        headers={"Content-Type": "image/png"}).status_code == 413
    assert store.get(sid)["segments"] == []


@pytest.mark.parametrize("engine,model", [("mlx", "large-v3"), ("mlx", "small"),
                                         ("faster-whisper", "large-v3-turbo")])
def test_download_status_checks_complete_matching_cache_without_network(tmp_path, monkeypatch, engine, model):
    from huggingface_hub import HfApi, constants

    monkeypatch.setattr("backend.asr.DATA", tmp_path)
    monkeypatch.setattr(constants, "HF_HUB_CACHE", str(tmp_path / "hub"))

    def no_network(*args, **kwargs):
        pytest.fail("Checking downloaded models must not access the network")

    monkeypatch.setattr(HfApi, "repo_info", no_network)
    assert cached_model_path(engine, model) is None
    root = tmp_path / "hub" if engine == "mlx" else tmp_path / "models" / "faster-whisper"
    repository = (MLX_MODELS if engine == "mlx" else FASTER_WHISPER_MODELS)[model]
    cache = root / ("models--" + repository.replace("/", "--"))
    (cache / "refs").mkdir(parents=True)
    (cache / "refs" / "main").write_text("a" * 40)
    snapshot = cache / "snapshots" / ("a" * 40)
    snapshot.mkdir(parents=True)
    (snapshot / "config.json").write_text("{}")
    assert cached_model_path(engine, model) is None
    weights = snapshot / ("weights.npz" if engine == "mlx" else "model.bin")
    weights.write_bytes(b"")
    assert cached_model_path(engine, model) is None
    weights.write_bytes(b"weights fixture")
    if engine != "mlx":
        assert cached_model_path(engine, model) is None
        (snapshot / "tokenizer.json").write_text("{}")
        (snapshot / "vocabulary.json").write_text("[]")
    assert cached_model_path(engine, model) == str(snapshot)
    availability = cached_models()["downloaded"]
    assert availability[engine][model] is True
    other_engine = "faster-whisper" if engine == "mlx" else "mlx"
    assert availability[other_engine][model] is False
    weights.unlink()
    weights.symlink_to(tmp_path / "missing-weights")
    assert cached_model_path(engine, model) is None


def test_status_exposes_disk_cache_independently_of_loaded_model(client, monkeypatch):
    browser, _, service = client
    availability = {"auto_engine": "mlx", "downloaded": {"mlx": {"large-v3": True}}}
    monkeypatch.setattr(main, "cached_models", lambda: availability)
    service.recognizer.state = {"status": "idle", "message": "", "engine": None, "model": None}
    status = browser.get("/api/status").json()
    assert status["model"]["status"] == "idle"
    assert status["model_cache"] == availability


def test_legacy_audio_is_preserved_but_not_served(client):
    browser, store, _ = client
    sid = store.create(Settings())
    store.update(sid, status="completed")
    legacy = store.root / sid / "audio.wav"
    legacy.write_bytes(b"legacy recording")
    assert browser.get(f"/api/sessions/{sid}/audio").status_code == 404
    assert legacy.read_bytes() == b"legacy recording"


def test_failed_inference_cleans_all_queued_audio(client, monkeypatch):
    browser, store, service = client

    def fail(*args):
        raise RuntimeError("Fixture inference failure")

    monkeypatch.setattr(service.recognizer, "transcribe", fail)
    response = browser.post("/api/sessions", json={})
    assert response.status_code == 201
    session = wait_completed(browser, response.json()["id"])
    assert session["status"] == "failed"
    assert "Fixture inference failure" in session["error"]
    assert not list(store.root.glob("*/*.wav"))


def test_unknown_session_and_segment(client):
    browser, store, _ = client
    assert browser.get("/api/sessions/missing").status_code == 404
    sid = store.create(Settings())
    assert browser.patch(f"/api/sessions/{sid}/segments/999", json={"text": "test"}).status_code == 404
    assert browser.get(f"/api/sessions/{sid}/audio").status_code == 404
    assert browser.delete("/api/sessions/missing").status_code == 404


def test_delete_session_removes_text_and_files(client):
    browser, store, _ = client
    sid = store.create(Settings(title="待删除课程"))
    store.update(sid, status="completed", duration=12)
    store.add_segments(
        sid, [{"start": 0, "end": 1.5, "text": "第一段", "review": True}]
    )
    leftover = store.root / sid / "chunk-000000.wav"
    leftover.write_bytes(b"temp")
    assert browser.delete(f"/api/sessions/{sid}").status_code == 200
    assert browser.get(f"/api/sessions/{sid}").status_code == 404
    assert browser.get("/api/sessions").json() == []
    assert not (store.root / sid).exists()


def test_cannot_delete_active_session(client, monkeypatch):
    browser, store, service = client

    async def capture(sid, queue):
        store.update(sid, status="recording")
        while not service.stop_event.is_set():
            await asyncio.sleep(0.01)
        store.update(sid, duration=1)

    monkeypatch.setattr(service, "capture_system", capture)
    sid = browser.post("/api/sessions", json={}).json()["id"]
    wait_status(browser, sid, "recording")
    response = browser.delete(f"/api/sessions/{sid}")
    assert response.status_code == 409
    assert "停止" in response.json()["detail"]
    assert browser.get(f"/api/sessions/{sid}").status_code == 200
    assert browser.post(f"/api/sessions/{sid}/stop").status_code == 202
    wait_completed(browser, sid)
    assert browser.delete(f"/api/sessions/{sid}").status_code == 200
    assert browser.get(f"/api/sessions/{sid}").status_code == 404


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


def test_desktop_service_requires_native_token(client, monkeypatch):
    browser, _, _ = client
    monkeypatch.setattr(main, "DESKTOP_TOKEN", "fixture-desktop-token")
    assert browser.get("/api/status").status_code == 403
    assert browser.get("/openapi.json").status_code == 403
    assert browser.get("/api/status", headers={"X-Parrot-Token": "wrong"}).status_code == 403
    headers = {"X-Parrot-Token": "fixture-desktop-token"}
    assert browser.get("/api/status", headers=headers).status_code == 200
    assert browser.get("/api/sessions", headers=headers).status_code == 200


def test_markdown_preserves_uncertainty_without_segment_timestamps(tmp_path):
    store = Store(tmp_path)
    sid = store.create(Settings(title="Lecture"))
    store.add_segments(
        sid,
        [
            {"start": 3661, "end": 3663, "text": "  中文 English \n\n ", "review": True},
            {"start": 3663, "end": 3664, "text": " \t ", "review": False},
            {"start": 3664, "end": 3666, "text": "Next sentence.\r\n\r\n Last line ", "review": False},
        ],
    )
    exported = markdown(store.get(sid))
    assert "（待校对）中文 English" in exported
    assert "01:01:01" not in exported
    assert "### " not in exported
    assert "- 音频时长：00:00:00" in exported
    body = exported.split("## 转写原文\n\n", 1)[1]
    assert body == "（待校对）中文 English\nNext sentence.\nLast line"


def test_stopping_capture_drains_last_partial_chunk(tmp_path, monkeypatch):
    async def run():
        store = Store(tmp_path)
        service = TranscriptionService(store)
        service.recognizer = FixtureRecognizer()

        class CaptureProcess:
            def __init__(self):
                self.stdout = asyncio.StreamReader()
                self.stderr = asyncio.StreamReader()
                with wave.open(io.BytesIO(wav_bytes(1.3))) as audio:
                    self.stdout.feed_data(audio.readframes(audio.getnframes()))
                self.stdout.feed_eof()
                self.stderr.feed_data(b"READY\n")
                self.stderr.feed_eof()
                self.returncode = None

            def terminate(self):
                self.returncode = -15

            def kill(self):
                self.returncode = -9

            async def wait(self):
                if self.returncode is None:
                    self.returncode = 0
                return self.returncode

        async def create_process(*args, **kwargs):
            service.stop_event.set()
            return CaptureProcess()

        monkeypatch.setattr("backend.service.asyncio.create_subprocess_exec", create_process)
        monkeypatch.setattr("backend.service.CAPTURE_BINARY", tmp_path)
        monkeypatch.setattr("backend.service.platform.system", lambda: "Darwin")
        sid = service.launch(Settings())
        with pytest.raises(ValueError):
            service.launch(Settings())
        await service.task
        record = store.get(sid)
        assert record["status"] == "completed"
        assert record["processed"] == 1.3
        assert record["duration"] == 1.3
        assert not list((store.root / sid).glob("*.wav"))
        assert record["segments"][0]["text"] == "课程内容 Test."
        await service.close()

    asyncio.run(run())


def test_stop_while_preparing_does_not_start_capture(tmp_path, monkeypatch):
    async def run():
        store = Store(tmp_path)
        service = TranscriptionService(store)
        service.recognizer = FixtureRecognizer()
        monkeypatch.setattr("backend.service.CAPTURE_BINARY", tmp_path)
        monkeypatch.setattr("backend.service.platform.system", lambda: "Darwin")
        sid = service.launch(Settings())
        service.stop()
        await service.task
        record = store.get(sid)
        assert record["status"] == "completed"
        assert record["duration"] == 0
        assert record["segments"] == []
        await service.close()

    asyncio.run(run())


@pytest.mark.parametrize("system", ["Windows", "Linux"])
def test_system_capture_rejects_unsupported_platform(client, monkeypatch, system):
    browser, _, _ = client
    monkeypatch.setattr("backend.service.platform.system", lambda: system)
    response = browser.post("/api/sessions", json={})
    assert response.status_code == 400
    assert "macOS" in response.json()["detail"]
    assert browser.get("/api/sessions").json() == []


class LiveCaptureProcess:
    """Emit a finite sample interval, but keep capture alive until terminated."""

    def __init__(self, duration=0.35, failed=False):
        self.stdout = asyncio.StreamReader()
        self.stderr = asyncio.StreamReader()
        self.finished = asyncio.Event()
        self.returncode = None
        if failed:
            self.stderr.feed_data(b"fixture resume failure\n")
            self.terminate()
        else:
            with wave.open(io.BytesIO(wav_bytes(duration))) as audio:
                self.stdout.feed_data(audio.readframes(audio.getnframes()))
            self.stderr.feed_data(b"READY\n")

    def terminate(self):
        if self.returncode is None:
            self.returncode = -15
            self.stdout.feed_eof()
            self.stderr.feed_eof()
            self.finished.set()

    def kill(self):
        self.terminate()

    async def wait(self):
        await self.finished.wait()
        return self.returncode


def wait_status(browser, sid, expected):
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        result = browser.get(f"/api/sessions/{sid}").json()
        if result["status"] == expected:
            return result
        if result["status"] == "failed" and expected != "failed":
            pytest.fail(result["error"])
        time.sleep(0.01)
    pytest.fail(f"Session did not reach {expected}")


@pytest.mark.parametrize("state", ["completed", "failed", "interrupted"])
@pytest.mark.parametrize("source", ["microphone", "system", "mixed"])
def test_continue_history_appends_to_same_record_and_cumulative_timeline(client, monkeypatch, state, source):
    browser, store, service = client
    monkeypatch.setattr(service, "capture_system", TranscriptionService.capture_system.__get__(service))
    processes = []
    prepared = []

    async def create_process(*args, **kwargs):
        assert args[-2:] == ("--source", source)
        process = LiveCaptureProcess(0.35 if not processes else 0.45)
        processes.append(process)
        return process

    def prepare(settings):
        prepared.append(settings)
        return "faster-whisper"

    monkeypatch.setattr("backend.service.asyncio.create_subprocess_exec", create_process)
    monkeypatch.setattr(service.recognizer, "prepare", prepare)
    sid = store.create(Settings(title="历史课程", model="small", language="en", audio_source=source))
    with store.connect() as db:
        db.execute("UPDATE sessions SET vocabulary='旧版术语' WHERE id=?", (sid,))
    store.update(sid, status=state, duration=15, processed=15, error="旧错误" if state != "completed" else None)
    store.add_segments(sid, [{"start": 0, "end": 12, "text": "保留的校对文字", "review": False}])
    store.save_markdown(sid, "# 自定义历史标题\n\n保留 **整理后的文稿**。\n")
    original = store.get(sid)
    elapsed = 15
    for duration in [0.35, 0.45]:
        response = browser.post(f"/api/sessions/{sid}/continue")
        assert response.status_code == 202
        wait_status(browser, sid, "recording")
        assert browser.get("/api/status").json()["active_id"] == sid
        assert browser.post(f"/api/sessions/{sid}/continue").status_code == 409
        assert browser.post("/api/sessions", json={}).status_code == 409
        browser.post(f"/api/sessions/{sid}/stop")
        result = wait_status(browser, sid, "completed")
        last = result["segments"][-1]
        assert last["start"] == pytest.approx(elapsed)
        elapsed += duration
        assert last["end"] == pytest.approx(elapsed)
        assert result["duration"] == pytest.approx(elapsed)
        assert result["processed"] == pytest.approx(elapsed)
        assert result["created_at"] == original["created_at"]
        assert result["segments"][0] == original["segments"][0]
        assert result["error"] is None
        assert len(browser.get("/api/sessions").json()) == 1
        exported = browser.get(f"/api/sessions/{sid}/markdown").text
        assert exported.startswith(original["markdown_content"])
        assert "课程内容 Test." in exported
        assert not list(store.root.glob("*/*.wav"))
    assert len(result["segments"]) == 3
    assert all(s.model == "small" and s.language == "en" and s.audio_source == source
               and not hasattr(s, "vocabulary") for s in prepared)
    assert "vocabulary" not in result
    assert browser.post("/api/sessions/missing/continue").status_code == 404
    unfinished = store.create(Settings())
    assert browser.post(f"/api/sessions/{unfinished}/continue").status_code == 409


@pytest.mark.parametrize("customized", [False, True])
def test_markdown_edit_keeps_transcription_added_while_typing(client, customized):
    browser, store, _ = client
    sid = store.create(Settings(title="并行编辑"))
    store.add_segments(sid, [{"start": 0, "end": 1, "text": "旧内容", "review": False}])
    if customized:
        store.save_markdown(sid, "# 整理标题\n\n旧内容")
    baseline = markdown(store.get(sid))
    store.add_segments(sid, [{"start": 1, "end": 2, "text": "续写的新内容", "review": False}])
    store.update(sid, duration=2)
    edited = baseline.replace("旧内容", "校对后的旧内容")
    response = browser.patch(f"/api/sessions/{sid}/markdown",
                             json={"content": edited, "base_content": baseline})
    assert response.status_code == 200
    assert response.json()["content"] == edited + "\n续写的新内容"
    assert browser.get(f"/api/sessions/{sid}/markdown").text == edited + "\n续写的新内容"
    browser.patch(f"/api/sessions/{sid}/markdown", json={"content": "# 其他修改"})
    stale = browser.patch(f"/api/sessions/{sid}/markdown", json={"content": edited, "base_content": baseline})
    assert stale.status_code == 409
    assert browser.get(f"/api/sessions/{sid}/markdown").text == "# 其他修改"


@pytest.mark.parametrize("finish", ["resume-stop", "stop-paused", "failed-resume"])
@pytest.mark.parametrize("source", ["microphone", "system", "mixed"])
def test_pause_controls_preserve_record_tail_and_elapsed_audio(client, monkeypatch, finish, source):
    browser, store, service = client
    monkeypatch.setattr(service, "capture_system", TranscriptionService.capture_system.__get__(service))
    processes = []

    async def create_process(*args, **kwargs):
        assert args[-2:] == ("--source", source)
        process = LiveCaptureProcess(0.35 if not processes else 0.45,
                                     failed=bool(processes) and finish == "failed-resume")
        processes.append(process)
        return process

    monkeypatch.setattr("backend.service.asyncio.create_subprocess_exec", create_process)
    sid = browser.post("/api/sessions", json={"audio_source": source}).json()["id"]
    wait_status(browser, sid, "recording")
    assert browser.post(f"/api/sessions/{sid}/resume").status_code == 409
    assert browser.post(f"/api/sessions/{sid}/pause").status_code == 202
    paused = wait_status(browser, sid, "paused")
    assert browser.get("/api/status").json()["active_status"] == "paused"
    assert paused["duration"] == pytest.approx(0.35)
    assert processes[0].returncode is not None
    assert service.process is None
    assert browser.get("/api/status").json()["level"] == 0
    assert browser.get("/api/status").json()["active_id"] == sid
    assert browser.post("/api/sessions", json={}).status_code == 409
    assert browser.post(f"/api/sessions/{sid}/pause").status_code == 409
    assert browser.get(f"/api/sessions/{sid}").json()["duration"] == paused["duration"]
    other = store.create(Settings(title="其他记录"))
    assert browser.post(f"/api/sessions/{other}/resume").status_code == 409
    assert browser.post("/api/sessions/missing/pause").status_code == 404
    assert browser.post("/api/sessions/missing/resume").status_code == 404
    deadline = time.monotonic() + 5
    while not browser.get(f"/api/sessions/{sid}").json()["segments"]:
        assert time.monotonic() < deadline
        time.sleep(0.01)
    if finish != "stop-paused":
        assert browser.post(f"/api/sessions/{sid}/resume").status_code == 202
        if finish == "failed-resume":
            result = wait_status(browser, sid, "failed")
            assert "fixture resume failure" in result["error"]
        else:
            wait_status(browser, sid, "recording")
            assert browser.get("/api/status").json()["active_status"] == "recording"
            browser.post(f"/api/sessions/{sid}/stop")
            result = wait_status(browser, sid, "completed")
            assert result["duration"] == pytest.approx(0.8)
            assert [(s["start"], s["end"]) for s in result["segments"]] == [(0, 0.35), (0.35, 0.8)]
    else:
        assert browser.post(f"/api/sessions/{sid}/stop").status_code == 202
        result = wait_status(browser, sid, "completed")
        assert result["duration"] == pytest.approx(0.35)
    assert result["segments"][0]["end"] == pytest.approx(0.35)
    assert result["audio_source"] == source
    assert len(processes) == (1 if finish == "stop-paused" else 2)
    assert not list(store.root.glob("*/*.wav"))
    assert browser.post(f"/api/sessions/{sid}/resume").status_code == 409
    assert browser.get("/api/status").json()["active_status"] is None


def test_paused_session_closes_without_starting_another_capture(tmp_path, monkeypatch):
    async def run():
        store = Store(tmp_path)
        service = TranscriptionService(store)
        service.recognizer = FixtureRecognizer()
        processes = []

        async def create_process(*args, **kwargs):
            process = LiveCaptureProcess()
            processes.append(process)
            return process

        monkeypatch.setattr("backend.service.asyncio.create_subprocess_exec", create_process)
        monkeypatch.setattr("backend.service.CAPTURE_BINARY", tmp_path)
        monkeypatch.setattr("backend.service.platform.system", lambda: "Darwin")
        sid = service.launch(Settings())
        try:
            async def reach(status):
                while store.get(sid)["status"] != status:
                    await asyncio.sleep(0.005)
            await asyncio.wait_for(reach("recording"), 5)
            service.pause()
            await asyncio.wait_for(reach("paused"), 5)
            await asyncio.wait_for(service.close(), 2)
            assert service.task.done()
            assert store.get(sid)["status"] == "completed"
            assert len(processes) == 1
            assert not list((store.root / sid).glob("*.wav"))
        finally:
            await service.close()

    asyncio.run(run())


def test_pause_at_exact_flush_boundary_does_not_reuse_old_context(tmp_path):
    chunker = Chunker(tmp_path)
    first = chunker.feed((np.ones(24 * 16000) * 3000).astype("<i2").tobytes())
    assert first is not None
    assert chunker.finish() is None
    chunker.feed((np.ones(1600) * 5000).astype("<i2").tobytes())
    resumed = chunker.finish()
    assert (resumed.offset, resumed.cutoff, resumed.end) == (24, 24, 24.1)
    assert first.path != resumed.path
    with wave.open(str(resumed.path)) as audio:
        assert audio.getnframes() == 1600


def test_study_notes_can_reorder_photos_and_update_title_with_exact_undo(client):
    browser, store, _ = client
    sid = store.create(Settings(title="学习记录"))
    store.update(sid, status="completed")
    image = png_bytes()
    for at in [1, 2]:
        assert browser.post(f"/api/sessions/{sid}/screenshots?at={at}", content=image,
                            headers={"Content-Type": "image/png"}).status_code == 201
    before = store.get(sid)
    photos = [segment["text"] for segment in before["segments"] if segment["kind"] == "image"]
    original = browser.get(f"/api/sessions/{sid}/markdown").text
    result = f"# RAG 的检索与生成\n\n## 检索\n\n{photos[1]}\n\n## 生成\n\n{photos[0]}\n"
    applied = browser.post(f"/api/sessions/{sid}/polish/apply", json={"content": result, "base_content": original})
    assert applied.status_code == 200
    assert applied.json()["title"] == "RAG 的检索与生成"
    assert applied.json()["segments"] == before["segments"]
    for segment in before["segments"]:
        assert browser.get(f"/api/sessions/{sid}/photo/{segment['photo_filename']}").content == image
    for changed in [result.replace(photos[0], ""), result + photos[0], result.replace("photo/", "photo/new-")]:
        saved = browser.post(f"/api/sessions/{sid}/polish/apply", json={"content": changed, "base_content": result})
        assert saved.status_code == 200
        assert saved.json()["markdown_content"] == changed
        assert browser.post(f"/api/sessions/{sid}/polish/undo", json={
            "revision_id": saved.json()["polish_revision"], "base_content": changed,
        }).status_code == 200
    revision = applied.json()["polish_revision"]
    undone = browser.post(f"/api/sessions/{sid}/polish/undo", json={"revision_id": revision, "base_content": result})
    assert undone.status_code == 200
    assert undone.json()["markdown_content"] == original
    assert undone.json()["title"] == "学习记录"

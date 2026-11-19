import asyncio
import hmac
import logging
import platform
import shutil
import tempfile
import zipfile
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Optional
from urllib.parse import quote

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from starlette.background import BackgroundTask

from .asr import cached_models
from .config import APPLE_SILICON, CAPTURE_BINARY, DESKTOP_TOKEN, ROOT
from .photos import MAX_SCREENSHOT_BYTES, validate_png
from .schemas import EditMarkdown, EditSegment, Settings
from .service import TranscriptionService
from .store import Store, markdown

store = Store()
service = TranscriptionService(store)
prepare_task: Optional[asyncio.Task] = None


@asynccontextmanager
async def lifespan(app):
    yield
    if prepare_task and not prepare_task.done():
        prepare_task.cancel()
    await service.close()


app = FastAPI(title="Parrot", version="1.0.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_methods=["GET", "POST", "PATCH", "DELETE"],
    allow_headers=["Content-Type", "X-Parrot-Token"],
)


@app.middleware("http")
async def localhost_only(request, call_next):
    # This local recorder has no login. Refuse cross-site state changes and DNS
    # rebinding, while keeping the CLI and same-origin Vite proxy usable.
    host = request.url.hostname
    if host not in {"localhost", "127.0.0.1", "::1", "testserver"}:
        return Response("Local access only", status_code=403)
    origin = request.headers.get("origin")
    allowed_origins = {"http://localhost:5173", "http://127.0.0.1:5173", str(request.base_url).rstrip("/")}
    if origin and origin not in allowed_origins:
        return Response("Origin not allowed", status_code=403)
    if DESKTOP_TOKEN and (
        request.url.path.startswith("/api/") or request.url.path in {"/docs", "/redoc", "/openapi.json"}
    ):
        if not hmac.compare_digest(
            request.headers.get("X-Parrot-Token", "").encode(), DESKTOP_TOKEN.encode()
        ):
            return Response("Desktop access only", status_code=403)
    return await call_next(request)


def require_session(sid):
    session = store.get(sid)
    if session is None:
        raise HTTPException(404, "记录不存在")
    return session


def require_idle():
    if service.busy or (prepare_task and not prepare_task.done()):
        raise HTTPException(409, "已有任务运行中，请等待当前任务完成。")


@app.get("/api/status")
async def status():
    active_id = service.active_id if service.busy else None
    return {
        "model": service.recognizer.state,
        "model_cache": cached_models(),
        "active_id": active_id,
        "active_status": store.get_status(active_id) if active_id else None,
        "level": service.level,
        "platform": platform.system(),
        "apple_silicon": APPLE_SILICON,
        "capture_available": platform.system() == "Darwin" and CAPTURE_BINARY.exists(),
    }


@app.post("/api/model/prepare", status_code=202)
async def prepare(settings: Settings):
    global prepare_task
    require_idle()

    async def load():
        try:
            await service.infer(service.recognizer.prepare, settings)
        except Exception:
            logging.getLogger(__name__).exception("Model preparation failed")

    prepare_task = asyncio.create_task(load())
    return {"message": "模型加载已开始"}


@app.get("/api/sessions")
async def sessions():
    return store.list()


@app.post("/api/sessions", status_code=201)
async def start(settings: Settings):
    require_idle()
    try:
        sid = service.launch(settings)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return require_session(sid)


@app.get("/api/sessions/{sid}")
async def detail(sid: str):
    return require_session(sid)


@app.post("/api/sessions/{sid}/continue", status_code=202)
async def continue_session(sid: str):
    session = require_session(sid)
    require_idle()
    if session["status"] not in {"completed", "failed", "interrupted"}:
        raise HTTPException(409, "请等待这条记录的转写完成后再续写。")
    try:
        service.launch(Settings(**session), sid=sid)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return require_session(sid)


@app.delete("/api/sessions/{sid}")
async def delete_session(sid: str):
    require_session(sid)
    if service.busy and service.active_id == sid:
        raise HTTPException(409, "请先停止当前聆听，再删除这条记录。")
    if not store.delete(sid):
        raise HTTPException(404, "记录不存在")
    return {"ok": True}


@app.post("/api/sessions/{sid}/stop", status_code=202)
async def stop(sid: str):
    require_session(sid)
    if service.busy and service.active_id == sid:
        service.stop()
    return require_session(sid)


def require_active_session(sid):
    require_session(sid)
    if not service.busy or service.active_id != sid:
        raise HTTPException(409, "这条记录没有正在进行的聆听任务。")


@app.post("/api/sessions/{sid}/pause", status_code=202)
async def pause(sid: str):
    require_active_session(sid)
    try:
        service.pause()
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    return require_session(sid)


@app.post("/api/sessions/{sid}/resume", status_code=202)
async def resume(sid: str):
    require_active_session(sid)
    try:
        service.resume()
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    return require_session(sid)


@app.patch("/api/sessions/{sid}/segments/{segment_id}")
async def edit(sid: str, segment_id: int, body: EditSegment):
    session = require_session(sid)
    if any(segment["id"] == segment_id and segment["kind"] == "image" for segment in session["segments"]):
        raise HTTPException(400, "截图不能作为文字段落修改")
    if not store.edit(sid, segment_id, body.text):
        raise HTTPException(404, "段落不存在")
    return {"ok": True}


@app.delete("/api/sessions/{sid}/segments/{segment_id}")
async def delete_segment(sid: str, segment_id: int):
    session = require_session(sid)
    target = next((segment for segment in session["segments"] if segment["id"] == segment_id), None)
    if target is None:
        raise HTTPException(404, "段落不存在")
    if target.get("kind") != "image":
        raise HTTPException(400, "目前只能删除截图段落")
    if not store.delete_segment(sid, segment_id):
        raise HTTPException(404, "段落不存在")
    return {"ok": True}


@app.post("/api/sessions/{sid}/segments/{segment_id}/photo", status_code=200)
async def replace_screenshot(sid: str, segment_id: int, request: Request):
    session = require_session(sid)
    target = next((segment for segment in session["segments"] if segment["id"] == segment_id), None)
    if target is None:
        raise HTTPException(404, "段落不存在")
    if target.get("kind") != "image":
        raise HTTPException(400, "只有截图段落可以替换图片")
    if request.headers.get("content-type", "").split(";", 1)[0].lower() != "image/png":
        raise HTTPException(415, "截图必须是 PNG 图片。")
    data = bytearray()
    async for chunk in request.stream():
        data.extend(chunk)
        if len(data) > MAX_SCREENSHOT_BYTES:
            raise HTTPException(413, "截图超过 25MB，请缩小截图范围。")
    try:
        validate_png(data)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    if not store.replace_screenshot(sid, segment_id, data):
        raise HTTPException(404, "段落不存在")
    return require_session(sid)


@app.get("/api/sessions/{sid}/markdown")
async def export(sid: str):
    session = require_session(sid)
    filename = quote(session["title"].replace("/", "_").replace("\\", "_") + ".md", safe="")
    return Response(
        markdown(session),
        media_type="text/markdown; charset=utf-8",
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{filename}"},
    )


@app.post("/api/sessions/{sid}/screenshots", status_code=201)
async def add_screenshot(sid: str, request: Request,
                         at: Optional[float] = Query(default=None, ge=0, allow_inf_nan=False)):
    require_session(sid)
    if request.headers.get("content-type", "").split(";", 1)[0].lower() != "image/png":
        raise HTTPException(415, "截图必须是 PNG 图片。")
    data = bytearray()
    async for chunk in request.stream():
        data.extend(chunk)
        if len(data) > MAX_SCREENSHOT_BYTES:
            raise HTTPException(413, "截图超过 25MB，请缩小截图范围。")
    try:
        validate_png(data)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    if not store.add_screenshot(sid, data, at):
        raise HTTPException(404, "记录不存在")
    return require_session(sid)


@app.get("/api/sessions/{sid}/photo/{filename}")
async def screenshot_file(sid: str, filename: str):
    require_session(sid)
    path = store.photo_path(sid, filename)
    if not path:
        raise HTTPException(404, "截图不存在")
    return FileResponse(path, media_type="image/png", headers={"X-Content-Type-Options": "nosniff"})


@app.post("/api/sessions/{sid}/bundle")
async def export_bundle(sid: str, body: EditMarkdown):
    session = require_session(sid)
    title = session["title"].replace("/", "_").replace("\\", "_").replace("\r", " ").replace("\n", " ")
    directory = Path(tempfile.mkdtemp(prefix="parrot-export-"))
    try:
        bundle = directory / "export.zip"
        with zipfile.ZipFile(bundle, "w") as archive:
            archive.writestr(f"{title}/{title}.md", body.content)
            archive.writestr(f"{title}/photo/", b"")
            for segment in session["segments"]:
                filename = segment.get("photo_filename")
                if segment.get("kind") == "image" and filename and f"photo/{filename}" in body.content:
                    path = store.photo_path(sid, filename)
                    if not path:
                        raise HTTPException(404, "文稿中的截图文件不存在")
                    archive.write(path, f"{title}/photo/{filename}")
        return FileResponse(bundle, media_type="application/zip", filename=title + ".zip",
                            background=BackgroundTask(shutil.rmtree, directory))
    except Exception:
        shutil.rmtree(directory, ignore_errors=True)
        raise


@app.patch("/api/sessions/{sid}/markdown")
async def edit_markdown(sid: str, body: EditMarkdown):
    try:
        saved = store.save_markdown(sid, body.content, body.base_content)
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    if not saved:
        raise HTTPException(404, "记录不存在")
    session = require_session(sid)
    return {"ok": True, "title": session["title"], "content": session["markdown_content"]}


dist = ROOT / "frontend" / "dist"
if dist.exists():
    app.mount("/", StaticFiles(directory=dist, html=True), name="frontend")

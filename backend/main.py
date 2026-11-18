import asyncio
import logging
import platform
import uuid
from contextlib import asynccontextmanager
from typing import Optional
from urllib.parse import quote

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import ValidationError

from .config import APPLE_SILICON, CAPTURE_BINARY, DATA, ROOT
from .schemas import EditSegment, Settings
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


app = FastAPI(title="Parrot", version="0.1.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_methods=["GET", "POST", "PATCH"],
    allow_headers=["Content-Type"],
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
    return {
        "model": service.recognizer.state,
        "active_id": service.active_id if service.busy else None,
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
        sid = service.launch(settings, "system")
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return require_session(sid)


@app.post("/api/sessions/upload", status_code=201)
async def upload(file: UploadFile = File(...), settings: str = Form(...)):
    require_idle()
    try:
        options = Settings.model_validate_json(settings)
    except ValidationError as exc:
        raise HTTPException(422, "转写设置格式不正确") from exc
    # Stream uploads to disk; never read an entire course into memory.
    path = DATA / f"upload-{uuid.uuid4().hex}"
    size = 0
    try:
        with path.open("wb") as output:
            while chunk := await file.read(1024 * 1024):
                size += len(chunk)
                if size > 2 * 1024**3:
                    raise HTTPException(413, "文件超过 2GB，请先切分文件。")
                output.write(chunk)
        if not size:
            raise HTTPException(400, "文件为空")
        # Recheck after streaming upload: another request may have started work.
        require_idle()
        sid = service.launch(options, "file", uploaded=path)
    except Exception:
        path.unlink(missing_ok=True)
        raise
    finally:
        await file.close()
    return require_session(sid)


@app.get("/api/sessions/{sid}")
async def detail(sid: str):
    return require_session(sid)


@app.post("/api/sessions/{sid}/stop", status_code=202)
async def stop(sid: str):
    require_session(sid)
    if service.busy and service.active_id == sid:
        service.stop()
    return require_session(sid)


@app.patch("/api/sessions/{sid}/segments/{segment_id}")
async def edit(sid: str, segment_id: int, body: EditSegment):
    require_session(sid)
    if not store.edit(sid, segment_id, body.text):
        raise HTTPException(404, "段落不存在")
    return {"ok": True}


@app.get("/api/sessions/{sid}/markdown")
async def export(sid: str):
    session = require_session(sid)
    filename = quote(session["title"].replace("/", "_").replace("\\", "_") + ".md", safe="")
    return Response(
        markdown(session),
        media_type="text/markdown; charset=utf-8",
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{filename}"},
    )


@app.get("/api/sessions/{sid}/audio")
async def audio(sid: str):
    session = require_session(sid)
    if session["status"] in {"preparing", "recording", "transcribing", "stopping"}:
        raise HTTPException(409, "录音完成后可回听")
    path = store.audio_path(sid)
    if not path.exists():
        raise HTTPException(404, "这条记录没有音频")
    return FileResponse(path, media_type="audio/wav", filename=f"{sid}.wav")


dist = ROOT / "frontend" / "dist"
if dist.exists():
    app.mount("/", StaticFiles(directory=dist, html=True), name="frontend")

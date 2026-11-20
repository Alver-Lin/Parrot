import asyncio
import logging
import platform
from concurrent.futures import ThreadPoolExecutor
from contextlib import suppress
from threading import Event

from .asr import Recognizer
from .audio import Chunker
from .config import CAPTURE_BINARY, SAMPLE_RATE

log = logging.getLogger(__name__)


class TranscriptionService:
    def __init__(self, store):
        self.store = store
        self.recognizer = Recognizer()
        self.executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="asr")
        self.task = None
        self.active_id = None
        self.stop_event = Event()
        self.process = None
        self.level = 0.0
        self.pause_requested = False
        # Bind the event in launch(), inside the actual serving loop (Python 3.9).
        self.resume_event = None

    @property
    def busy(self):
        return self.task is not None and not self.task.done()

    async def infer(self, func, *args):
        return await asyncio.get_running_loop().run_in_executor(self.executor, func, *args)

    def launch(self, settings, sid=None):
        if self.busy:
            raise ValueError("已有任务运行中，请先停止并等待转写完成。")
        if platform.system() != "Darwin":
            raise ValueError("Parrot 的音频采集仅支持 macOS 13+。")
        if not CAPTURE_BINARY.exists():
            raise ValueError("音频采集组件未编译，请运行 ./scripts/build-capture.sh。")
        if sid is None:
            sid = self.store.create(settings)
        else:
            session = self.store.get(sid)
            if session is None or session["status"] not in {"completed", "failed", "interrupted"}:
                raise ValueError("只有已结束的历史记录可以续写。")
            offset = max(session["duration"], session["processed"],
                         max((segment["end"] for segment in session["segments"]), default=0))
            (self.store.root / sid).mkdir(exist_ok=True)
            self.store.update(sid, status="preparing", error=None, duration=offset, processed=offset)
        self.active_id = sid
        self.stop_event = Event()
        self.pause_requested = False
        self.resume_event = asyncio.Event()
        self.resume_event.set()
        self.level = 0.0
        self.task = asyncio.create_task(self.run(sid, settings))
        return sid

    def stop(self):
        if not self.busy:
            return
        self.stop_event.set()
        self.resume_event.set()  # Wake a paused session so Stop/quit cannot hang.
        self.store.update(self.active_id, status="stopping")
        if self.process and self.process.returncode is None:
            with suppress(ProcessLookupError):
                self.process.terminate()

    def pause(self):
        if not self.busy or self.store.get(self.active_id)["status"] != "recording":
            raise ValueError("只有正在聆听的记录可以暂停。")
        self.pause_requested = True
        self.resume_event.clear()
        self.level = 0.0
        self.store.update(self.active_id, status="pausing")
        if self.process and self.process.returncode is None:
            with suppress(ProcessLookupError):
                self.process.terminate()

    def resume(self):
        if not self.busy or self.store.get(self.active_id)["status"] != "paused":
            raise ValueError("只有已暂停的记录可以继续。")
        self.pause_requested = False
        self.store.update(self.active_id, status="resuming")
        self.resume_event.set()

    async def run(self, sid, settings):
        queue = asyncio.Queue()  # Contains paths, never retained PCM buffers.
        consumer = None
        try:
            engine = await self.infer(self.recognizer.prepare, settings)
            self.store.update(sid, engine=engine)
            if self.stop_event.is_set():
                self.store.update(sid, status="completed")
                return
            consumer = asyncio.create_task(self.consume(sid, settings, queue))
            await self.capture_system(sid, queue)
            self.store.update(sid, status="stopping")
            await queue.put(None)
            await consumer
            self.store.update(sid, status="completed")
        except asyncio.CancelledError:
            self.store.update(sid, status="interrupted", error="服务已停止，已完成的文字仍保留。")
            raise
        except Exception as exc:
            log.exception("Transcription failed: %s", sid)
            self.store.update(sid, status="failed", error=str(exc))
        finally:
            if self.process and self.process.returncode is None:
                with suppress(ProcessLookupError):
                    self.process.kill()
                await self.process.wait()
            self.process = None
            if consumer and not consumer.done():
                consumer.cancel()
                with suppress(asyncio.CancelledError):
                    await consumer
            for path in (self.store.root / sid).glob("chunk-*.wav"):
                path.unlink(missing_ok=True)
            self.level = 0.0

    async def consume(self, sid, settings, queue):
        while True:
            chunk = await queue.get()
            if chunk is None:
                return
            try:
                segments = await self.infer(self.recognizer.transcribe, chunk, settings)
                self.store.add_segments(sid, segments)
                self.store.update(sid, processed=chunk.end)
            except Exception:
                # Stop acquisition promptly when inference fails; retain text
                # already committed and remove temporary audio when the task exits.
                self.stop()
                raise
            finally:
                chunk.path.unlink(missing_ok=True)

    async def capture_system(self, sid, queue):
        # Keep sample offsets and chunk filenames across resumes. Each pause
        # closes the capture process and drains only audio already acquired.
        chunker = Chunker(self.store.root / sid, offset=self.store.get(sid)["duration"])
        while not self.stop_event.is_set():
            if self.pause_requested:
                self.level = 0.0
                self.store.update(sid, status="paused")
                await self.resume_event.wait()
                if self.stop_event.is_set():
                    break
            await self.capture_interval(sid, queue, chunker)
            if not self.stop_event.is_set() and not self.pause_requested:
                raise RuntimeError("音频采集意外结束，请重试。")

    async def capture_interval(self, sid, queue, chunker):
        import numpy as np

        process = await asyncio.create_subprocess_exec(
            str(CAPTURE_BINARY),
            "--source", self.store.get(sid)["audio_source"],
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        self.process = process
        errors = []

        async def stderr_reader():
            while True:
                line = await process.stderr.readline()
                if not line:
                    break
                text = line.decode(errors="replace").strip()
                if text == "READY":
                    if self.stop_event.is_set():
                        self.stop()
                    else:
                        self.store.update(sid, status="recording")
                else:
                    errors.append(text)

        error_task = asyncio.create_task(stderr_reader())
        pending = bytearray()
        try:
            while True:
                pcm = await process.stdout.read(3200)
                if not pcm:
                    break
                # Pipe reads may split a sample or audio frame. Fixed 100ms
                # frames keep silence detection independent of read sizes.
                pending.extend(pcm)
                while len(pending) >= 3200:
                    frame = bytes(pending[:3200])
                    del pending[:3200]
                    samples = np.frombuffer(frame, dtype="<i2").astype(np.float32) / 32768
                    self.level = min(1, float(np.sqrt(np.mean(samples * samples))) * 8)
                    chunk = chunker.feed(frame)
                    if chunk:
                        queue.put_nowait(chunk)
                    self.store.update(sid, duration=chunker.total / SAMPLE_RATE)
                if chunker.total - chunker.initial_samples >= SAMPLE_RATE * 8 * 3600:
                    self.stop()
            if len(pending) >= 2:
                frame = bytes(pending[: len(pending) // 2 * 2])
                chunk = chunker.feed(frame)
                if chunk:
                    queue.put_nowait(chunk)
            chunk = chunker.finish()
            if chunk:
                queue.put_nowait(chunk)
            self.store.update(sid, duration=chunker.total / SAMPLE_RATE)
            self.level = 0.0
            await process.wait()
            await error_task
            if not self.stop_event.is_set() and not self.pause_requested:
                detail = " ".join(errors[-8:])
                raise RuntimeError(detail or "音频采集中断。请检查所选音频来源的权限与设备，再重试。")
            self.process = None
        finally:
            error_task.cancel()
            with suppress(asyncio.CancelledError):
                await error_task

    async def close(self):
        self.stop()
        if self.task and not self.task.done():
            try:
                await asyncio.wait_for(asyncio.shield(self.task), timeout=10)
            except asyncio.TimeoutError:
                self.task.cancel()
                with suppress(asyncio.CancelledError):
                    await self.task
        self.executor.shutdown(wait=False)

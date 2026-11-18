import asyncio
import logging
import platform
import wave
from concurrent.futures import ThreadPoolExecutor
from contextlib import suppress

from .asr import Recognizer
from .audio import Chunker, normalized_pcm
from .config import CAPTURE_BINARY, SAMPLE_RATE

log = logging.getLogger(__name__)


class TranscriptionService:
    def __init__(self, store):
        self.store = store
        self.recognizer = Recognizer()
        self.executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="asr")
        self.task = None
        self.active_id = None
        self.stop_event = asyncio.Event()
        self.process = None
        self.level = 0.0

    @property
    def busy(self):
        return self.task is not None and not self.task.done()

    async def infer(self, func, *args):
        return await asyncio.get_running_loop().run_in_executor(self.executor, func, *args)

    def launch(self, settings, source, uploaded=None):
        if self.busy:
            raise ValueError("已有任务运行中，请先停止并等待转写完成。")
        if source == "system":
            if platform.system() != "Darwin":
                raise ValueError("当前系统声音采集支持 macOS 13+；其他平台请先使用导入音频。")
            if not CAPTURE_BINARY.exists():
                raise ValueError("系统声音采集组件未编译，请运行 ./scripts/build-capture.sh。")
        sid = self.store.create(settings, source)
        self.active_id = sid
        self.stop_event = asyncio.Event()
        self.level = 0.0
        self.task = asyncio.create_task(self.run(sid, settings, uploaded))
        return sid

    def stop(self):
        if not self.busy:
            return
        self.stop_event.set()
        self.store.update(self.active_id, status="stopping")
        if self.process and self.process.returncode is None:
            with suppress(ProcessLookupError):
                self.process.terminate()

    async def run(self, sid, settings, uploaded):
        queue = asyncio.Queue()  # Contains paths, never retained PCM buffers.
        consumer = None
        try:
            engine = await self.infer(self.recognizer.prepare, settings)
            self.store.update(sid, engine=engine)
            if self.stop_event.is_set():
                self.store.update(sid, status="completed")
                return
            consumer = asyncio.create_task(self.consume(sid, settings, queue))
            if uploaded:
                self.store.update(sid, status="transcribing")
                await self.capture_file(sid, uploaded, queue)
            else:
                await self.capture_system(sid, queue)
            self.store.update(sid, status="stopping")
            await queue.put(None)
            await consumer
            self.store.update(sid, status="completed")
        except asyncio.CancelledError:
            self.store.update(sid, status="interrupted", error="服务已停止，已完成的文字和录音仍保留。")
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
            if uploaded:
                uploaded.unlink(missing_ok=True)
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
                # Stop acquisition promptly when inference fails. The original
                # audio remains available for retry instead of silently losing text.
                self.stop()
                raise
            finally:
                chunk.path.unlink(missing_ok=True)

    async def capture_file(self, sid, uploaded, queue):
        # Decoding is blocking; use a separate producer thread, not the ASR thread.
        loop = asyncio.get_running_loop()

        def decode():
            chunker = Chunker(self.store.root / sid)
            with wave.open(str(self.store.audio_path(sid)), "wb") as output:
                output.setparams((1, 2, SAMPLE_RATE, 0, "NONE", "not compressed"))
                for pcm in normalized_pcm(uploaded):
                    if self.stop_event.is_set():
                        break
                    output.writeframesraw(pcm)
                    chunk = chunker.feed(pcm)
                    if chunk:
                        loop.call_soon_threadsafe(queue.put_nowait, chunk)
                        self.store.update(sid, duration=chunker.total / SAMPLE_RATE)
                chunk = chunker.finish()
                if chunk:
                    loop.call_soon_threadsafe(queue.put_nowait, chunk)
                self.store.update(sid, duration=chunker.total / SAMPLE_RATE)

        await asyncio.to_thread(decode)

    async def capture_system(self, sid, queue):
        import numpy as np

        self.process = await asyncio.create_subprocess_exec(
            str(CAPTURE_BINARY),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        errors = []

        async def stderr_reader():
            while True:
                line = await self.process.stderr.readline()
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
        chunker = Chunker(self.store.root / sid)
        pending = bytearray()
        try:
            with wave.open(str(self.store.audio_path(sid)), "wb") as output:
                output.setparams((1, 2, SAMPLE_RATE, 0, "NONE", "not compressed"))
                while True:
                    pcm = await self.process.stdout.read(3200)
                    if not pcm:
                        break
                    # Pipe reads may split a sample or audio frame. Fixed 100ms
                    # frames keep silence detection independent of read sizes.
                    pending.extend(pcm)
                    while len(pending) >= 3200:
                        frame = bytes(pending[:3200])
                        del pending[:3200]
                        output.writeframesraw(frame)
                        samples = np.frombuffer(frame, dtype="<i2").astype(np.float32) / 32768
                        self.level = min(1, float(np.sqrt(np.mean(samples * samples))) * 8)
                        chunk = chunker.feed(frame)
                        if chunk:
                            queue.put_nowait(chunk)
                        self.store.update(sid, duration=chunker.total / SAMPLE_RATE)
                    if chunker.total >= SAMPLE_RATE * 8 * 3600:
                        self.stop()
                if len(pending) >= 2:
                    frame = bytes(pending[: len(pending) // 2 * 2])
                    output.writeframesraw(frame)
                    chunk = chunker.feed(frame)
                    if chunk:
                        queue.put_nowait(chunk)
                chunk = chunker.finish()
                if chunk:
                    queue.put_nowait(chunk)
                self.store.update(sid, duration=chunker.total / SAMPLE_RATE)
            await self.process.wait()
            await error_task
            if not self.stop_event.is_set():
                detail = " ".join(errors[-8:])
                raise RuntimeError(detail or "系统声音采集中断。请检查屏幕与系统音频录制权限，再重试。")
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

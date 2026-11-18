import importlib.util
import math
import wave

import numpy as np

from .config import APPLE_SILICON, DATA, SAMPLE_RATE

MLX_MODELS = {
    "large-v3": "mlx-community/whisper-large-v3-mlx",
    "large-v3-turbo": "mlx-community/whisper-large-v3-turbo",
    "small": "mlx-community/whisper-small-mlx",
}


class Recognizer:
    """All model operations run on one dedicated thread, including MLX/Metal."""

    def __init__(self):
        self.model = None
        self.key = None
        self.state = {"status": "idle", "message": "首次使用需下载识别模型", "engine": None, "model": None}

    def resolve_engine(self, requested):
        if requested == "auto":
            return "mlx" if APPLE_SILICON and importlib.util.find_spec("mlx_whisper") else "faster-whisper"
        if requested == "mlx" and not APPLE_SILICON:
            raise ValueError("MLX 仅支持 Apple 芯片 Mac，请选择自动或 faster-whisper。")
        return requested

    def prepare(self, settings):
        engine = self.resolve_engine(settings.engine)
        key = (engine, settings.model)
        if self.key == key and self.state["status"] == "ready":
            return engine
        self.state = {
            "status": "loading",
            "message": "正在加载模型；首次使用会下载模型，请查看后端终端进度。",
            "engine": engine,
            "model": settings.model,
        }
        try:
            if engine == "mlx":
                import mlx.core as mx
                from mlx_whisper.transcribe import ModelHolder

                self.model = ModelHolder.get_model(MLX_MODELS[settings.model], mx.float16)
            else:
                from faster_whisper import WhisperModel

                self.model = WhisperModel(
                    settings.model,
                    device="cpu",
                    compute_type="int8",
                    download_root=str(DATA / "models" / "faster-whisper"),
                )
            self.key = key
            self.state = {
                "status": "ready",
                "message": "模型已就绪",
                "engine": engine,
                "model": settings.model,
            }
            return engine
        except Exception as exc:
            self.state = {
                "status": "error",
                "message": f"模型加载失败：{exc}",
                "engine": engine,
                "model": settings.model,
            }
            raise

    def transcribe(self, chunk, settings):
        with wave.open(str(chunk.path), "rb") as audio:
            samples = (
                np.frombuffer(audio.readframes(audio.getnframes()), dtype="<i2").astype(np.float32) / 32768
            )
        if not len(samples) or np.max(np.abs(samples)) < 0.001:
            return []

        # Use Silero to reject non-speech windows for both engines. Keep original
        # audio/timeline intact rather than concatenating speech and shifting times.
        from faster_whisper.vad import VadOptions, get_speech_timestamps

        if not get_speech_timestamps(
            samples, VadOptions(min_silence_duration_ms=500), sampling_rate=SAMPLE_RATE
        ):
            return []
        prompt = (
            "以下是课程的简体中文逐字稿，保留 English 专业术语。"
            if settings.language != "en"
            else "A lecture transcript with punctuation."
        )
        if settings.vocabulary.strip():
            prompt += " 专业术语 / vocabulary: " + settings.vocabulary.strip()
        options = dict(
            language=None if settings.language == "auto" else settings.language,
            initial_prompt=prompt,
            condition_on_previous_text=False,
            word_timestamps=True,
            temperature=0.0,
            no_speech_threshold=0.6,
        )
        if self.key[0] == "mlx":
            import mlx_whisper

            result = mlx_whisper.transcribe(
                samples, path_or_hf_repo=MLX_MODELS[settings.model], verbose=None, **options
            )
            segments = result["segments"]
        else:
            from dataclasses import asdict

            output, _ = self.model.transcribe(samples, beam_size=5, vad_filter=True, **options)
            segments = [asdict(segment) for segment in output]
        return clean_segments(segments, chunk)


def clean_segments(segments, chunk):
    cleaned = []
    for segment in segments:
        words = segment.get("words") or []
        if words:
            # Compare word midpoints to the previous chunk's coverage, avoiding
            # duplicated context text without removing legitimate repeated speech.
            words = [
                word for word in words if chunk.offset + (word["start"] + word["end"]) / 2 >= chunk.cutoff
            ]
            if not words:
                continue
            text = "".join(word["word"] for word in words).strip()
            start, end = words[0]["start"], words[-1]["end"]
        else:
            start, end, text = segment["start"], segment["end"], segment["text"].strip()
            if chunk.offset + (start + end) / 2 < chunk.cutoff:
                continue
        start = max(chunk.cutoff, chunk.offset + start)
        end = min(chunk.end, chunk.offset + end)
        if not text or end <= start:
            continue
        logprob = float(segment.get("avg_logprob", -1))
        if not math.isfinite(logprob):
            logprob = -2
        cleaned.append(
            {"start": round(start, 3), "end": round(end, 3), "text": text, "review": logprob < -0.8}
        )
    return cleaned

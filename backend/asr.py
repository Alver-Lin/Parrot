import math
import wave
from pathlib import Path

import numpy as np

from .config import APPLE_SILICON, SAMPLE_RATE

QWEN_MODELS = {
    "qwen3-0.6b": "Qwen/Qwen3-ASR-0.6B",
    "qwen3-1.7b": "Qwen/Qwen3-ASR-1.7B",
}
LEGACY_WHISPER_MODELS = {"large-v3", "large-v3-turbo", "small"}
ENGINE = "qwen3"


def normalize_model(model: str) -> str:
    if model in LEGACY_WHISPER_MODELS:
        return "qwen3-0.6b"
    return model


def cached_model_path(engine, model):
    """Inspect a complete local Qwen3-ASR snapshot without downloading."""
    from huggingface_hub import snapshot_download
    from huggingface_hub.errors import LocalEntryNotFoundError

    model = normalize_model(model)
    if engine not in (ENGINE, "auto", "mlx") or model not in QWEN_MODELS:
        return None
    try:
        path = Path(snapshot_download(QWEN_MODELS[model], local_files_only=True))
        if not (path / "config.json").is_file():
            return None
        weights = [item for item in path.rglob("*.safetensors") if item.is_file() and item.stat().st_size > 0]
        if not weights:
            return None
        return str(path)
    except (LocalEntryNotFoundError, OSError, ValueError):
        return None


def cached_models():
    return {
        "auto_engine": ENGINE,
        "downloaded": {
            ENGINE: {model: cached_model_path(ENGINE, model) is not None for model in QWEN_MODELS},
        },
    }


def _language_name(language: str) -> str | None:
    if language == "zh":
        return "Chinese"
    if language == "en":
        return "English"
    return None


def _segments_from_result(result, chunk):
    """Normalize Qwen result into Whisper-like segments for clean_segments."""
    timed = result.segments or []
    if timed:
        words = []
        for item in timed:
            text = str(item.get("text") or "").strip()
            if not text:
                continue
            words.append({
                "word": text,
                "start": float(item["start"]),
                "end": float(item["end"]),
            })
        if words:
            return [{
                "text": "".join(word["word"] for word in words).strip(),
                "start": words[0]["start"],
                "end": words[-1]["end"],
                "words": words,
                "avg_logprob": -0.5,
            }]
    text = (result.text or "").strip()
    if not text:
        return []
    # Alignment failed: assign the whole transcript to the new audio span.
    start = max(0.0, chunk.cutoff - chunk.offset)
    end = max(start + 0.05, chunk.end - chunk.offset)
    return [{"text": text, "start": start, "end": end, "words": [], "avg_logprob": -0.5}]


class Recognizer:
    """All model operations run on one dedicated thread, including MLX/Metal."""

    def __init__(self):
        self.session = None
        self.key = None
        self.model_path = None
        self.state = {"status": "idle", "message": "开始转写时自动准备识别模型", "engine": None, "model": None}

    @staticmethod
    def resolve_engine(requested):
        if not APPLE_SILICON:
            raise ValueError("Qwen3-ASR 仅支持 Apple 芯片 Mac。")
        if requested in (None, "", "auto", "mlx", "faster-whisper", ENGINE):
            return ENGINE
        raise ValueError("当前仅支持 Qwen3-ASR（Apple Silicon / MLX）。")

    def prepare(self, settings):
        engine = self.resolve_engine(settings.engine)
        model = normalize_model(settings.model)
        key = (engine, model)
        if self.key == key and self.state["status"] == "ready" and self.session is not None:
            return engine
        self.state = {
            "status": "loading",
            "message": "正在准备识别模型，请稍等片刻。",
            "engine": engine,
            "model": model,
        }
        try:
            from mlx_qwen3_asr import Session

            repo = QWEN_MODELS[model]
            self.model_path = cached_model_path(engine, model) or repo
            self.session = Session(repo)
            self.key = key
            self.state = {
                "status": "ready",
                "message": "模型已就绪",
                "engine": engine,
                "model": model,
            }
            return engine
        except Exception as exc:
            self.session = None
            self.key = None
            self.state = {
                "status": "error",
                "message": f"模型加载失败：{exc}",
                "engine": engine,
                "model": model,
            }
            raise

    def transcribe(self, chunk, settings):
        with wave.open(str(chunk.path), "rb") as audio:
            samples = (
                np.frombuffer(audio.readframes(audio.getnframes()), dtype="<i2").astype(np.float32) / 32768
            )
        if not len(samples) or np.max(np.abs(samples)) < 0.001:
            return []

        if self.session is None:
            self.prepare(settings)

        context = (
            "课程 讲义 API Redis GPU 数据库 缓存 负载均衡"
            if settings.language != "en"
            else "lecture transcript API Redis GPU"
        )
        result = self.session.transcribe(
            (samples, SAMPLE_RATE),
            language=_language_name(settings.language),
            context=context,
            return_timestamps=True,
            return_chunks=False,
        )
        return clean_segments(_segments_from_result(result, chunk), chunk)


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

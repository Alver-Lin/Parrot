import wave
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from .config import SAMPLE_RATE


@dataclass
class Chunk:
    path: Path
    offset: float
    cutoff: float
    end: float


def write_wav(path, pcm):
    with wave.open(str(path), "wb") as output:
        output.setnchannels(1)
        output.setsampwidth(2)
        output.setframerate(SAMPLE_RATE)
        output.writeframes(pcm)


class Chunker:
    """Cut near pauses after 8s, at most 24s, retain 1s context at boundaries.

    Disk-backed jobs prevent an inference backlog from retaining hours of RAM.
    cutoff assigns overlapping words to exactly one chunk.
    """

    def __init__(self, directory):
        self.directory = directory
        self.buffer = bytearray()
        self.offset = 0
        self.cutoff = 0
        self.total = 0
        self.index = 0
        self.silent_samples = 0

    def feed(self, pcm):
        if len(pcm) % 2:
            raise ValueError("PCM bytes must be aligned to 16-bit samples")
        self.buffer.extend(pcm)
        samples = np.frombuffer(pcm, dtype="<i2").astype(np.float32)
        self.total += len(samples)
        rms = float(np.sqrt(np.mean(samples * samples))) / 32768 if len(samples) else 0
        self.silent_samples = self.silent_samples + len(samples) if rms < 0.006 else 0
        duration = len(self.buffer) / 2 / SAMPLE_RATE
        if duration >= 24 or (duration >= 8 and self.silent_samples >= SAMPLE_RATE * 0.6):
            return self._flush(overlap=True)
        return None

    def finish(self):
        if self.total > self.cutoff:
            return self._flush(overlap=False)
        return None

    def _flush(self, overlap):
        path = self.directory / f"chunk-{self.index:06}.wav"
        write_wav(path, self.buffer)
        chunk = Chunk(path, self.offset / SAMPLE_RATE, self.cutoff / SAMPLE_RATE, self.total / SAMPLE_RATE)
        self.index += 1
        self.cutoff = self.total
        retained = bytes(self.buffer[-SAMPLE_RATE * 2 :]) if overlap else b""
        self.buffer = bytearray(retained)
        self.offset = self.total - len(retained) // 2
        self.silent_samples = 0
        return chunk


def normalized_pcm(path):
    """Decode local audio/video using PyAV's bundled FFmpeg libraries."""
    import av

    with av.open(str(path)) as container:
        if not container.streams.audio:
            raise ValueError("文件中没有音轨，请选择包含声音的音频或视频。")
        resampler = av.AudioResampler(format="s16", layout="mono", rate=SAMPLE_RATE)
        for frame in container.decode(audio=0):
            for converted in resampler.resample(frame):
                yield converted.to_ndarray().astype("<i2").tobytes()
        for converted in resampler.resample(None):
            yield converted.to_ndarray().astype("<i2").tobytes()

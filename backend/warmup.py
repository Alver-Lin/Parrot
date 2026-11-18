"""Download/load a model and optionally transcribe a fixture without starting capture."""

import argparse
import json
import tempfile
from pathlib import Path

from .asr import Recognizer
from .audio import Chunker, normalized_pcm
from .schemas import Settings


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", choices=["large-v3", "large-v3-turbo", "small"], default="large-v3")
    parser.add_argument("--engine", choices=["auto", "mlx", "faster-whisper"], default="auto")
    parser.add_argument("--audio", type=Path)
    parser.add_argument("--language", choices=["zh", "en", "auto"], default="zh")
    parser.add_argument("--vocabulary", default="")
    args = parser.parse_args()
    settings = Settings(
        model=args.model, engine=args.engine, language=args.language, vocabulary=args.vocabulary
    )
    recognizer = Recognizer()
    engine = recognizer.prepare(settings)
    print(f"Ready: {engine} / {args.model}", flush=True)
    if args.audio:
        with tempfile.TemporaryDirectory() as directory:
            chunker = Chunker(Path(directory))
            for pcm in normalized_pcm(args.audio):
                chunk = chunker.feed(pcm)
                if chunk:
                    print(json.dumps(recognizer.transcribe(chunk, settings), ensure_ascii=False), flush=True)
            chunk = chunker.finish()
            if chunk:
                print(json.dumps(recognizer.transcribe(chunk, settings), ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()

"""Download and load a speech model without starting system audio capture."""

import argparse

from .asr import Recognizer
from .schemas import Settings


def main():
    parser = argparse.ArgumentParser(description="预加载 Parrot 系统声音识别模型")
    parser.add_argument("--model", choices=["qwen3-0.6b", "qwen3-1.7b"], default="qwen3-0.6b")
    parser.add_argument("--engine", choices=["qwen3", "auto"], default="qwen3")
    args = parser.parse_args()
    settings = Settings(model=args.model, engine="qwen3")
    recognizer = Recognizer()
    engine = recognizer.prepare(settings)
    print(f"Ready: {engine} / {args.model}", flush=True)


if __name__ == "__main__":
    main()

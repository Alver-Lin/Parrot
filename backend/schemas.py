from typing import Literal

from pydantic import BaseModel, Field


class Settings(BaseModel):
    title: str = Field(default="未命名学习记录", min_length=1, max_length=120)
    language: Literal["auto", "zh", "en"] = "zh"
    model: Literal["large-v3", "large-v3-turbo", "small"] = "large-v3"
    engine: Literal["auto", "mlx", "faster-whisper"] = "auto"
    vocabulary: str = Field(default="", max_length=1000)


class EditSegment(BaseModel):
    text: str = Field(max_length=10000)

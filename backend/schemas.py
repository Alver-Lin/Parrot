from typing import Literal, Optional

from pydantic import BaseModel, Field


class Settings(BaseModel):
    title: str = Field(default="未命名学习记录", min_length=1, max_length=120)
    audio_source: Literal["microphone", "system", "mixed"] = "system"
    language: Literal["auto", "zh", "en"] = "zh"
    model: Literal["large-v3", "large-v3-turbo", "small"] = "large-v3"
    engine: Literal["auto", "mlx", "faster-whisper"] = "auto"


class EditSegment(BaseModel):
    text: str = Field(max_length=10000)


class EditMarkdown(BaseModel):
    content: str = Field(max_length=1000000)
    base_content: Optional[str] = Field(default=None, max_length=1000000)

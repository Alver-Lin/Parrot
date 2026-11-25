from typing import Literal, Optional

from pydantic import BaseModel, Field, field_validator


class Settings(BaseModel):
    title: str = Field(default="未命名学习记录", min_length=1, max_length=120)
    audio_source: Literal["microphone", "system", "mixed"] = "system"
    language: Literal["auto", "zh", "en"] = "zh"
    model: Literal["qwen3-0.6b", "qwen3-1.7b"] = "qwen3-0.6b"
    engine: Literal["qwen3"] = "qwen3"

    @field_validator("model", mode="before")
    @classmethod
    def coerce_legacy_model(cls, value):
        if value in {"large-v3", "large-v3-turbo", "small"}:
            return "qwen3-0.6b"
        return value

    @field_validator("engine", mode="before")
    @classmethod
    def coerce_legacy_engine(cls, value):
        if value in {None, "", "auto", "mlx", "faster-whisper", "qwen3"}:
            return "qwen3"
        return value


class EditSegment(BaseModel):
    text: str = Field(max_length=10000)


class EditMarkdown(BaseModel):
    content: str = Field(max_length=1000000)
    base_content: Optional[str] = Field(default=None, max_length=1000000)


class ApplyPolish(BaseModel):
    content: str = Field(min_length=1, max_length=1000000)
    base_content: str = Field(max_length=1000000)


class UndoPolish(BaseModel):
    revision_id: int = Field(gt=0)
    base_content: str = Field(max_length=1000000)

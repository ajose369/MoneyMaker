"""Video generation backends (pluggable, per the VideoBackend interface)."""

from __future__ import annotations

from ..config import Config
from ..llm import LLM
from .base import VideoBackend


def get_backend(cfg: Config, llm: LLM) -> VideoBackend:
    name = cfg.get("video_backend", "kenburns")
    if name == "kenburns":
        from .kenburns import KenBurnsBackend
        return KenBurnsBackend(cfg, llm)
    if name == "veo":
        from .veo import VeoBackend
        return VeoBackend(cfg, llm)
    raise ValueError(f"Unknown video_backend '{name}' (expected kenburns or veo)")

"""Pluggable image generation backends (mirrors toonpipe.backends for video)."""

from __future__ import annotations

from ..config import Config
from ..llm import LLM
from .base import ImageBackend


def get_image_backend(cfg: Config, llm: LLM) -> ImageBackend:
    name = cfg.get("image_backend", "local_sd")
    if name == "local_sd":
        from .local_sd import LocalSDBackend
        return LocalSDBackend(cfg, llm)
    if name == "gemini":
        from .gemini_backend import GeminiImageBackend
        return GeminiImageBackend(cfg, llm)
    if name == "flow":
        from .flow_bulk import FlowBulkBackend
        return FlowBulkBackend(cfg, llm)
    if name == "flow_auto":
        from .flow_playwright import FlowAutoBackend
        return FlowAutoBackend(cfg, llm)
    raise ValueError(
        f"Unknown image_backend '{name}' (expected local_sd, gemini, flow or flow_auto)"
    )

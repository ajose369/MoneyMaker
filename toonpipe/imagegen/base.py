"""ImageBackend interface — keeps model-specific code out of the QC/retry loop in images.py."""

from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path

from ..config import Config
from ..llm import LLM


class ImageBackend(ABC):
    def __init__(self, cfg: Config, llm: LLM):
        self.cfg = cfg
        self.llm = llm

    @abstractmethod
    def generate(self, prompt: str, ref_images: list[Path], out_path: Path) -> Path:
        """Produce one image at out_path. ref_images (character sheets / environment
        art) should be used for visual consistency when the backend supports it."""

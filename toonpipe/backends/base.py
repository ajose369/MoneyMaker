"""VideoBackend interface — keeps backend-specific hacks out of the core pipeline."""

from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path

from ..config import Config
from ..llm import LLM
from ..manifest import Manifest, Scene


class VideoBackend(ABC):
    def __init__(self, cfg: Config, llm: LLM):
        self.cfg = cfg
        self.llm = llm

    @abstractmethod
    def render_scene(self, m: Manifest, scene: Scene, scene_image: Path,
                     audio: Path, duration_s: float, out_path: Path) -> Path:
        """Produce one scene clip (video+narration audio muxed) at out_path."""

"""Configuration loading: config.yaml + optional per-project override + .env."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
PROJECTS_DIR = ROOT / "projects"
LOGS_DIR = ROOT / "logs"

load_dotenv(ROOT / ".env")


def _deep_merge(base: dict, override: dict) -> dict:
    out = dict(base)
    for k, v in override.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out


class Config:
    def __init__(self, data: dict[str, Any]):
        self._data = data

    def __getattr__(self, name: str) -> Any:
        try:
            return self._data[name]
        except KeyError:
            raise AttributeError(f"Config has no key '{name}'") from None

    def get(self, name: str, default: Any = None) -> Any:
        return self._data.get(name, default)

    @property
    def data(self) -> dict[str, Any]:
        return self._data

    @property
    def size(self) -> tuple[int, int]:
        return int(self._data["width"]), int(self._data["height"])

    # env-backed model overrides
    @property
    def claude_model(self) -> str:
        return os.environ.get("CLAUDE_MODEL") or self._data.get("claude_model", "claude-opus-4-8")

    @property
    def gemini_image_model(self) -> str:
        return os.environ.get("GEMINI_IMAGE_MODEL") or self._data.get(
            "gemini_image_model", "gemini-3.1-flash-image"
        )

    @property
    def gemini_text_model(self) -> str:
        return os.environ.get("GEMINI_TEXT_MODEL") or self._data.get(
            "gemini_text_model", "gemini-3.5-flash"
        )

    @property
    def veo_model(self) -> str:
        return os.environ.get("VEO_MODEL") or self._data.get(
            "veo_model", "veo-3.1-lite-generate-preview"
        )


def load_config(project_dir: Path | None = None) -> Config:
    with open(ROOT / "config.yaml", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    if project_dir:
        override_path = project_dir / "config.yaml"
        if override_path.exists():
            with open(override_path, encoding="utf-8") as f:
                data = _deep_merge(data, yaml.safe_load(f) or {})
    return Config(data)

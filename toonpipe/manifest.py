"""Manifest: the single source of truth per project (pydantic models + persistence).

Resumability model: every stage writes files into the project directory and
records state in the manifest. Stages skip work whose output already exists,
so any stage (or the whole autopilot) can be re-run safely after a failure.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from pydantic import BaseModel, Field

from .config import PROJECTS_DIR


# ---------- Story schema (also used as the LLM structured-output schema) ----------

class DialogueLine(BaseModel):
    speaker: str = Field(description="Character id, or 'narrator'")
    line: str = Field(description="The spoken line, in the target language")


class Character(BaseModel):
    id: str = Field(description="snake_case id, e.g. 'boy_ravi'")
    name: str
    design_prompt: str = Field(
        description="Detailed visual design for an image model: age, build, face, hair, "
        "clothing with exact colors, distinctive accessories. Must be specific enough to "
        "reproduce the character identically across many images."
    )
    voice: str = Field(
        description="One of: child_male, child_female, adult_male, adult_female, elder_male, elder_female"
    )


class Environment(BaseModel):
    id: str = Field(description="snake_case id, e.g. 'village_well'")
    image_prompt: str = Field(
        description="Detailed visual description of the location for an image model: "
        "layout, key objects, colors, time of day, mood."
    )


class Scene(BaseModel):
    seq: int = Field(description="1-based scene order")
    environment: str = Field(description="Environment id this scene takes place in")
    characters: list[str] = Field(description="Character ids present in this scene")
    action: str = Field(
        description="What visually happens in this scene, one or two sentences, present tense."
    )
    dialogue: list[DialogueLine] = Field(
        description="Narration and dialogue lines in order. Use speaker 'narrator' for narration."
    )
    image_prompt: str = Field(
        description="A single-frame composition prompt for this scene: which characters, "
        "their poses and expressions, camera angle, what part of the environment is visible."
    )
    video_prompt: str = Field(
        description="A short motion description for a video model (camera move, character motion)."
    )


class Story(BaseModel):
    title: str
    logline: str
    moral: str = Field(description="The lesson of the story in one sentence")
    characters: list[Character]
    environments: list[Environment]
    scenes: list[Scene]


class ReviewedStory(BaseModel):
    """Self-review output: quality verdict + the improved story."""
    issues_found: list[str] = Field(description="Concrete problems found in the draft")
    story: Story = Field(description="The improved, final story")


class ImageVerdict(BaseModel):
    approved: bool
    reason: str


class VideoMeta(BaseModel):
    title: str = Field(description="Clickable YouTube title, under 90 chars, no clickbait lies")
    description: str = Field(description="2-3 paragraph YouTube description. Do NOT include chapter timestamps; they are appended automatically.")
    tags: list[str] = Field(description="10-20 YouTube tags")
    thumbnail_text: str = Field(description="2-5 punchy words to overlay on the thumbnail")


# ---------- Runtime bookkeeping ----------

class LineTiming(BaseModel):
    speaker: str
    line: str
    start_s: float
    end_s: float


class SceneAsset(BaseModel):
    seq: int
    audio: Optional[str] = None          # relative path to scene audio wav
    duration_s: Optional[float] = None
    image: Optional[str] = None          # composited scene image
    clip: Optional[str] = None           # final scene video clip
    timings: list[LineTiming] = []


class Manifest(BaseModel):
    slug: str
    created_at: str
    topic: Optional[str] = None
    story: Optional[Story] = None
    review_issues: list[str] = []
    scene_assets: dict[str, SceneAsset] = {}   # keyed by str(seq)
    character_images: dict[str, str] = {}      # id -> relative path
    environment_images: dict[str, str] = {}
    meta: Optional[VideoMeta] = None
    chapters: list[tuple[float, str]] = []     # (start_s, label)
    final_video: Optional[str] = None
    music_track: Optional[str] = None          # filename of the bed track (for attribution)
    thumbnail: Optional[str] = None
    youtube_id: Optional[str] = None
    stage_status: dict[str, str] = {}          # stage -> done|failed

    # ---------- persistence ----------

    @property
    def dir(self) -> Path:
        return PROJECTS_DIR / self.slug

    def path_for(self, rel: str) -> Path:
        return self.dir / rel

    def save(self) -> None:
        self.dir.mkdir(parents=True, exist_ok=True)
        (self.dir / "manifest.json").write_text(
            self.model_dump_json(indent=2), encoding="utf-8"
        )

    def mark(self, stage: str, status: str = "done") -> None:
        self.stage_status[stage] = status
        self.save()

    def is_done(self, stage: str) -> bool:
        return self.stage_status.get(stage) == "done"

    @classmethod
    def load(cls, slug: str) -> "Manifest":
        p = PROJECTS_DIR / slug / "manifest.json"
        if not p.exists():
            raise FileNotFoundError(f"No manifest for project '{slug}' ({p})")
        return cls.model_validate_json(p.read_text(encoding="utf-8"))

    @classmethod
    def create(cls, slug: str) -> "Manifest":
        m = cls(slug=slug, created_at=datetime.now(timezone.utc).isoformat())
        m.save()
        for sub in ("assets/characters", "assets/environments", "assets/scenes",
                    "assets/audio", "assets/clips", "out"):
            (m.dir / sub).mkdir(parents=True, exist_ok=True)
        return m


def slugify(text: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return s[:60] or "untitled"


def next_slug(topic: str) -> str:
    base = slugify(topic)
    slug, i = base, 1
    while (PROJECTS_DIR / slug).exists():
        i += 1
        slug = f"{base}-{i}"
    return slug

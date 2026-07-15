"""Stage 1 — story engine.

Replaces the manual Claude chat AND the human review gate:
  1. pick_topic(): generates a fresh topic, avoiding everything already published
  2. generate_story(): strict-schema story with characters/environments/scenes
  3. self_review(): a second Claude pass critiques and rewrites the draft
     (narrative quality is the main YPP/monetization defense — see README)
"""

from __future__ import annotations

import json
from pathlib import Path

from pydantic import BaseModel, Field

from .config import ROOT, Config
from .llm import LLM
from .manifest import Manifest, ReviewedStory, Story

HISTORY_FILE = ROOT / "topics_history.json"

# Shared rules for the visual-prompt fields, identical across content types.
VISUAL_PROMPT_RULES = (
    "- design_prompt and image_prompt fields are written in ENGLISH for an image "
    "model: specific about colors, clothing, and layout so subjects stay "
    "identical across scenes, but CONCISE — under 40 words each, front-loading "
    "the 2-3 most visually distinctive traits first. The image model silently "
    "truncates long descriptions, so padding or filler adjectives cost real "
    "detail; every word should be load-bearing.\n"
    "- design_prompt and image_prompt describe WHAT is depicted only — the "
    "subject's shape, colors, and pose. They must NEVER specify an art medium, "
    "rendering technique, or material (no 'stop-motion', 'felted wool', "
    "'papercraft', 'claymation', '3D render', 'watercolor', 'diorama', etc.). "
    "The visual medium is fixed separately for the whole video and describing "
    "one in these fields will corrupt every image.\n"
    "- Every scene's image_prompt is ONE single moment/composition — never "
    "describe a sequence, multiple panels, or 'before and after'.\n"
)

REVIEW_VISUAL_RULES = (
    "Achieve distinctiveness through the WRITING, never by inventing an art "
    "medium or rendering style in design_prompt/image_prompt. Those fields "
    "describe only the subject's shape, colors, and pose — the visual medium is "
    "fixed centrally for the whole video and is not yours to set; do not add "
    "'stop-motion', 'felted wool', 'papercraft', 'claymation', '3D render', "
    "'watercolor', 'diorama', or any other medium/material word to them, even "
    "if the draft already contains one — strip it out. Each scene's "
    "image_prompt must stay ONE single moment/composition, never a sequence or "
    "multiple panels. Keep design_prompt/image_prompt fields under 40 words "
    "each, front-loading the most distinctive visual traits first — the image "
    "model silently truncates long descriptions."
)


def content_type(cfg: Config) -> str:
    return str(cfg.get("content_type", "story")).lower()


class TopicIdea(BaseModel):
    topic: str = Field(description="One-line story premise or explainer question")
    why_it_works: str = Field(description="Why this will hold the target audience")


def _history() -> list[str]:
    if HISTORY_FILE.exists():
        return json.loads(HISTORY_FILE.read_text(encoding="utf-8"))
    return []


def remember_topic(topic: str) -> None:
    h = _history()
    h.append(topic)
    HISTORY_FILE.write_text(json.dumps(h, indent=2, ensure_ascii=False), encoding="utf-8")


def pick_topic(cfg: Config, llm: LLM) -> str:
    used = _history()
    if content_type(cfg) == "explainer":
        system = (
            f"You are the content editor of a successful {cfg.get('language')} YouTube "
            f"channel making animated curiosity-explainer videos for {cfg.get('audience')}. "
            "You pick irresistible 'How/Why/What-if' questions about animals, the human "
            "body, space, weather, history, and everyday mysteries — the kind of question "
            "a viewer stops scrolling to have answered (e.g. 'How do dogs know an "
            "earthquake is coming before it hits?'). The answer must rest on real, "
            "verifiable facts and contain a genuinely surprising core."
        )
        what = "video topic, phrased as its central question"
    else:
        system = (
            f"You are the story editor of a successful {cfg.get('language')} YouTube channel "
            f"making {cfg.get('genre')} animated videos for {cfg.get('audience')}. "
            "You pick premises that are emotionally specific, visually stageable in a few "
            "locations, and NOT generic retellings everyone has seen."
        )
        what = "story premise"
    idea = llm.structured(
        "topic",
        system=system,
        user=(
            f"Propose ONE new {what}. Write it in ENGLISH — it is an internal working "
            f"title; the video itself will be written in {cfg.get('language')}. It must "
            "be clearly different from all of these already-used ones:\n"
            + ("\n".join(f"- {t}" for t in used[-100:]) if used else "(none yet)")
        ),
        schema=TopicIdea,
        max_tokens=2000,
    )
    return idea.topic


def generate_story(cfg: Config, llm: LLM, m: Manifest) -> Story:
    if content_type(cfg) == "explainer":
        system = (
            "You are the head writer of a hit factual-curiosity YouTube channel that "
            "answers one fascinating question per video with 2D animated visuals and "
            "a single narrator voiceover.\n"
            f"Language for ALL narration: {cfg.get('language')}.\n"
            f"Audience: {cfg.get('audience')}.\n"
            "Rules:\n"
            f"- Exactly {cfg.get('scene_count')} scenes; 1-3 recurring visual subjects "
            "(fill the characters list with them, e.g. 'the dog', 'the seismologist' — "
            "give each voice 'adult_male', it is unused); 2-4 environments.\n"
            "- EVERY dialogue line uses speaker 'narrator' — this is a voiceover "
            "explainer with no character dialogue.\n"
            "- Every scene: 2-5 narration lines, each short and natural when read "
            "aloud (aim 20-35 seconds of speech per scene).\n"
            "- Scene 1 is the cold-open hook: restate the central question vividly "
            "and tease the surprising answer within the first two lines.\n"
            "- Middle scenes unfold the real explanation step by step: one clear idea "
            "per scene, concrete examples, escalating 'wow'. Use only true, "
            "verifiable facts — NEVER invent statistics, named studies, or specific "
            "incidents; when a number is uncertain, use honest approximations "
            "('scientists believe', 'roughly').\n"
            "- Final scene: recap the answer in one memorable sentence, then a warm "
            "call to like and subscribe.\n"
            "- The moral field holds the one-sentence key takeaway of the video.\n"
            + VISUAL_PROMPT_RULES
            + "- Dramatic curiosity is welcome; nothing gruesome, distressing, or "
            "unsafe for a family audience."
        )
        user = f"Write the full explainer script for this question:\n\n{m.topic}"
    else:
        system = (
            "You are an award-winning children's screenwriter. You write complete, "
            "production-ready scripts for short 2D animated videos.\n"
            f"Language for ALL dialogue and narration: {cfg.get('language')}.\n"
            f"Audience: {cfg.get('audience')}.\n"
            "Rules:\n"
            f"- Exactly {cfg.get('scene_count')} scenes; 2-4 characters; 2-4 environments.\n"
            "- Every scene: 2-5 lines of narration/dialogue, each line short and natural "
            "when read aloud (aim 20-35 seconds of speech per scene).\n"
            "- A clear arc: setup, escalating problem, turning point, resolution, moral.\n"
            + VISUAL_PROMPT_RULES
            + "- Nothing scary, violent, branded, or unsafe for young children."
        )
        user = f"Write the full story for this premise:\n\n{m.topic}"
    story = llm.structured("story", system=system, user=user, schema=Story)
    return story


def self_review(cfg: Config, llm: LLM, m: Manifest) -> None:
    """Automated quality gate: critique + rewrite in one structured pass."""
    assert m.story
    if content_type(cfg) == "explainer":
        system = (
            "You are a ruthless script editor and fact-checker for a factual-"
            "curiosity YouTube channel. Given a draft animated explainer script as "
            "JSON, find every weakness — a weak or buried hook, factual claims that "
            "are dubious, invented, or too precise to verify (rewrite them as honest "
            "approximations or replace them with well-established facts), repetition, "
            "lines that ramble when read aloud, scenes that add no new idea, visual "
            "prompts too vague to reproduce consistently, and anything that reads as "
            "generic mass-produced AI content. Then output the IMPROVED script in "
            "the same schema (same scene count, same language for narration, and "
            "EVERY dialogue line keeps speaker 'narrator'). "
            + REVIEW_VISUAL_RULES
        )
    else:
        system = (
            "You are a ruthless story editor and children's-media compliance reviewer. "
            "Given a draft animated-video script as JSON, find every weakness — flat "
            "dialogue, unclear moral, scenes that don't advance the story, character "
            "design prompts too vague to reproduce consistently, anything unsafe for "
            "kids, and anything that reads as generic mass-produced AI content. "
            "Then output the IMPROVED story in the same schema (same scene count, "
            "same language for dialogue). Distinctiveness and emotional specificity "
            "matter: this must feel hand-crafted through the writing (plot, voice, "
            "character quirks, sensory detail in the prose). "
            + REVIEW_VISUAL_RULES
        )
    reviewed = llm.structured(
        "self_review",
        system=system,
        user="Review and improve this draft:\n\n" + m.story.model_dump_json(indent=2),
        schema=ReviewedStory,
    )
    m.review_issues = reviewed.issues_found
    m.story = reviewed.story
    m.save()
    # Human-readable script for anyone who wants to inspect afterwards.
    write_script_md(m, cfg)


def write_script_md(m: Manifest, cfg: Config | None = None) -> Path:
    assert m.story
    s = m.story
    label = "Takeaway" if cfg and content_type(cfg) == "explainer" else "Moral"
    lines = [f"# {s.title}", "", f"*{s.logline}*", "", f"**{label}:** {s.moral}", "", "## Characters"]
    for c in s.characters:
        lines += [f"- **{c.name}** (`{c.id}`, voice: {c.voice}) — {c.design_prompt}"]
    lines += ["", "## Scenes"]
    for sc in s.scenes:
        lines += ["", f"### Scene {sc.seq} — {sc.environment}", sc.action, ""]
        for d in sc.dialogue:
            lines += [f"- **{d.speaker}:** {d.line}"]
    p = m.path_for("script.md")
    p.write_text("\n".join(lines), encoding="utf-8")
    return p

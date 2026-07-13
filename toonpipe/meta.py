"""Stage 6a — metadata (title/description/tags via Claude) + thumbnail (Pillow).

Chapter timestamps are computed from the manifest, not guessed.
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from .config import Config
from .llm import LLM
from .manifest import Manifest, VideoMeta

FONT_CANDIDATES = [
    r"C:\Windows\Fonts\arialbd.ttf",
    r"C:\Windows\Fonts\seguibl.ttf",
    r"C:\Windows\Fonts\arial.ttf",
]


def generate_metadata(cfg: Config, llm: LLM, m: Manifest) -> VideoMeta:
    assert m.story
    if m.meta:
        return m.meta
    meta = llm.structured(
        "metadata",
        system=(
            f"You are a YouTube growth strategist for a {cfg.get('language')} kids' "
            "animation channel. Write metadata that is compelling and honest — high CTR "
            "without misleading claims. The description should read naturally, mention "
            "the moral, and end with a warm call to subscribe."
        ),
        user="Write YouTube metadata for this video script:\n\n" + m.story.model_dump_json(indent=2),
        schema=VideoMeta,
        max_tokens=3000,
    )
    m.meta = meta
    m.save()
    return meta


def description_with_chapters(m: Manifest) -> str:
    assert m.meta
    desc = m.meta.description.strip()
    if m.music_track:
        # Attribution for CC-BY / credit-required tracks. Name files like
        # "Artist - Track (CC-BY soundimage.org).mp3" and this line covers you.
        from pathlib import PurePath
        desc += f"\n\nMusic: {PurePath(m.music_track).stem}"
    if m.chapters:
        lines = ["", "", "Chapters:"]
        for start, label in m.chapters:
            mm, ss = divmod(int(start), 60)
            hh, mm = divmod(mm, 60)
            stamp = f"{hh}:{mm:02d}:{ss:02d}" if hh else f"{mm}:{ss:02d}"
            lines.append(f"{stamp} {label}")
        desc += "\n".join(lines)
    return desc


def _load_font(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    for path in FONT_CANDIDATES:
        if Path(path).exists():
            return ImageFont.truetype(path, size)
    return ImageFont.load_default()


def make_thumbnail(cfg: Config, m: Manifest) -> Path:
    """Best scene image + dark gradient + big title text. 1280x720 JPEG < 2MB."""
    assert m.story and m.meta
    out = m.path_for("out/thumbnail.jpg")
    if out.exists():
        m.thumbnail = "out/thumbnail.jpg"
        m.save()
        return out

    # Use the mid-story scene image (usually the most dramatic frame).
    scenes = sorted(m.scene_assets.values(), key=lambda a: a.seq)
    src = next((m.path_for(a.image) for a in scenes[len(scenes) // 2:] + scenes if a.image), None)
    if src is None:
        raise RuntimeError("No scene images available for thumbnail")

    W, H = 1280, 720
    img = Image.open(src).convert("RGB")
    scale = max(W / img.width, H / img.height)
    img = img.resize((int(img.width * scale), int(img.height * scale)))
    img = img.crop(((img.width - W) // 2, (img.height - H) // 2,
                    (img.width - W) // 2 + W, (img.height - H) // 2 + H))

    # Bottom gradient for text legibility
    grad = Image.new("L", (1, H), 0)
    for y in range(H):
        grad.putpixel((0, y), int(max(0, (y - H * 0.45) / (H * 0.55)) * 200))
    overlay = Image.new("RGB", (W, H), (0, 0, 0))
    img = Image.composite(overlay, img, grad.resize((W, H)))

    draw = ImageDraw.Draw(img)
    # Defensive: never trust the LLM to have actually followed "max 5 words" —
    # weaker/local models sometimes echo the full title instead (observed with
    # qwen3:8b, including title-separator "|" characters). Strip characters the
    # fallback font can't render (emoji show as tofu boxes) and separator
    # punctuation BEFORE splitting into words, so a stray emoji or "|" doesn't
    # waste one of the 5 word slots as empty/junk — then cap word count, so a
    # bad LLM response degrades to a shorter thumbnail, not an overflowing one.
    raw = "".join(c for c in m.meta.thumbnail_text if ord(c) < 0x2000)
    words = [w for w in raw.split() if any(ch.isalnum() for ch in w)]
    text = " ".join(words[:5]).upper()
    if not text:
        text = m.meta.title.split("|")[0].strip()[:20].upper()

    size = 130
    font = _load_font(size)
    while draw.textlength(text, font=font) > W - 120 and size > 40:
        size -= 8
        font = _load_font(size)
    # Floor hit and still too wide (very long words) — truncate rather than overflow.
    while draw.textlength(text, font=font) > W - 120 and len(text) > 4:
        text = text[:-2].rstrip() + "…"
    tw = draw.textlength(text, font=font)
    x, y = (W - tw) // 2, H - size - 60
    for dx, dy in ((-3, -3), (3, -3), (-3, 3), (3, 3), (0, 0)):
        color = (0, 0, 0) if (dx, dy) != (0, 0) else (255, 214, 0)
        draw.text((x + dx, y + dy), text, font=font, fill=color)

    img.save(out, "JPEG", quality=88)
    m.thumbnail = "out/thumbnail.jpg"
    m.save()
    return out

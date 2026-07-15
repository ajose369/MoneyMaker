"""Semi-automated image backend: Google Flow + the ZAPI FLOW Chrome extension.

Free Imagen-quality images at the cost of one ~2-minute manual step per video:

  1. The pipeline writes every needed image prompt (characters, environments,
     scenes — in that order) to <project>/flow_prompts.txt, one per line, then
     stops the stage with instructions.
  2. You open a Google Flow project (labs.google/fx/tools/flow) in Chrome,
     paste the file's contents into the ZAPI FLOW side panel (enable
     "serial numbers in filenames"), and let it bulk-generate + auto-download.
  3. Drop/point the downloads into <project>/flow_inbox/ and re-run the stage
     (or autopilot). Files are matched to prompts by serial-number order,
     moved into assets/, and the pipeline continues normally.

flow_prompts.txt always contains ONLY the still-missing prompts in canonical
order, and importing moves files out of the inbox — so partial batches and
re-runs stay consistent: whatever is in the inbox is matched, the file is
rewritten with what remains.

No reference-image conditioning (the extension submits text prompts only), so
character consistency relies on the shared style tokens and detailed design
prompts — weaker than local_sd's IP-Adapter, usually acceptable for explainer
content where subjects matter less than in character stories. Imported images
skip vision QC (they never pass through the generate() retry loop).

Caveat (reported mid-2026, verify on your first batch): free-tier Flow images
may carry a small visible Gemini-sparkle watermark in a corner; paid Google AI
plans remove it. All tiers embed invisible SynthID watermarks regardless.
"""

from __future__ import annotations

import re
from pathlib import Path

from ..config import Config
from ..manifest import Manifest
from .base import ImageBackend

PROMPTS_FILE = "flow_prompts.txt"
IMAGE_EXTS = (".png", ".jpg", ".jpeg", ".webp")


class FlowBulkBackend(ImageBackend):
    """Safety net only — with prepare_flow_images() satisfied, every output
    already exists and the QC loop never calls generate()."""

    def generate(self, prompt: str, ref_images: list[Path], out_path: Path) -> Path:
        raise RuntimeError(
            f"image_backend 'flow' cannot generate {out_path.name} directly — "
            "images come from the Flow inbox. Follow the instructions printed "
            "by the stage (paste flow_prompts.txt into ZAPI FLOW, put the "
            "downloads in flow_inbox/, re-run)."
        )


def _serial_key(p: Path) -> tuple[int, float, str]:
    m = re.search(r"\d+", p.stem)
    return (int(m.group()) if m else 10 ** 9, p.stat().st_mtime, p.name)


def _import_inbox(inbox: Path, missing: list[tuple[str, Path]]) -> int:
    """Match inbox files to missing outputs in serial order. Returns count imported."""
    files = sorted(
        (f for f in inbox.iterdir() if f.suffix.lower() in IMAGE_EXTS),
        key=_serial_key,
    )
    imported = 0
    for f, (_prompt, out) in zip(files, missing):
        out.parent.mkdir(parents=True, exist_ok=True)
        if f.suffix.lower() == ".png":
            f.replace(out)
        else:
            from PIL import Image
            Image.open(f).convert("RGB").save(out)
            f.unlink()
        imported += 1
    leftover = len(files) - imported
    if leftover > 0:
        print(f"  [flow] {leftover} extra file(s) left in {inbox.name}/ (more images than pending prompts)")
    return imported


def prepare_flow_images(cfg: Config, m: Manifest, expected: list[tuple[str, str]]) -> None:
    """Import any downloaded Flow images, then stop the stage with instructions
    if outputs are still missing. `expected` is the canonical ordered list of
    (prompt, relative output path) for ALL images the video needs, so one
    manual Flow round covers the characters, environments and scenes stages.
    """
    inbox = m.path_for(str(cfg.get("flow_inbox", "flow_inbox")))
    inbox.mkdir(parents=True, exist_ok=True)
    prompts_path = m.path_for(PROMPTS_FILE)

    missing = [(p, m.path_for(rel)) for p, rel in expected if not m.path_for(rel).exists()]
    if missing:
        n = _import_inbox(inbox, missing)
        if n:
            print(f"  [flow] imported {n} image(s) from {inbox.name}/")
        missing = [(p, out) for p, out in missing if not out.exists()]

    if not missing:
        prompts_path.unlink(missing_ok=True)
        return

    # One prompt per line — exactly what the ZAPI FLOW textarea expects.
    prompts_path.write_text(
        "\n".join(" ".join(p.split()) for p, _out in missing) + "\n",
        encoding="utf-8",
    )
    raise RuntimeError(
        f"{len(missing)} Flow image(s) pending — prompts written to {prompts_path}\n"
        "  1. Open a Google Flow project (labs.google/fx/tools/flow) in Chrome\n"
        "  2. Open the ZAPI FLOW side panel, paste the contents of flow_prompts.txt\n"
        "     (one prompt per line) and enable serial numbers in filenames\n"
        f"  3. Move the auto-downloaded images into {inbox}\n"
        "  4. Re-run this stage (or autopilot) — images are matched to prompts in\n"
        "     serial order and the pipeline resumes automatically."
    )

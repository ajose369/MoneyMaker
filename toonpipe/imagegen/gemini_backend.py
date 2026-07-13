"""Gemini image backend — requires a Google Cloud billing account linked (the
no-billing free API tier gives 0 requests/day for image-output models; text
calls remain free). Kept as an option for when the channel is monetizing and
the extra quality/consistency is worth ~$0.04-0.15/image. See README."""

from __future__ import annotations

import time
from pathlib import Path

from ..config import Config
from ..llm import LLM
from .base import ImageBackend


class GeminiImageBackend(ImageBackend):
    def __init__(self, cfg: Config, llm: LLM):
        super().__init__(cfg, llm)
        from ..gemini import GeminiPool
        self.pool = GeminiPool.shared()

    def generate(self, prompt: str, ref_images: list[Path], out_path: Path) -> Path:
        from google.genai import types

        parts: list = []
        for ref in ref_images:
            parts.append(types.Part.from_bytes(
                data=ref.read_bytes(),
                mime_type="image/png" if ref.suffix.lower() == ".png" else "image/jpeg",
            ))
        parts.append(prompt)

        def call(client):
            return client.models.generate_content(model=self.cfg.gemini_image_model, contents=parts)

        last_err: Exception | None = None
        for attempt in range(4):
            try:
                response = self.pool.run(call, what=f"image {out_path.name}")
                for part in response.candidates[0].content.parts:
                    if getattr(part, "inline_data", None) and part.inline_data.data:
                        out_path.write_bytes(part.inline_data.data)
                        self.llm.ledger.record("image", self.cfg.gemini_image_model, 0.0,
                                               {"file": out_path.name})
                        return out_path
                raise RuntimeError("Gemini returned no image data (possibly safety-filtered)")
            except Exception as e:
                last_err = e
                time.sleep(8 * (attempt + 1))
        raise RuntimeError(f"Gemini image generation failed for {out_path.name}: {last_err}")

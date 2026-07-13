"""Veo backend: real image-conditioned video generation via the Gemini API.

Paid — per-second video pricing makes this the dominant cost of each film.
Enable with `video_backend: veo` once the channel earns. Verify the current
model id and pricing at https://ai.google.dev before enabling.
"""

from __future__ import annotations

import time
from pathlib import Path

from ..ffmpeg_utils import probe_duration, run_ffmpeg
from ..manifest import Manifest, Scene
from .base import VideoBackend


class VeoBackend(VideoBackend):
    def __init__(self, cfg, llm):
        super().__init__(cfg, llm)
        from ..gemini import GeminiPool
        # Veo jobs are long-lived; use one client (first usable key) per run.
        pool = GeminiPool.shared()
        self.client = pool._client(pool.index)

    def render_scene(self, m: Manifest, scene: Scene, scene_image: Path,
                     audio: Path, duration_s: float, out_path: Path) -> Path:
        if out_path.exists():
            return out_path
        from google.genai import types

        raw = out_path.with_suffix(".veo.mp4")
        if not raw.exists():
            print(f"    [veo] submitting scene {scene.seq:03d}")
            operation = self.client.models.generate_videos(
                model=self.cfg.veo_model,
                prompt=(
                    f"{self.cfg.get('style', '2D cel animation')}. {scene.video_prompt} "
                    "Keep characters exactly as drawn in the input image."
                ),
                image=types.Image(
                    image_bytes=scene_image.read_bytes(),
                    mime_type="image/png",
                ),
                config=types.GenerateVideosConfig(
                    aspect_ratio=self.cfg.get("aspect_ratio", "16:9"),
                ),
            )
            while not operation.done:
                time.sleep(15)
                operation = self.client.operations.get(operation)
            video = operation.response.generated_videos[0]
            self.client.files.download(file=video.video)
            video.video.save(str(raw))
            self.llm.ledger.record("veo_clip", self.cfg.veo_model, 0.0,
                                   {"scene": scene.seq, "note": "add actual $ manually or via billing export"})

        # Fit the Veo clip to the narration duration (loop/trim) and mux audio.
        clip_dur = probe_duration(raw)
        w, h = self.cfg.size
        fps = int(self.cfg.get("fps", 30))
        dur = duration_s + 0.6
        loops = max(0, int(dur // max(clip_dur, 0.1)))
        run_ffmpeg([
            "-stream_loop", str(loops), "-i", str(raw),
            "-i", str(audio),
            "-filter_complex",
            f"[0:v]scale={w}:{h}:force_original_aspect_ratio=increase,"
            f"crop={w}:{h},fps={fps},format=yuv420p[v];"
            f"[1:a]aresample=44100,apad=pad_dur=0.8[a]",
            "-map", "[v]", "-map", "[a]",
            "-t", f"{dur:.3f}",
            "-c:v", "libx264", "-preset", "medium", "-crf", "19",
            "-c:a", "aac", "-b:a", "192k", "-ar", "44100", "-ac", "2",
            str(out_path),
        ])
        return out_path

"""Ken Burns backend: animate each scene image with pan/zoom via ffmpeg.

$0 per video, fully deterministic, no API. Motion pattern alternates per scene
(zoom in / zoom out / pan left / pan right) so the result doesn't feel static.
"""

from __future__ import annotations

from pathlib import Path

from ..ffmpeg_utils import run_ffmpeg
from ..manifest import Manifest, Scene
from .base import VideoBackend

TAIL_S = 0.6  # visual hold after the narration ends


class KenBurnsBackend(VideoBackend):
    def render_scene(self, m: Manifest, scene: Scene, scene_image: Path,
                     audio: Path, duration_s: float, out_path: Path) -> Path:
        if out_path.exists():
            return out_path

        w, h = self.cfg.size
        fps = int(self.cfg.get("fps", 30))
        dur = duration_s + TAIL_S
        frames = max(int(dur * fps), fps)

        mode = scene.seq % 4
        zmax = 1.18
        rate = (zmax - 1.0) / frames
        if mode == 0:      # zoom in, centered
            z = f"min(1+{rate:.6f}*on,{zmax})"
            x, y = "iw/2-(iw/zoom/2)", "ih/2-(ih/zoom/2)"
        elif mode == 1:    # zoom out
            z = f"max({zmax}-{rate:.6f}*on,1.0)"
            x, y = "iw/2-(iw/zoom/2)", "ih/2-(ih/zoom/2)"
        elif mode == 2:    # pan left -> right at fixed zoom
            z = "1.12"
            x, y = f"(iw-iw/zoom)*on/{frames}", "ih/2-(ih/zoom/2)"
        else:              # pan right -> left at fixed zoom
            z = "1.12"
            x, y = f"(iw-iw/zoom)*(1-on/{frames})", "ih/2-(ih/zoom/2)"

        # Upscale before zoompan to avoid sub-pixel jitter.
        vf = (
            f"scale={w * 2}:{h * 2}:force_original_aspect_ratio=increase,"
            f"crop={w * 2}:{h * 2},"
            f"zoompan=z='{z}':x='{x}':y='{y}':d={frames}:s={w}x{h}:fps={fps},"
            f"format=yuv420p"
        )
        run_ffmpeg([
            "-loop", "1", "-framerate", str(fps), "-i", str(scene_image),
            "-i", str(audio),
            "-filter_complex",
            f"[0:v]{vf}[v];[1:a]aresample=44100,apad=pad_dur={TAIL_S + 0.2}[a]",
            "-map", "[v]", "-map", "[a]",
            "-t", f"{dur:.3f}",
            "-c:v", "libx264", "-preset", "medium", "-crf", "19",
            "-c:a", "aac", "-b:a", "192k", "-ar", "44100", "-ac", "2",
            str(out_path),
        ])
        return out_path

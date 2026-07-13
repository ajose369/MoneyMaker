"""Stage 5 — assembly. Pure ffmpeg, fully deterministic.

concat scene clips -> add music bed (ducked by volume, loudnorm to -14 LUFS)
-> optional burned subtitles -> out/final.mp4. Also computes chapter marks.
"""

from __future__ import annotations

import random
from pathlib import Path

from .config import ROOT, Config
from .ffmpeg_utils import concat_videos, escape_filter_path, probe_duration, run_ffmpeg
from .manifest import Manifest


def _srt_timestamp(t: float) -> str:
    h, rem = divmod(t, 3600)
    mnt, sec = divmod(rem, 60)
    ms = int(round((sec - int(sec)) * 1000))
    return f"{int(h):02d}:{int(mnt):02d}:{int(sec):02d},{ms:03d}"


def _pick_music(cfg: Config) -> Path | None:
    music_dir = ROOT / str(cfg.get("music_dir", "assets/music"))
    if not music_dir.exists():
        return None
    tracks = [p for p in music_dir.rglob("*") if p.suffix.lower() in (".mp3", ".wav", ".m4a", ".ogg")]
    return random.choice(tracks) if tracks else None


def build_srt(m: Manifest) -> Path:
    """Global SRT from per-scene line timings + scene start offsets."""
    assert m.story
    entries: list[str] = []
    idx = 1
    offset = 0.0
    from .backends.kenburns import TAIL_S
    for scene in m.story.scenes:
        asset = m.scene_assets[str(scene.seq)]
        for t in asset.timings:
            entries.append(
                f"{idx}\n{_srt_timestamp(offset + t.start_s)} --> {_srt_timestamp(offset + t.end_s)}\n{t.line}\n"
            )
            idx += 1
        offset += (asset.duration_s or 0) + TAIL_S
    p = m.path_for("out/subtitles.srt")
    p.write_text("\n".join(entries), encoding="utf-8")
    return p


def compute_chapters(m: Manifest) -> None:
    assert m.story
    from .backends.kenburns import TAIL_S
    chapters: list[tuple[float, str]] = []
    offset = 0.0
    for scene in m.story.scenes:
        asset = m.scene_assets[str(scene.seq)]
        label = scene.action.split(".")[0][:60]
        chapters.append((round(offset, 1), f"Scene {scene.seq}: {label}"))
        offset += (asset.duration_s or 0) + TAIL_S
    m.chapters = chapters
    m.save()


def assemble(cfg: Config, m: Manifest) -> Path:
    assert m.story
    final = m.path_for("out/final.mp4")
    if final.exists():
        m.final_video = "out/final.mp4"
        m.save()
        return final

    clips = []
    for scene in m.story.scenes:
        asset = m.scene_assets[str(scene.seq)]
        if not asset.clip:
            raise RuntimeError(f"Scene {scene.seq} has no rendered clip — run the video stage first")
        clips.append(m.path_for(asset.clip))

    print("  [assemble] concatenating scene clips")
    joined = m.path_for("out/joined.mp4")
    concat_videos(clips, joined)

    total = probe_duration(joined)
    compute_chapters(m)

    music = _pick_music(cfg)
    if music:
        m.music_track = music.name
        m.save()
    subs_mode = cfg.get("subtitles", "burn")
    srt = build_srt(m) if subs_mode in ("burn", "srt") else None

    print(f"  [assemble] final mix (music={'yes' if music else 'no'}, subtitles={subs_mode})")
    args: list[str] = ["-i", str(joined)]
    vf = None
    if subs_mode == "burn" and srt:
        vf = f"subtitles='{escape_filter_path(srt)}':force_style='Fontsize=16,Outline=1,MarginV=28'"

    if music:
        args += ["-stream_loop", "-1", "-i", str(music)]
        afilter = (
            f"[1:a]volume={cfg.get('music_volume', 0.13)},atrim=duration={total:.3f}[m];"
            f"[0:a][m]amix=inputs=2:duration=first:normalize=0,"
            f"loudnorm=I=-14:TP=-1.5:LRA=11[aout]"
        )
        args += ["-filter_complex", (f"[0:v]{vf}[vout];" if vf else "") + afilter]
        args += ["-map", "[vout]" if vf else "0:v", "-map", "[aout]"]
    else:
        args += ["-af", "loudnorm=I=-14:TP=-1.5:LRA=11"]
        if vf:
            args += ["-vf", vf]
        args += ["-map", "0:v", "-map", "0:a"]

    args += [
        "-c:v", "libx264", "-preset", "medium", "-crf", "19",
        "-c:a", "aac", "-b:a", "192k",
        "-movflags", "+faststart",
        str(final),
    ]
    run_ffmpeg(args)
    joined.unlink(missing_ok=True)

    m.final_video = "out/final.mp4"
    m.save()
    return final

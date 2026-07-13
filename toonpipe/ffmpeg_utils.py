"""Thin ffmpeg/ffprobe wrappers (Windows-safe)."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path


def _bin(name: str) -> str:
    path = shutil.which(name)
    if not path:
        raise RuntimeError(
            f"'{name}' not found on PATH. Install FFmpeg (winget install Gyan.FFmpeg) "
            "and restart the terminal."
        )
    return path


def run_ffmpeg(args: list[str]) -> None:
    cmd = [_bin("ffmpeg"), "-y", "-hide_banner", "-loglevel", "error", *args]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(
            "ffmpeg failed:\n  " + " ".join(cmd) + "\n" + (result.stderr or "")[-3000:]
        )


def probe_duration(path: Path) -> float:
    result = subprocess.run(
        [_bin("ffprobe"), "-v", "error", "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=1", str(path)],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(f"ffprobe failed for {path}: {result.stderr}")
    return float(result.stdout.strip())


def concat_audio(parts: list[Path], gaps_ms: int, out_path: Path) -> float:
    """Concatenate audio files with a fixed silence gap between them -> wav."""
    inputs: list[str] = []
    filters: list[str] = []
    for i, p in enumerate(parts):
        inputs += ["-i", str(p)]
        filters.append(f"[{i}:a]aresample=44100,aformat=channel_layouts=mono[a{i}]")
    gap_s = gaps_ms / 1000
    chain = "".join(
        f"[a{i}]" + (f"[g{i}]" if i < len(parts) - 1 else "")
        for i in range(len(parts))
    )
    gap_defs = "".join(
        f"anullsrc=r=44100:cl=mono,atrim=duration={gap_s}[g{i}];"
        for i in range(len(parts) - 1)
    )
    filter_complex = (
        gap_defs + ";".join(filters) + ";" + chain +
        f"concat=n={2 * len(parts) - 1}:v=0:a=1[out]"
    )
    run_ffmpeg([
        *inputs, "-filter_complex", filter_complex,
        "-map", "[out]", "-c:a", "pcm_s16le", str(out_path),
    ])
    return probe_duration(out_path)


def concat_videos(clips: list[Path], out_path: Path) -> None:
    """Concatenate same-codec clips via the concat demuxer (no re-encode)."""
    list_file = out_path.with_suffix(".txt")
    lines = "\n".join(f"file '{str(c).replace(chr(39), chr(39) + chr(92) + chr(39) + chr(39))}'" for c in clips)
    list_file.write_text(lines, encoding="utf-8")
    run_ffmpeg([
        "-f", "concat", "-safe", "0", "-i", str(list_file),
        "-c", "copy", str(out_path),
    ])
    list_file.unlink(missing_ok=True)


def escape_filter_path(path: Path) -> str:
    """Escape a Windows path for use inside an ffmpeg filter argument."""
    return str(path).replace("\\", "/").replace(":", "\\:").replace("'", "\\'")

"""Free text-to-speech via edge-tts, with per-character voice assignment.

Produces one WAV per scene plus per-line timings (used for subtitles and chapters).
"""

from __future__ import annotations

import asyncio
from pathlib import Path

from .config import Config
from .ffmpeg_utils import concat_audio, probe_duration
from .manifest import LineTiming, Manifest, SceneAsset

# language -> voice archetype -> edge-tts voice
VOICES: dict[str, dict[str, str]] = {
    "english": {
        "narrator": "en-US-AriaNeural",
        "child_male": "en-US-AnaNeural",
        "child_female": "en-US-AnaNeural",
        "adult_male": "en-US-GuyNeural",
        "adult_female": "en-US-JennyNeural",
        "elder_male": "en-GB-RyanNeural",
        "elder_female": "en-GB-SoniaNeural",
    },
    "hindi": {
        "narrator": "hi-IN-SwaraNeural",
        "child_male": "hi-IN-MadhurNeural",
        "child_female": "hi-IN-SwaraNeural",
        "adult_male": "hi-IN-MadhurNeural",
        "adult_female": "hi-IN-SwaraNeural",
        "elder_male": "hi-IN-MadhurNeural",
        "elder_female": "hi-IN-SwaraNeural",
    },
    "malayalam": {
        "narrator": "ml-IN-SobhanaNeural",
        "child_male": "ml-IN-MidhunNeural",
        "child_female": "ml-IN-SobhanaNeural",
        "adult_male": "ml-IN-MidhunNeural",
        "adult_female": "ml-IN-SobhanaNeural",
        "elder_male": "ml-IN-MidhunNeural",
        "elder_female": "ml-IN-SobhanaNeural",
    },
    "tamil": {
        "narrator": "ta-IN-PallaviNeural",
        "child_male": "ta-IN-ValluvarNeural",
        "child_female": "ta-IN-PallaviNeural",
        "adult_male": "ta-IN-ValluvarNeural",
        "adult_female": "ta-IN-PallaviNeural",
        "elder_male": "ta-IN-ValluvarNeural",
        "elder_female": "ta-IN-PallaviNeural",
    },
}

# Slight pitch offsets so two characters sharing a voice still sound distinct.
PITCHES = ["+0Hz", "+18Hz", "-15Hz", "+35Hz", "-30Hz", "+8Hz"]


def _voice_for(cfg: Config, m: Manifest, speaker: str) -> tuple[str, str]:
    lang = str(cfg.get("language", "English")).lower()
    table = VOICES.get(lang, VOICES["english"])
    if speaker == "narrator" or not m.story:
        return str(cfg.get("narrator_voice") or table["narrator"]), "+0Hz"
    for i, ch in enumerate(m.story.characters):
        if ch.id == speaker:
            return table.get(ch.voice, table["adult_male"]), PITCHES[i % len(PITCHES)]
    return table["narrator"], "+0Hz"


async def _synth(text: str, voice: str, rate: str, pitch: str, out_path: Path) -> None:
    import edge_tts
    communicate = edge_tts.Communicate(text, voice, rate=rate, pitch=pitch)
    await communicate.save(str(out_path))


def _run_async(coro) -> None:
    """Run a coroutine to completion whether or not this thread already has a
    running event loop. The flow_auto image backend leaves Playwright's sync-API
    loop running on the main thread, so a plain asyncio.run() here raises
    'cannot be called from a running event loop' — fall back to a private loop
    in a worker thread in that case."""
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        asyncio.run(coro)
        return
    import threading
    err: list[BaseException] = []

    def worker() -> None:
        try:
            asyncio.run(coro)
        except BaseException as e:  # surface failures from the worker thread
            err.append(e)

    t = threading.Thread(target=worker)
    t.start()
    t.join()
    if err:
        raise err[0]


def _synth_omnivoice(cfg: Config, text: str, speaker: str, m: Manifest, out_path: Path) -> bool:
    """Local TTS via OmniVoice-Studio's OpenAI-compatible API (localhost:3900).

    Returns False if the server isn't reachable so the caller can fall back to
    edge-tts — the autopilot must never stall on an optional local service.
    Voice mapping: config `omnivoice_voices: {narrator: ..., child_male: ..., default: ...}`.
    """
    import requests as rq
    base = str(cfg.get("omnivoice_url", "http://localhost:3900/v1")).rstrip("/")
    voices: dict = cfg.get("omnivoice_voices") or {}
    archetype = "narrator"
    if m.story and speaker != "narrator":
        for ch in m.story.characters:
            if ch.id == speaker:
                archetype = ch.voice
                break
    voice = voices.get(archetype) or voices.get("default") or "default"
    try:
        r = rq.post(
            f"{base}/audio/speech",
            json={
                "model": cfg.get("omnivoice_model", "omnivoice"),
                "voice": voice,
                "input": text,
                "response_format": "mp3",
            },
            timeout=300,
        )
        r.raise_for_status()
        out_path.write_bytes(r.content)
        return True
    except Exception as e:
        print(f"    [tts] OmniVoice unavailable ({str(e)[:60]}) — falling back to edge-tts")
        return False


def build_scene_audio(cfg: Config, m: Manifest) -> None:
    """Synthesize every dialogue line, concat per scene, record timings."""
    assert m.story
    gap_ms = int(cfg.get("line_gap_ms", 400))
    rate = str(cfg.get("tts_rate", "-5%"))
    tmp_dir = m.path_for("assets/audio/lines")
    tmp_dir.mkdir(parents=True, exist_ok=True)

    for scene in m.story.scenes:
        key = str(scene.seq)
        asset = m.scene_assets.get(key) or SceneAsset(seq=scene.seq)
        rel = f"assets/audio/scene_{scene.seq:03d}.wav"
        out = m.path_for(rel)
        if out.exists() and asset.audio:
            continue
        print(f"  [audio] scene {scene.seq:03d} ({len(scene.dialogue)} lines)")

        engine = str(cfg.get("tts_engine", "edge")).lower()
        line_files: list[Path] = []
        for i, dl in enumerate(scene.dialogue):
            lf = tmp_dir / f"s{scene.seq:03d}_l{i:02d}.mp3"
            if not lf.exists():
                done = False
                if engine == "omnivoice":
                    done = _synth_omnivoice(cfg, dl.line, dl.speaker, m, lf)
                if not done:
                    voice, pitch = _voice_for(cfg, m, dl.speaker)
                    _run_async(_synth(dl.line, voice, rate, pitch, lf))
            line_files.append(lf)

        if not line_files:
            continue
        concat_audio(line_files, gap_ms, out)

        # Per-line timings (line duration + fixed gap)
        timings: list[LineTiming] = []
        t = 0.0
        for i, (dl, lf) in enumerate(zip(scene.dialogue, line_files)):
            d = probe_duration(lf)
            timings.append(LineTiming(speaker=dl.speaker, line=dl.line,
                                      start_s=round(t, 3), end_s=round(t + d, 3)))
            t += d + (gap_ms / 1000 if i < len(line_files) - 1 else 0)

        asset.audio = rel
        asset.duration_s = round(probe_duration(out), 3)
        asset.timings = timings
        m.scene_assets[key] = asset
        m.save()

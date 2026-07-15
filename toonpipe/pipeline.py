"""Pipeline orchestrator: individual stages + the fully-automatic autopilot."""

from __future__ import annotations

import traceback
from datetime import datetime

from . import assemble as assemble_mod
from . import meta as meta_mod
from . import publish as publish_mod
from . import story as story_mod
from .backends import get_backend
from .config import Config, load_config, PROJECTS_DIR
from .images import ImageGen
from .llm import LLM, Ledger
from .manifest import Manifest, next_slug
from .tts import build_scene_audio

STAGES = ["story", "characters", "environments", "scene_images",
          "audio", "video", "assemble", "metadata", "publish"]


def make_llm(cfg: Config, m: Manifest) -> LLM:
    ledger = Ledger(m.dir, float(cfg.get("max_cost_usd_per_video", 3.0)))
    return LLM(cfg, ledger)


def run_stage(stage: str, m: Manifest, cfg: Config) -> None:
    llm = make_llm(cfg, m)
    print(f"[{datetime.now():%H:%M:%S}] stage: {stage}")

    if stage == "story":
        if m.story and m.is_done("story"):
            print("  (already done)")
            return
        if not m.topic:
            m.topic = story_mod.pick_topic(cfg, llm)
            story_mod.remember_topic(m.topic)
            m.save()
            print(f"  topic: {m.topic}")
        m.story = story_mod.generate_story(cfg, llm, m)
        m.save()
        if cfg.get("quality_gate", "auto") == "auto":
            print("  self-reviewing script…")
            story_mod.self_review(cfg, llm, m)
            if m.review_issues:
                print("  review fixed: " + "; ".join(m.review_issues[:5]))
        else:
            story_mod.write_script_md(m, cfg)
            print(f"  MANUAL GATE: edit {m.path_for('script.md')} / manifest.json, "
                  "then run the next stage.")
        m.mark("story")

    elif stage == "characters":
        ImageGen(cfg, llm).characters(m)
        m.mark("characters")

    elif stage == "environments":
        ImageGen(cfg, llm).environments(m)
        m.mark("environments")

    elif stage == "scene_images":
        ImageGen(cfg, llm).scene_images(m)
        m.mark("scene_images")

    elif stage == "audio":
        build_scene_audio(cfg, m)
        m.mark("audio")

    elif stage == "video":
        backend = get_backend(cfg, llm)
        assert m.story
        for scene in m.story.scenes:
            asset = m.scene_assets[str(scene.seq)]
            rel = f"assets/clips/scene_{scene.seq:03d}.mp4"
            out = m.path_for(rel)
            if not out.exists():
                print(f"  [video] scene {scene.seq:03d} ({cfg.get('video_backend')})")
                if not (asset.image and asset.audio and asset.duration_s):
                    raise RuntimeError(f"Scene {scene.seq} missing image/audio — run earlier stages")
                backend.render_scene(m, scene, m.path_for(asset.image),
                                     m.path_for(asset.audio), asset.duration_s, out)
            asset.clip = rel
            m.scene_assets[str(scene.seq)] = asset
            m.save()
        m.mark("video")

    elif stage == "assemble":
        final = assemble_mod.assemble(cfg, m)
        print(f"  final video: {final}")
        m.mark("assemble")

    elif stage == "metadata":
        meta_mod.generate_metadata(cfg, llm, m)
        meta_mod.make_thumbnail(cfg, m)
        print(f"  title: {m.meta.title}")
        m.mark("metadata")

    elif stage == "publish":
        if not cfg.get("publish", {}).get("enabled", True):
            print("  publishing disabled in config — skipping")
            return
        publish_mod.upload(cfg, m)
        m.mark("publish")

    else:
        raise ValueError(f"Unknown stage '{stage}'. Valid: {', '.join(STAGES)}")


def autopilot(topic: str | None = None, slug: str | None = None) -> Manifest:
    """One command: topic -> story -> assets -> video -> upload. No human input."""
    if slug:
        m = Manifest.load(slug)
    else:
        cfg0 = load_config()
        if topic is None:
            from .config import LOGS_DIR
            LOGS_DIR.mkdir(exist_ok=True)
            llm = LLM(cfg0, Ledger(LOGS_DIR, 5.0))  # topic-pick cost logged globally
            topic = story_mod.pick_topic(cfg0, llm)
        m = Manifest.create(next_slug(topic))
        m.topic = topic
        story_mod.remember_topic(topic)
        m.save()

    cfg = load_config(m.dir)
    print(f"=== autopilot: {m.slug} ===")
    for stage in STAGES:
        if m.is_done(stage):
            print(f"[skip] {stage} (done)")
            continue
        try:
            run_stage(stage, m, cfg)
        except Exception:
            m.mark(stage, "failed")
            print(f"\nStage '{stage}' FAILED — project is resumable with:\n"
                  f"  python -m toonpipe run {stage} --slug {m.slug}\n")
            traceback.print_exc()
            raise
    print(f"=== autopilot complete: {m.slug} ===")
    return m


def check() -> bool:
    """Doctor: validate every dependency the autopilot needs. Never prints secrets."""
    import shutil
    ok = True

    def report(label: str, good: bool, note: str = "") -> None:
        nonlocal ok
        ok = ok and good
        print(f"  [{'OK' if good else 'FAIL'}] {label}" + (f" — {note}" if note else ""))

    cfg = load_config()
    print("toonpipe check:")

    report("ffmpeg", shutil.which("ffmpeg") is not None,
           "" if shutil.which("ffmpeg") else "winget install Gyan.FFmpeg, then restart terminal")

    image_backend = cfg.get("image_backend", "local_sd")
    provider = cfg.get("llm_provider", "gemini")

    # Gemini keys are needed for the LLM (provider=gemini) and/or the gemini
    # image backend, and always for image QC (Gemini vision, free even in
    # local_sd/ollama mode) — check them whenever either is relevant.
    if provider == "gemini" or image_backend == "gemini" or provider != "claude":
        try:
            from .gemini import GeminiPool
            results = GeminiPool.shared().check()
            good = [r for r in results if r[1]]
            for idx, k_ok, note in results:
                report(f"gemini key #{idx}", k_ok, note if not k_ok else "")
            if not good:
                report("gemini keys", False, "no working key")
        except Exception as e:
            report("gemini keys", False, str(e)[:120])

    if image_backend == "local_sd":
        try:
            import torch
            cuda_ok = torch.cuda.is_available()
            note = torch.cuda.get_device_name(0) if cuda_ok else "no CUDA GPU detected — will run on CPU (very slow)"
            report("local_sd: torch", True, note)
            if not cuda_ok and cfg.get("local_sd_device", "cuda") == "cuda":
                report("local_sd: GPU", False, "config wants cuda but torch sees no GPU")
        except ImportError:
            report("local_sd: torch", False,
                   "not installed — see requirements-local-image.txt for setup")
        try:
            import diffusers  # noqa: F401
            report("local_sd: diffusers", True)
        except ImportError:
            report("local_sd: diffusers", False, "pip install -r requirements-local-image.txt")
    elif image_backend == "gemini":
        print("  [NOTE] gemini image backend needs a Google Cloud billing account "
              "linked to work — a 429 error with 'limit: 0' during the characters "
              "stage means it isn't. This can't be checked without spending money, "
              "so it isn't included in the pass/fail above.")
    elif image_backend == "flow":
        print("  [NOTE] flow image backend is semi-automated: each video pauses at "
              "the characters stage until you run the flow_prompts.txt batch "
              "through the ZAPI FLOW extension and drop the downloads into the "
              "project's flow_inbox/ folder.")
    elif image_backend == "flow_auto":
        try:
            import playwright  # noqa: F401
            report("flow_auto: playwright", True)
        except ImportError:
            report("flow_auto: playwright", False, "pip install playwright")
        from .config import ROOT as _root
        profile = _root / str(cfg.get("flow_profile_dir", ".flow_profile"))
        signed_in = profile.exists() and any(profile.iterdir())
        report("flow_auto: signed-in profile", signed_in,
               "" if signed_in else "run: python -m toonpipe flow-login")
    if provider == "ollama":
        try:
            import requests as rq
            tags = rq.get(f"{cfg.get('ollama_url')}/api/tags", timeout=5).json()
            names = [t["name"] for t in tags.get("models", [])]
            want = cfg.get("ollama_model", "qwen3:8b")
            report(f"ollama ({want})", any(want in n for n in names),
                   "" if any(want in n for n in names)
                   else f"run: ollama pull {want} — models present: {names or 'none'}")
        except Exception:
            report("ollama", False, "server not reachable — install/launch Ollama")
    elif provider == "claude":
        import os
        report("ANTHROPIC_API_KEY", bool(os.environ.get("ANTHROPIC_API_KEY")),
               "" if os.environ.get("ANTHROPIC_API_KEY") else "empty in .env")

    if cfg.get("tts_engine", "edge") == "omnivoice":
        try:
            import requests as rq
            rq.get(str(cfg.get("omnivoice_url", "http://localhost:3900/v1")).rstrip("/") + "/models",
                   timeout=5)
            report("omnivoice", True)
        except Exception:
            report("omnivoice", False, "not running — will fall back to edge-tts (not fatal)")

    if cfg.get("publish", {}).get("enabled", True):
        try:
            from .publish import get_credentials
            get_credentials(cfg, interactive=False)
            report("youtube auth", True)
        except Exception as e:
            report("youtube auth", False, str(e)[:110])

    from .config import ROOT
    music_dir = ROOT / str(cfg.get("music_dir", "assets/music"))
    tracks = [p for p in music_dir.rglob("*") if p.suffix.lower() in (".mp3", ".wav", ".m4a", ".ogg")] \
        if music_dir.exists() else []
    report("music tracks", True, f"{len(tracks)} found" + ("" if tracks else " — videos will have no music (see assets/music/README.md)"))

    print("Result:", "READY — run: python -m toonpipe autopilot" if ok else "fix the FAIL items above")
    return ok


def status() -> None:
    if not PROJECTS_DIR.exists():
        print("No projects yet.")
        return
    for p in sorted(PROJECTS_DIR.iterdir()):
        mf = p / "manifest.json"
        if not mf.exists():
            continue
        m = Manifest.load(p.name)
        done = [s for s in STAGES if m.is_done(s)]
        failed = [s for s, v in m.stage_status.items() if v == "failed"]
        yt = f" https://youtu.be/{m.youtube_id}" if m.youtube_id else ""
        cost = Ledger(m.dir, 1e9).total()
        print(f"{p.name:45s} {len(done)}/{len(STAGES)} stages"
              + (f"  FAILED:{','.join(failed)}" if failed else "")
              + f"  ${cost:.2f}{yt}")

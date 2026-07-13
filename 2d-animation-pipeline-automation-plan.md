# Work Plan: Automating the AI 2D Animated Movie Pipeline

**Source:** "I Used Claude AI (FREE) + Gemini OMNI to Make a 2D Animated Movie" (zapiwala ai, Jun 2026)
**Goal:** Convert the video's manual copy-paste workflow into a one-command, manifest-driven pipeline that goes from a single config file to an upload-ready animated video.
**Owner:** Aldrin · **Stack:** Python 3.12, FastAPI-optional, SQLite/JSON manifest, ffmpeg, Playwright — all tools you already use.

---

## 1. The workflow as shown in the video (what we're automating)

The creator's process has seven manual stages:

1. Paste a "master prompt" into Claude free plan; choose language, duration, story type.
2. Claude outputs a full story, dialogue-only script, character design prompts, and environment prompts.
3. Manually copy each character prompt into Gemini (image model) to create character sheets.
4. Manually generate environment/location images the same way.
5. Feed image + scene-prompt pairs into Google Flow to generate ~45 scene video clips, using their free "Zapi Flow" Chrome extension for bulk submission.
6. Download all clips, manually order/sync/edit them into the final film.
7. Ask Claude to generate viral title, description, and tags; upload manually.

The human is the message bus between four tools. That's the automation target. The genuinely hard part — character consistency across 45 clips — is solved by the *reference-image* approach (character sheet + environment image passed into each video generation), which is fully automatable.

---

## 2. Target architecture

A single Python CLI orchestrator (call it `toonpipe`) with idempotent, resumable stages. Each project lives in `projects/<slug>/` with a `manifest.json` as the single source of truth. Every stage reads the manifest, does its work, writes results back, and can be re-run safely (skip-if-done, retry-if-failed).

```
toonpipe new "village-moral-story-01"
toonpipe run story        # Stage 1
toonpipe review script    # human gate
toonpipe run characters   # Stage 2
toonpipe run environments # Stage 3
toonpipe run scenes       # Stage 4 (the big one)
toonpipe run assemble     # Stage 5
toonpipe run publish      # Stage 6
```

State machine per asset: `pending → generating → review → approved → done | failed(retries)`.

---

## 3. Stage-by-stage plan

### Stage 0 — Project scaffold (build first)
`config.yaml` per project: language (English/Hindi/Malayalam/Tamil), target duration, story genre, scene count, aspect ratio, style tokens (e.g. "2D cel animation, flat colors, thick outlines"). A `manifest.json` schema (pydantic models) covering characters, environments, scenes, and output files. This replaces the video's "choose your options in the master prompt" step.

### Stage 1 — Story engine (replaces manual Claude chat)
One structured call to the Claude API (or Ollama locally as a zero-cost fallback — you already run it) with a system prompt adapted from the creator's master prompt, but demanding strict JSON output:

```json
{
  "title": "...",
  "logline": "...",
  "characters": [{"id": "boy_ravi", "design_prompt": "...", "voice": "child_male"}],
  "environments": [{"id": "village_well", "image_prompt": "..."}],
  "scenes": [{
    "seq": 1, "environment": "village_well",
    "characters": ["boy_ravi"],
    "dialogue": [{"speaker": "boy_ravi", "line": "..."}],
    "video_prompt": "...", "duration_s": 8
  }]
}
```

Validate with pydantic; auto-retry with error feedback if the JSON fails schema. **Human gate:** render the script to a readable Markdown file and approve/edit before spending any image/video credits. This is where you inject taste — the one thing worth keeping manual.

### Stage 2 — Character sheets (replaces manual Gemini image gen)
Loop over `characters[]`, call the Gemini API image model (free tier exists with daily quotas; verify current model name and limits at build time — the video's "OMNI" branding may map to a specific model ID). For each character generate a turnaround sheet: front/side/back views plus 3–4 expressions, using identical style tokens from config so all characters share one visual language. Save as `assets/characters/<id>.png` and record in manifest. **Human gate:** quick approve/regenerate loop per character (a tiny FastAPI review page with approve/reject buttons is a 2-hour build and worth it).

### Stage 3 — Environments
Identical machinery to Stage 2, looping `environments[]`. Batch both stages together against the daily free quota; if quota is the bottleneck, the orchestrator sleeps and resumes next day automatically (this is exactly what manifest-driven resumability buys you).

### Stage 4 — Scene video generation (the critical decision)
Each scene needs: character sheet(s) + environment image + video prompt → one 5–8s clip. Three routes, in ascending cost and descending fragility:

**Option A — Keep the Chrome extension, automate around it (start here).** The pipeline pre-generates a bulk-import file (CSV/JSON of prompt + reference image paths) formatted for the Zapi Flow extension, and post-processes the downloaded clips (watch a downloads folder, rename to `scene_001.mp4` by matching prompt text or order, register in manifest). Semi-automated, zero cost, lowest effort.

**Option B — Playwright automation of Google Flow.** Fully headless submission using a persisted login session. Technically straightforward for you, but brittle (UI changes break it) and sits in a gray zone with Google's ToS — account suspension risk on a Google account you presumably care about. If you go this way, use a dedicated account and conservative rate limiting.

**Option C — Veo via the Gemini/Vertex API (paid, production-grade).** Reference-image-conditioned video generation through a real API: async job submission, polling, retries, no browser. This is the only route that scales cleanly, but per-second video pricing makes it the dominant cost of each film — check current Veo API pricing before committing; at typical rates, 45 clips × ~8s is real money per video, so it only makes sense once a channel is earning.

**Recommendation:** Build the pipeline API-shaped from day one (a `VideoBackend` interface), implement Option A first, keep Option C as a drop-in backend for later. Never let the Flow-specific hack leak into the core.

### Stage 5 — Assembly (replaces manual editing)
Pure ffmpeg, fully deterministic:

1. Normalize all clips (fps, resolution, codec) — Flow/Veo outputs can vary.
2. Concat in manifest `seq` order via ffmpeg concat demuxer.
3. Dialogue audio: if the generated clips lack usable speech, synthesize per-line TTS (Gemini TTS or edge-tts free) and overlay per scene with timing from the manifest; optionally burn subtitles (also great for Hindi/Malayalam audiences).
4. Background music bed (royalty-free library folder, random pick per genre), ducked under dialogue, loudness-normalized to −14 LUFS.
5. Export `final.mp4` + auto-generate a thumbnail frame candidate set.

Use moviepy only if ffmpeg filter graphs get too painful; raw ffmpeg is faster for batch work.

### Stage 6 — Metadata & publish
One Claude API call taking the script as input → title, description, tags, chapter timestamps (you already have per-scene durations in the manifest, so timestamps are computed, not guessed). Thumbnail: composite the best character render + title text with Pillow. Upload via YouTube Data API v3 (OAuth once, refresh token stored; one upload costs 1600 quota units against a 10,000/day default — plenty). Publish as *private* first, with a final human gate before flipping to public.

### Stage 7 — Ops layer
Per-video cost ledger (API tokens, video seconds), structured logging, a `toonpipe status` dashboard command, and optionally a cron/Celery weekly trigger once the pipeline is trusted. Given your Redis/Celery familiarity this is trivial — but resist building it before Stages 1–6 work end-to-end once.

---

## 4. Build schedule (weekend-sized, pre-IIST realistic)

| Session | Deliverable |
|---|---|
| Weekend 1 (Sat) | Scaffold + Stage 1 story engine with schema validation and review gate |
| Weekend 1 (Sun) | Stages 2–3 image generation loops + approval mini-UI |
| Weekend 2 (Sat) | Stage 4 Option A: bulk-file generator + download watcher/renamer |
| Weekend 2 (Sun) | Stage 5 ffmpeg assembly, end-to-end test → first full video |
| Weekend 3 | Stage 6 metadata + YouTube upload; publish video #1 |
| Weekend 4 (optional) | Hardening: retries, cost ledger, second video fully hands-off except gates |

Total: ~5–6 focused days. After that, marginal effort per video drops to roughly 30–60 minutes of review-gate decisions.

---

## 5. Risks and honest caveats

**YouTube monetization policy is the biggest business risk, not the tech.** YouTube's inauthentic/mass-produced content rules specifically target repetitious AI-generated channels. Distinctive stories, real narrative quality at the Stage-1 gate, and human-voiced or well-produced audio materially change your standing versus fully templated output. Verify the current YPP policy before investing in volume.

**Free-tier dependence is fragile.** The whole "100% free" pitch rests on Flow credits and Gemini free quotas that Google can change any day. The API-shaped backend design (Section 3, Stage 4) is your hedge.

**Character consistency is good, not perfect,** with reference-image conditioning. Budget for ~10–20% clip regeneration; the manifest retry states handle this automatically.

**Language advantage:** you can generate Malayalam/Tamil kids' moral stories — far less saturated niches than English/Hindi, and culturally native to you. That's a real differentiator over the video's audience.

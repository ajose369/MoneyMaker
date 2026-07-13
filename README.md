# toonpipe — fully automatic 2D animated YouTube video bot

One command turns a topic (or nothing at all) into an uploaded YouTube video:

```
python -m toonpipe autopilot
```

**Pipeline:** pick fresh topic → an LLM (Gemini/Ollama/Claude — your choice) writes
the story (strict JSON), then self-reviews and rewrites it (automated quality gate)
→ a local Stable Diffusion XL + IP-Adapter model generates character sheets,
environments, and one composited frame per scene ($0, runs on your GPU;
reference-image conditioning nudges characters toward consistency) → Gemini vision
auto-approves or regenerates each image → free `edge-tts` voices every line
(per-character voices, English/Hindi/Malayalam/Tamil) → ffmpeg animates each frame
with Ken Burns motion synced to the audio → assembly adds a music bed, subtitles,
−14 LUFS loudness → the LLM writes title/description/tags (chapters computed from
real timings) → Pillow builds the thumbnail → YouTube Data API uploads it.

Everything is manifest-driven and resumable: re-running any stage (or autopilot)
skips finished work, so quota limits or crashes just mean "run it again later."

---

## One-time setup (~20 minutes)

Python 3.12 and FFmpeg are already installed on this machine.

### 1. API keys → `.env`  ✅ (already done)

- `GEMINI_API_KEY` — from https://aistudio.google.com/apikey. **Multiple keys are
  supported, comma-separated** — the pipeline rotates to the next key on quota
  errors. Yours are already in `.env`. Used for story/metadata text generation
  and image QC (both free) — **not** for image generation (see below).
- `ANTHROPIC_API_KEY` — optional; only needed if you set `llm_provider: claude`
  for the highest writing quality.

Run `python -m toonpipe check` any time to validate every key and dependency
(secrets are never printed).

> ⚠️ **Important finding from testing:** Google's Gemini API free tier gives
> **zero** requests/day for image-*generation* models (confirmed via a live
> `limit: 0` quota error — a Google Cloud billing account must be linked to use
> `gemini-2.5/3.1-flash-image` at all, even one request; text models like
> `gemini-3.5-flash` remain free). Because of this, **image generation defaults
> to a local Stable Diffusion backend (`image_backend: local_sd`, $0, runs on
> your GPU)** instead of Gemini. See step 1b below and the roadmap section for
> the tradeoffs. If you'd rather pay ~$0.04–0.15/image for stronger character
> consistency, link billing on your Google Cloud project and set
> `image_backend: gemini` in `config.yaml`.

### 1b. Local image generation setup (Stable Diffusion XL)

```
pip install --index-url https://download.pytorch.org/whl/cu128 torch torchvision
pip install -r requirements-local-image.txt
```

The `cu128` index is required for RTX 50-series (Blackwell/`sm_120`) GPUs — older
GPUs can use the default PyPI `torch` build instead. First run downloads the SDXL
base model + IP-Adapter weights from Hugging Face (~7GB, one-time, cached after).
`python -m toonpipe check` confirms torch sees your GPU before you run anything.

### 2. YouTube upload credentials

1. https://console.cloud.google.com → create a project → enable **YouTube Data API v3**.
2. *APIs & Services → OAuth consent screen*: External, add yourself as a test user.
3. *Credentials → Create credentials → OAuth client ID → Desktop app* → download the
   JSON and save it as `client_secret.json` in this folder.
4. Run once (opens a browser, sign in with the channel account):
   ```
   python -m toonpipe auth
   ```
   The refresh token is saved to `yt_token.json` — uploads run unattended after this.

### 3. Music (optional but recommended)

Drop tracks into `assets/music/` — **see `assets/music/README.md`** for the vetted
list of 12 legal free sources (StreamBeats, Scott Buckley, incompetech, soundimage,
NCS, …) and their license conditions. One track is picked at random per video; its
filename is credited automatically in the video description, so name files like
`Artist - Track (CC-BY soundimage.org).mp3` and attribution is handled for you.

### 4. Test it

```
python -m toonpipe autopilot --topic "A shy firefly who is afraid of the dark"
```

First run costs a few Claude/Gemini calls; check `projects/<slug>/out/final.mp4`
and the printed YouTube link.

### 5. Go hands-off

```
.\schedule_daily.ps1 -Time "09:00"
```

registers a Windows Task Scheduler job that produces and uploads one video per
day. Logs land in `logs\`. Remove with `schtasks /Delete /TN ToonPipeDaily /F`.

---

## Commands

| Command | What it does |
|---|---|
| `python -m toonpipe autopilot` | full run, auto-generated topic |
| `python -m toonpipe autopilot --topic "..."` | full run with your premise |
| `python -m toonpipe autopilot --slug <slug>` | resume a failed/partial project |
| `python -m toonpipe new "topic"` | create a project without running it |
| `python -m toonpipe run <stage> --slug <slug>` | run one stage (`story`, `characters`, `environments`, `scene_images`, `audio`, `video`, `assemble`, `metadata`, `publish`, `all`) |
| `python -m toonpipe status` | progress + cost of every project |
| `python -m toonpipe check` | validate keys, ffmpeg, ollama, YouTube auth, music |
| `python -m toonpipe auth` | one-time YouTube OAuth |

## Choosing the brain: gemini / ollama / claude (`llm_provider`)

Story writing, self-review, metadata, and image QC all go through one provider:

| Provider | Cost | Quality | Setup |
|---|---|---|---|
| `gemini` (**current default**) | $0 (free-tier key pool) | very good, strong Indic languages | none — your keys are in `.env` |
| `ollama` (your local-LLM plan) | $0, fully local | okay (8B model on your 8GB GPU); weaker Malayalam/Tamil dialogue | installed; `qwen3:8b` is downloading — then set `llm_provider: ollama` |
| `claude` | ~$0.5–1.5/video | best writing | add `ANTHROPIC_API_KEY` |

Notes for `ollama` mode: image QC automatically stays on Gemini vision (free), so
quality control survives the switch. Local 8B models occasionally produce invalid
JSON — the pipeline auto-retries with the validation error fed back, but if a story
stage fails repeatedly, switch that run back to `gemini`.

Gemini model choices (from ai.google.dev/gemini-api/docs/models, verified 2026-07):
`gemini-3.5-flash` (text, free tier). Image models (`gemini-3.1-flash-image` =
Nano Banana 2, `gemini-3-pro-image` = Nano Banana Pro for 4K) and video
(`veo-3.1-lite-generate-preview`) all require billing — see `image_backend` above
and `video_backend` below. All model IDs are set in `config.yaml`.

## Choosing the eyes: `image_backend`

| Backend | Cost | Consistency | Setup |
|---|---|---|---|
| `local_sd` (**current default**) | $0 (your GPU) | good for single-character scenes; weaker on multi-character scenes (IP-Adapter blends references rather than truly compositing them like Gemini's multi-image chat did) | torch + diffusers (step 1b above) |
| `gemini` | ~$0.04–0.15/image | strong — true multi-reference-image conditioning | Google Cloud billing account linked |

`local_sd` uses Stable Diffusion XL + IP-Adapter: character sheets and environment
art condition scene generation the same way Gemini's reference images did, just
with a less precise mechanism. Expect more `image_qc` regenerations on scenes with
2+ characters — this is a real quality tradeoff for $0, not a bug. If a channel
starts earning, switching to `gemini` is a one-line config change.

## Configuration (`config.yaml`)

Highlights (a project can override any of these with its own `projects/<slug>/config.yaml`):

- `language`: English / Hindi / **Malayalam / Tamil** — the less-saturated niches
  from your plan; dialogue, TTS voices, and metadata all follow it.
- `scene_count`, `aspect_ratio` (`9:16` makes Shorts), `style`.
- `video_backend`: `kenburns` (default, $0, ffmpeg motion over Gemini frames) or
  `veo` (real AI video via the Gemini API — paid; the `VideoBackend` interface
  means switching is one config line, exactly as the plan's Option C intended).
- `publish.privacy`: `public` (default, fully hands-off) or `private` if you want
  a manual safety check before videos go live.
- `quality_gate`: `auto` (Claude self-review) or `manual` (writes `script.md` and
  waits for you between stages).
- `max_cost_usd_per_video`: hard budget cap — the run aborts if the per-project
  ledger (`projects/<slug>/ledger.jsonl`) crosses it.

## Local-model roadmap (assessed for this machine: RTX 5060 Laptop 8GB / 16GB RAM)

- **OmniVoice-Studio** (github.com/debpalash/OmniVoice-Studio) — **fits your
  hardware** (min 8GB RAM / 4GB VRAM) and is already wired in: install their
  desktop app, clone/design voices, then set `tts_engine: omnivoice` and map
  voices in `config.yaml` (`omnivoice_voices`). The pipeline calls its local
  OpenAI-compatible API (`localhost:3900/v1`) and **falls back to edge-tts
  automatically** if the app isn't running, so autopilot never stalls. AGPL-3.0,
  free for commercial use. This is the biggest quality upgrade available to you
  for $0 — cloned narrator voices beat stock TTS for YPP "produced content."
- **Wan2.2** (github.com/Wan-Video/Wan2.2) — open-source video generation; would
  be a true free local Veo alternative, **but not on this laptop**: the smallest
  model (TI2V-5B, 720p@24fps) targets 24GB-VRAM GPUs (~9 min per 5-second clip on
  a 4090); a 5-minute video ≈ 60 clips ≈ days of rendering, and 8GB VRAM needs
  heavy quantization (ComfyUI GGUF builds) at much lower quality. Revisit if you
  get a desktop GPU or rent one (RunPod/Vast ~$0.3–0.6/hr) — then it slots in as
  a new `VideoBackend` (`toonpipe/backends/`) with zero changes to the rest of
  the pipeline. Until then, `veo` (3.1 Lite API) is the paid upgrade path.
- **HeyGem** (github.com/Caladog/HeyGem, fork of Duix-Avatar) — offline talking-head
  avatars (clone a face+voice, drive it with text/audio). Below your machine's
  spec (wants RTX 4070+, 32GB RAM, 100GB+ Docker images) and it produces realistic
  human presenters, which doesn't fit the 2D-cartoon format. Where it would shine
  later: a separate "storyteller" channel format (avatar narrator + story imagery),
  or intro/outro segments. It exposes a local REST API, so integration would follow
  the same pattern as OmniVoice.

## Design notes / honest caveats

- **Why Ken Burns instead of browser automation:** the plan's Option A (Chrome
  extension) needs a human and Option B (Playwright on Google Flow) risks account
  suspension. Animated-storybook style is fully automatic, free, ToS-clean, and a
  proven kids-content format. When revenue justifies it, flip `video_backend: veo`.
- **Monetization risk is policy, not tech.** YouTube's inauthentic-content rules
  target repetitious AI channels. The self-review gate, distinct topics
  (`topics_history.json` prevents repeats), per-character voices, and subtitles
  all push toward "produced content," but review the current YPP policy and put
  real taste into `config.yaml`'s style/genre. Consider starting with
  `publish.privacy: private` for the first few videos and spot-checking them.
- **Made for kids** is declared per `config.yaml` (COPPA requirement for kids'
  content; note personalized ads are disabled on such videos).
- **Model names drift.** All Gemini/Veo model ids live in `config.yaml` / `.env` —
  if Google renames them (e.g. adds/drops a `-preview` suffix), update one line.
  `python -m toonpipe check` will show auth vs. model errors distinctly.
- **Free-tier quotas (text/LLM only):** if the Gemini text model hits a rate limit,
  the project just pauses — `autopilot --slug <slug>` (or the next scheduled run)
  picks up where it stopped. Image generation on `local_sd` has no quota at all
  (it's your GPU); on `gemini` it's billed per-image, not quota-limited.

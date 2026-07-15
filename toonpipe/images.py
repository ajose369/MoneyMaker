"""Image generation orchestration (backend-agnostic).

Character consistency strategy (fully automated version of the video's manual flow):
  1. Generate one character *sheet* per character (turnaround + expressions).
  2. Generate one image per environment.
  3. Generate one composited image per scene, passing the relevant character
     sheets + environment image as REFERENCE IMAGES so the backend keeps
     characters on-model across every scene.

The actual generation call is delegated to an ImageBackend (see toonpipe/imagegen/):
`local_sd` (default — free, local Stable Diffusion + IP-Adapter) or `gemini`
(needs Google Cloud billing enabled; stronger consistency).

Every image goes through vision QC (auto approve/regenerate) unless disabled.
"""

from __future__ import annotations

from pathlib import Path

from .config import Config
from .imagegen import get_image_backend
from .llm import LLM
from .manifest import Manifest


class ImageGen:
    def __init__(self, cfg: Config, llm: LLM):
        self.cfg = cfg
        self.llm = llm
        self.backend = get_image_backend(cfg, llm)

    # ---------- QC wrapper ----------

    def _generate_with_qc(self, prompt: str, refs: list[Path], out_path: Path,
                          expectation: str) -> Path:
        if out_path.exists():
            return out_path
        retries = int(self.cfg.get("image_qc_max_retries", 2)) if self.cfg.get("image_qc", True) else 0
        for attempt in range(retries + 1):
            self.backend.generate(prompt, refs, out_path)
            if not self.cfg.get("image_qc", True):
                return out_path
            verdict = self.llm.vision_verdict(out_path, expectation)
            if verdict.approved:
                return out_path
            print(f"    [qc] rejected {out_path.name} ({verdict.reason}) — regenerating")
            prompt = prompt + f"\n\nAvoid this problem from the previous attempt: {verdict.reason}"
            out_path.unlink(missing_ok=True)
        # After exhausting retries, generate once more and accept it — the show must go on.
        return self.backend.generate(prompt, refs, out_path)

    # ---------- shared bits ----------

    def _style(self) -> str:
        # In every template below this lands after a ~40-word design/image
        # prompt, deep into local_sd's 77-token CLIP budget — keep this short
        # (under ~25 tokens) or it gets silently truncated away and generation
        # falls back to SDXL's default (photoreal/diorama-ish) look.
        return self.cfg.get("style", "2D cel animation, flat colors")

    def _ar_text(self) -> str:
        # Gemini has no width/height parameter — this is its only aspect-ratio
        # hint, so keep it (low priority: local_sd ignores it, using real
        # width/height instead; placed last so CLIP truncation drops it first).
        return "wide 16:9 landscape" if self.cfg.get("aspect_ratio", "16:9") == "16:9" \
            else "tall 9:16 vertical"

    # ---------- prompt templates (shared by the stage loops and the flow export) ----------

    def _character_prompt(self, ch) -> str:
        # A SINGLE clean full-body pose, not a multi-view turnaround grid:
        # reference-image conditioning (both Gemini's and IP-Adapter's) tends
        # to leak the *composition* of the reference into every generated
        # scene, so a multi-panel sheet gets reproduced as multiple copies
        # of the character instead of one character in the scene.
        #
        # Style FIRST, identity second, framing boilerplate LAST: SDXL's CLIP
        # text encoder silently truncates at 77 tokens, and a ~40-word
        # design_prompt alone can eat most of that budget — measured with
        # the real tokenizer, style: "..." placed after the description
        # routinely got truncated away entirely, silently falling back to
        # SDXL's default (photoreal/diorama-ish) look. Style is short by
        # convention (see _style()) so it always survives; if anything
        # gets dropped it's the trailing framing text — harmless for
        # local_sd, which ignores {self._ar_text()} anyway (uses real
        # width/height instead).
        return (
            f"{self._style()}. {ch.name}: {ch.design_prompt} "
            f"Single full-body character illustration, three-quarter view, standing "
            f"neutral pose, plain light background, {self._ar_text()} image."
        )

    def _environment_prompt(self, env) -> str:
        return (
            f"{self._style()}. {env.image_prompt} "
            f"Background environment art, no characters, no text, "
            f"{self._ar_text()} image."
        )

    def _scene_prompt(self, scene, with_refs: bool) -> str:
        ref_text = (
            "Match the attached reference images (background location, then character "
            "designs). " if with_refs else ""
        )
        return (
            f"{self._style()}. {scene.image_prompt} "
            f"One finished animation frame, {self._ar_text()}. "
            f"{ref_text}No text, no watermarks, no panel borders."
        )

    # ---------- flow (semi-automated) support ----------

    def _flow_prepare_if_needed(self, m: Manifest) -> None:
        """image_backend 'flow' gets all images from one manual ZAPI FLOW round:
        import whatever is in the inbox, or halt the stage with instructions."""
        if self.cfg.get("image_backend", "local_sd") != "flow":
            return
        assert m.story
        from .imagegen.flow_bulk import prepare_flow_images
        expected: list[tuple[str, str]] = []
        for ch in m.story.characters:
            expected.append((self._character_prompt(ch), f"assets/characters/{ch.id}.png"))
        for env in m.story.environments:
            expected.append((self._environment_prompt(env), f"assets/environments/{env.id}.png"))
        for scene in m.story.scenes:
            expected.append((self._scene_prompt(scene, with_refs=False),
                             f"assets/scenes/scene_{scene.seq:03d}.png"))
        prepare_flow_images(self.cfg, m, expected)

    # ---------- stages ----------

    def characters(self, m: Manifest) -> None:
        assert m.story
        self._flow_prepare_if_needed(m)
        for ch in m.story.characters:
            rel = f"assets/characters/{ch.id}.png"
            out = m.path_for(rel)
            prompt = self._character_prompt(ch)
            print(f"  [characters] {ch.id}")
            self._generate_with_qc(prompt, [], out,
                                   f"A single full-body illustration of one cartoon character: {ch.design_prompt}")
            m.character_images[ch.id] = rel
            m.save()

    def environments(self, m: Manifest) -> None:
        assert m.story
        self._flow_prepare_if_needed(m)
        for env in m.story.environments:
            rel = f"assets/environments/{env.id}.png"
            out = m.path_for(rel)
            prompt = self._environment_prompt(env)
            print(f"  [environments] {env.id}")
            self._generate_with_qc(prompt, [], out,
                                   f"An empty cartoon background of: {env.image_prompt}")
            m.environment_images[env.id] = rel
            m.save()

    def scene_images(self, m: Manifest) -> None:
        assert m.story
        self._flow_prepare_if_needed(m)
        from .manifest import SceneAsset
        for scene in m.story.scenes:
            key = str(scene.seq)
            asset = m.scene_assets.get(key) or SceneAsset(seq=scene.seq)
            rel = f"assets/scenes/scene_{scene.seq:03d}.png"
            out = m.path_for(rel)
            refs = [m.path_for(m.environment_images[scene.environment])] if scene.environment in m.environment_images else []
            for cid in scene.characters:
                if cid in m.character_images:
                    refs.append(m.path_for(m.character_images[cid]))
            prompt = self._scene_prompt(scene, with_refs=bool(refs))
            print(f"  [scenes] scene {scene.seq:03d}")
            self._generate_with_qc(prompt, refs, out, f"A cartoon frame showing: {scene.image_prompt}")
            asset.image = rel
            m.scene_assets[key] = asset
            m.save()

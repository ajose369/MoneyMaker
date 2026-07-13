"""Local Stable Diffusion XL backend — $0 per image, runs on your RTX 5060 (8GB VRAM).

Character/environment consistency uses IP-Adapter: when reference images are
given (a character sheet, an environment) they're encoded and used to bias the
generation toward that visual identity. This is a real but WEAKER consistency
mechanism than Gemini's true multi-image reference conditioning — expect more
image_qc rejections/regenerations on multi-character scenes. Honest tradeoff
for $0 and full local control; switch `image_backend: gemini` if you later
enable Google Cloud billing and want stronger consistency.

Model and pipeline are loaded lazily and cached process-wide (one load per
`toonpipe` invocation, reused across every character/environment/scene image).
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image

from ..config import Config
from ..llm import LLM
from .base import ImageBackend

# SDXL's officially-trained resolution buckets (w, h) — picking the closest one
# for the configured aspect ratio gives noticeably better composition/quality
# than an arbitrary resolution.
SDXL_BUCKETS = [
    (1024, 1024), (1152, 896), (896, 1152), (1216, 832), (832, 1216),
    (1344, 768), (768, 1344), (1536, 640), (640, 1536),
]

# Ordered by observed failure frequency, not alphabetically: the negative prompt
# has its own ~77-token CLIP budget and can itself be truncated, so the two
# defects actually seen in testing (multi-panel layout, wrong art medium) come
# first — generic SD boilerplate (anatomy/quality) is lower priority here.
NEGATIVE_PROMPT = (
    "comic panel, multiple panels, panel border, grid layout, collage, contact "
    "sheet, storyboard, split screen, multiple views, inset image, "
    "claymation, stop motion, felt, felted wool, papercraft, paper cutout, "
    "diorama, plasticine, watercolor, anime, manga, photorealistic, photo, "
    "3d render, blurry, low quality, jpeg artifacts, extra limbs, fused "
    "fingers, mutated hands, deformed, disfigured, bad anatomy, watermark, "
    "signature, text, caption, logo, cropped, out of frame, ugly, duplicate, "
    "scary, violent, gore"
)


def _closest_bucket(aspect_ratio: str) -> tuple[int, int]:
    try:
        w_str, h_str = aspect_ratio.split(":")
        target = float(w_str) / float(h_str)
    except (ValueError, ZeroDivisionError):
        target = 16 / 9
    return min(SDXL_BUCKETS, key=lambda wh: abs(wh[0] / wh[1] - target))


class LocalSDBackend(ImageBackend):
    _pipe = None  # shared across instances within one process
    # diffusers requires an ip_adapter_image whenever the adapter is attached —
    # used with scale=0.0 for calls that have no real reference images.
    _NEUTRAL_IMAGE = Image.new("RGB", (224, 224), (128, 128, 128))

    def __init__(self, cfg: Config, llm: LLM):
        super().__init__(cfg, llm)
        self.width, self.height = _closest_bucket(str(cfg.get("aspect_ratio", "16:9")))

    # ---------- lazy pipeline load ----------

    def _pipeline(self):
        if LocalSDBackend._pipe is not None:
            return LocalSDBackend._pipe

        import torch
        from diffusers import StableDiffusionXLPipeline

        model_id = self.cfg.get("local_sd_model", "stabilityai/stable-diffusion-xl-base-1.0")
        device = self.cfg.get("local_sd_device", "cuda")
        if device == "cuda" and not torch.cuda.is_available():
            print("    [local_sd] CUDA not available — falling back to CPU (will be VERY slow)")
            device = "cpu"

        print(f"    [local_sd] loading {model_id} (first call only, may take a few minutes)…")
        dtype = torch.float16 if device == "cuda" else torch.float32
        pipe = StableDiffusionXLPipeline.from_pretrained(
            model_id, torch_dtype=dtype, variant="fp16" if device == "cuda" else None,
            use_safetensors=True,
        )

        # IP-Adapter for reference-image conditioning (character/environment consistency).
        pipe.load_ip_adapter(
            "h94/IP-Adapter", subfolder="sdxl_models", weight_name="ip-adapter_sdxl.bin",
        )
        pipe.set_ip_adapter_scale(float(self.cfg.get("local_sd_ip_adapter_scale", 0.55)))

        if device == "cuda":
            # 8GB VRAM: offload idle components to system RAM instead of keeping
            # the whole pipeline resident — trades speed for headroom.
            pipe.enable_model_cpu_offload()
            pipe.enable_vae_slicing()
            pipe.enable_vae_tiling()
        else:
            pipe.to(device)

        LocalSDBackend._pipe = pipe
        return pipe

    # ---------- generation ----------

    def generate(self, prompt: str, ref_images: list[Path], out_path: Path) -> Path:
        pipe = self._pipeline()
        steps = int(self.cfg.get("local_sd_steps", 28))
        guidance = float(self.cfg.get("local_sd_guidance", 6.5))

        kwargs: dict = dict(
            prompt=prompt,
            negative_prompt=NEGATIVE_PROMPT,
            width=self.width,
            height=self.height,
            num_inference_steps=steps,
            guidance_scale=guidance,
        )
        # ip_adapter_image must be a list with one entry PER LOADED ADAPTER (we
        # load exactly one), and that entry is itself a list of reference
        # images for it — i.e. [[img1, img2, ...]], not a flat [img1, img2].
        # Passing a flat list is misread as "one image per adapter" and errors
        # as soon as more than one reference image is given.
        if ref_images:
            # Multiple references are averaged by the single IP-Adapter — a
            # soft approximation of "match all of these," not per-image control.
            kwargs["ip_adapter_image"] = [[Image.open(p).convert("RGB") for p in ref_images]]
            pipe.set_ip_adapter_scale(float(self.cfg.get("local_sd_ip_adapter_scale", 0.55)))
        else:
            # diffusers requires an ip_adapter_image whenever the adapter is
            # attached, even with zero influence — use a neutral placeholder.
            kwargs["ip_adapter_image"] = [[self._NEUTRAL_IMAGE]]
            pipe.set_ip_adapter_scale(0.0)

        result = pipe(**kwargs)

        result.images[0].save(out_path)
        self.llm.ledger.record("image", "local_sd", 0.0, {"file": out_path.name})
        return out_path

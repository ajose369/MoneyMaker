"""LLM layer: Claude API, Gemini API, or Ollama (free local).

Providers (config `llm_provider`):
  - claude  — best writing quality; needs ANTHROPIC_API_KEY
  - gemini  — free-tier via the same GEMINI_API_KEY pool used for images
  - nvidia  — free NIM catalogue (build.nvidia.com); needs NVIDIA_API_KEY.
              Big models (qwen3.5-122b) without Gemini's free-tier transient
              error storms — the most reliable free option.
  - ollama  — fully local (e.g. qwen3:8b); needs `ollama serve` running

Provides:
  - structured(): schema-validated generation (story, review, metadata)
  - vision_verdict(): image QC (Claude vision, or Gemini vision for non-claude
    providers) — auto approve/regenerate, no human in the loop
  - a per-project cost ledger (ledger.jsonl) with a hard budget cap
"""

from __future__ import annotations

import base64
import json
import os
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import TypeVar

import requests
from pydantic import BaseModel, ValidationError

from .config import Config
from .manifest import ImageVerdict

T = TypeVar("T", bound=BaseModel)


def _extract_json(text: str) -> str:
    """Pull the JSON object out of a chat reply that may wrap it in reasoning
    (<think>…</think>), markdown fences, or chatter. Open-weight models do all
    three even when told not to."""
    t = re.sub(r"<think>.*?</think>", "", text, flags=re.S).strip()
    fence = re.search(r"```(?:json)?\s*(.*?)```", t, flags=re.S)
    if fence:
        t = fence.group(1).strip()
    start, end = t.find("{"), t.rfind("}")
    return t[start:end + 1] if start != -1 and end > start else t

# USD per 1M tokens (input, output) — used for the ledger only.
PRICES = {
    "claude-opus-4-8": (5.00, 25.00),
    "claude-opus-4-7": (5.00, 25.00),
    "claude-sonnet-5": (3.00, 15.00),
    "claude-haiku-4-5": (1.00, 5.00),
}


class BudgetExceeded(RuntimeError):
    pass


class Ledger:
    def __init__(self, project_dir: Path, max_cost_usd: float):
        self.path = project_dir / "ledger.jsonl"
        self.max_cost_usd = max_cost_usd

    def total(self) -> float:
        if not self.path.exists():
            return 0.0
        total = 0.0
        for line in self.path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                total += json.loads(line).get("cost_usd", 0.0)
        return total

    def record(self, kind: str, model: str, cost_usd: float, detail: dict | None = None) -> None:
        entry = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "kind": kind,
            "model": model,
            "cost_usd": round(cost_usd, 6),
            **(detail or {}),
        }
        with open(self.path, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry) + "\n")
        if self.total() > self.max_cost_usd:
            raise BudgetExceeded(
                f"Project cost ${self.total():.2f} exceeded cap "
                f"${self.max_cost_usd:.2f} (see {self.path})"
            )


class LLM:
    def __init__(self, cfg: Config, ledger: Ledger):
        self.cfg = cfg
        self.ledger = ledger
        self.provider = cfg.get("llm_provider", "claude")
        self._client = None

    # ---------- Claude ----------

    @property
    def client(self):
        if self._client is None:
            import anthropic
            # Zero-arg client: resolves ANTHROPIC_API_KEY, ANTHROPIC_AUTH_TOKEN,
            # or an `ant auth login` profile automatically.
            self._client = anthropic.Anthropic()
        return self._client

    def _record_usage(self, kind: str, response) -> None:
        model = getattr(response, "model", self.cfg.claude_model)
        usage = getattr(response, "usage", None)
        cost = 0.0
        detail = {}
        if usage:
            price = next(
                (p for key, p in PRICES.items() if str(model).startswith(key)),
                (5.00, 25.00),
            )
            in_tok = (usage.input_tokens or 0) + (getattr(usage, "cache_creation_input_tokens", 0) or 0)
            out_tok = usage.output_tokens or 0
            cost = in_tok / 1e6 * price[0] + out_tok / 1e6 * price[1]
            detail = {"input_tokens": in_tok, "output_tokens": out_tok}
        self.ledger.record(kind, str(model), cost, detail)

    def structured(self, kind: str, system: str, user: str, schema: type[T],
                   max_tokens: int = 16000) -> T:
        """Generate a schema-validated object."""
        if self.provider == "ollama":
            return self._ollama_structured(kind, system, user, schema)
        if self.provider == "gemini":
            return self._gemini_structured(kind, system, user, schema)
        if self.provider == "nvidia":
            return self._nvidia_structured(kind, system, user, schema, max_tokens)

        last_err: Exception | None = None
        for attempt in range(3):
            try:
                response = self.client.messages.parse(
                    model=self.cfg.claude_model,
                    max_tokens=max_tokens,
                    system=system,
                    messages=[{"role": "user", "content": user}],
                    output_format=schema,
                )
                self._record_usage(kind, response)
                if response.stop_reason == "refusal":
                    raise RuntimeError(f"Claude refused the '{kind}' request")
                if response.parsed_output is None:
                    raise ValueError("No parsed output returned")
                return response.parsed_output
            except (ValidationError, ValueError) as e:
                last_err = e
                user = user + f"\n\nYour previous output failed validation: {e}. Fix it."
            except Exception as e:
                last_err = e
                time.sleep(5 * (attempt + 1))
        raise RuntimeError(f"Structured generation '{kind}' failed after retries: {last_err}")

    QC_SYSTEM = (
        "You are an art director QC-ing frames for a 2D animated children's video. "
        "Approve images that reasonably match the brief and are free of obvious "
        "generation defects (mangled anatomy, garbled text, wrong character count, "
        "photorealism when a cartoon was requested). Be pragmatic — minor style "
        "drift is fine; reject only real problems."
    )

    def vision_verdict(self, image_path: Path, expectation: str) -> ImageVerdict:
        """Ask a vision model whether a generated image matches its prompt."""
        if self.provider != "claude":
            # Gemini vision QC — same key pool as image generation, $0 free tier.
            return self._gemini_vision(image_path, expectation)

        data = base64.standard_b64encode(image_path.read_bytes()).decode()
        media = "image/png" if image_path.suffix.lower() == ".png" else "image/jpeg"
        response = self.client.messages.parse(
            model=self.cfg.claude_model,
            max_tokens=1024,
            system=self.QC_SYSTEM,
            messages=[{
                "role": "user",
                "content": [
                    {"type": "image", "source": {"type": "base64", "media_type": media, "data": data}},
                    {"type": "text", "text": f"The brief for this image was:\n{expectation}\n\nApprove or reject."},
                ],
            }],
            output_format=ImageVerdict,
        )
        self._record_usage("image_qc", response)
        return response.parsed_output or ImageVerdict(approved=True, reason="parse failed; defaulting to approve")

    # ---------- Gemini provider (free-tier, uses the same key pool as images) ----------

    @property
    def _pool(self):
        from .gemini import GeminiPool
        return GeminiPool.shared()

    def _gemini_structured(self, kind: str, system: str, user: str, schema: type[T]) -> T:
        model = self.cfg.gemini_text_model
        last_err: Exception | None = None
        for _ in range(3):
            def call(client):
                return client.models.generate_content(
                    model=model,
                    contents=user,
                    config={
                        "system_instruction": system,
                        "response_mime_type": "application/json",
                        "response_schema": schema,
                    },
                )
            try:
                resp = self._pool.run(call, what=kind)
                obj = getattr(resp, "parsed", None)
                if obj is None:
                    obj = schema.model_validate_json(resp.text)
                self.ledger.record(kind, model, 0.0, {"note": "gemini free tier"})
                return obj
            except ValidationError as e:
                last_err = e
                user = user + f"\n\nYour previous JSON failed validation: {e}. Output corrected JSON only."
            except Exception as e:
                last_err = e
                time.sleep(5)
        raise RuntimeError(f"Gemini structured generation '{kind}' failed: {last_err}")

    def _gemini_vision(self, image_path: Path, expectation: str) -> ImageVerdict:
        from google.genai import types
        model = self.cfg.gemini_text_model
        mime = "image/png" if image_path.suffix.lower() == ".png" else "image/jpeg"

        def call(client):
            return client.models.generate_content(
                model=model,
                contents=[
                    types.Part.from_bytes(data=image_path.read_bytes(), mime_type=mime),
                    f"{self.QC_SYSTEM}\n\nThe brief for this image was:\n{expectation}\n\nApprove or reject.",
                ],
                config={
                    "response_mime_type": "application/json",
                    "response_schema": ImageVerdict,
                },
            )
        try:
            # QC fails open on any error, so a Gemini outage should cost seconds,
            # not the full patient retry budget — same outcome either way.
            resp = self._pool.run(call, what="image_qc", max_transient_rounds=2)
            obj = getattr(resp, "parsed", None) or ImageVerdict.model_validate_json(resp.text)
            self.ledger.record("image_qc", model, 0.0)
            return obj
        except Exception as e:
            # QC must never block the pipeline — fail open.
            return ImageVerdict(approved=True, reason=f"vision QC unavailable ({str(e)[:80]})")

    # ---------- NVIDIA NIM (free cloud, OpenAI-compatible) ----------

    def _nvidia_structured(self, kind: str, system: str, user: str, schema: type[T],
                           max_tokens: int) -> T:
        """Structured output via build.nvidia.com's OpenAI-compatible endpoint.

        NIM exposes no schema-constrained decoding, so the schema goes in the
        system prompt and the reply is validated with pydantic — the existing
        retry-on-ValidationError loop handles slips. Model choice matters: pick
        one that answers in plain content (qwen3.5-122b does); some catalogue
        entries return empty content or 404 'not found for account' even though
        they are listed.
        """
        key = os.environ.get("NVIDIA_API_KEY", "").strip()
        if not key:
            raise RuntimeError("NVIDIA_API_KEY is not set — add it to .env")
        url = str(self.cfg.get("nvidia_url", "https://integrate.api.nvidia.com/v1")).rstrip("/")
        model = str(self.cfg.get("nvidia_model", "qwen/qwen3.5-122b-a10b"))
        sys_prompt = (
            f"{system}\n\nRespond with ONLY one JSON object matching this schema. "
            f"No prose, no markdown fences.\nSCHEMA:\n{json.dumps(schema.model_json_schema())}"
        )
        last_err: Exception | None = None
        for _ in range(3):
            try:
                r = requests.post(
                    f"{url}/chat/completions",
                    headers={"Authorization": f"Bearer {key}",
                             "Content-Type": "application/json"},
                    json={
                        "model": model,
                        "messages": [{"role": "system", "content": sys_prompt},
                                     {"role": "user", "content": user}],
                        "max_tokens": max_tokens,
                        "temperature": 0.7,
                    },
                    timeout=(15, 600),
                )
                r.raise_for_status()
                content = (r.json()["choices"][0]["message"].get("content") or "").strip()
                if not content:
                    raise ValueError(f"empty content from {model} (a reasoning-only reply?)")
                obj = schema.model_validate_json(_extract_json(content))
                self.ledger.record(kind, f"nvidia:{model}", 0.0, {"note": "nvidia nim free tier"})
                return obj
            except ValidationError as e:
                last_err = e
                user += f"\n\nYour previous JSON failed validation: {e}. Output corrected JSON only."
            except Exception as e:
                last_err = e
                time.sleep(5)
        raise RuntimeError(f"NVIDIA structured generation '{kind}' failed: {last_err}")

    # ---------- Ollama fallback (free, local) ----------

    def _ollama_structured(self, kind: str, system: str, user: str, schema: type[T]) -> T:
        url = self.cfg.get("ollama_url", "http://localhost:11434").rstrip("/")
        last_err: Exception | None = None
        # Thinking models (qwen3, deepseek-r1, …) burn minutes of <think> tokens
        # before emitting the schema-constrained JSON — measured 22s with
        # thinking off vs >240s (timeout) with it on for a trivial schema on
        # qwen3:8b. Disable it; models that reject the option get one retry
        # without the flag.
        send_think = True
        for _ in range(3):
            body = {
                "model": self.cfg.get("ollama_model", "llama3.1"),
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
                "format": schema.model_json_schema(),
                "stream": False,
                "options": {"num_ctx": 16384},
            }
            if send_think:
                body["think"] = False
            r = requests.post(f"{url}/api/chat", json=body, timeout=600)
            if send_think and r.status_code == 400 and "think" in r.text.lower():
                send_think = False
                body.pop("think")
                r = requests.post(f"{url}/api/chat", json=body, timeout=600)
            r.raise_for_status()
            content = r.json()["message"]["content"]
            try:
                obj = schema.model_validate_json(content)
                self.ledger.record(kind, "ollama:" + self.cfg.get("ollama_model", ""), 0.0)
                return obj
            except ValidationError as e:
                last_err = e
                user = user + f"\n\nYour previous JSON failed validation: {e}. Output corrected JSON only."
        raise RuntimeError(f"Ollama structured generation '{kind}' failed: {last_err}")

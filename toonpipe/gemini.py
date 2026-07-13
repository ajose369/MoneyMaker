"""Gemini API key pool.

GEMINI_API_KEY in .env may contain SEVERAL keys separated by commas — the pool
rotates to the next key automatically on quota/auth errors, which multiplies
the free-tier daily allowance. Keys are never printed.
"""

from __future__ import annotations

import os
import time
from typing import Any, Callable


def _split_keys(raw: str) -> list[str]:
    return [k.strip() for k in raw.replace(";", ",").split(",") if k.strip()]


def gemini_keys() -> list[str]:
    keys = _split_keys(os.environ.get("GEMINI_API_KEY", ""))
    if not keys:
        raise RuntimeError("GEMINI_API_KEY is not set — add one or more keys (comma-separated) to .env")
    return keys


def _is_quota_error(e: Exception) -> bool:
    msg = str(e)
    return "429" in msg or "RESOURCE_EXHAUSTED" in msg or "quota" in msg.lower()


def _is_auth_error(e: Exception) -> bool:
    msg = str(e)
    return ("API key not valid" in msg or "API_KEY_INVALID" in msg
            or "401" in msg or "403" in msg or "PERMISSION_DENIED" in msg)


class GeminiPool:
    """Round-robin over multiple API keys; rotate on quota/auth failures."""

    _shared: "GeminiPool | None" = None

    def __init__(self, keys: list[str] | None = None):
        from google import genai
        self._genai = genai
        self.keys = keys or gemini_keys()
        self._clients: dict[int, Any] = {}
        self.index = 0
        self.dead: set[int] = set()       # invalid keys (auth failures)
        self.cooldown: dict[int, float] = {}  # key index -> unix time it may retry

    @classmethod
    def shared(cls) -> "GeminiPool":
        if cls._shared is None:
            cls._shared = cls()
        return cls._shared

    def _client(self, i: int):
        if i not in self._clients:
            self._clients[i] = self._genai.Client(api_key=self.keys[i])
        return self._clients[i]

    def _usable(self) -> list[int]:
        now = time.time()
        return [i for i in range(len(self.keys))
                if i not in self.dead and self.cooldown.get(i, 0) <= now]

    def run(self, fn: Callable[[Any], Any], what: str = "gemini call") -> Any:
        """Execute fn(client), rotating to the next key on quota/auth errors.

        Non-quota errors are raised immediately (callers keep their own retry
        loops for transient failures).
        """
        last_err: Exception | None = None
        for _round in range(3):
            usable = self._usable()
            if not usable:
                if len(self.dead) == len(self.keys):
                    raise RuntimeError(
                        f"All Gemini API keys are invalid ({what}) — check .env"
                    ) from last_err
                soonest = min(t for i, t in self.cooldown.items() if i not in self.dead)
                wait = max(soonest - time.time(), 5)
                if wait > 1800:
                    raise RuntimeError(
                        f"All Gemini keys quota-exhausted ({what}); re-run later — "
                        "the pipeline resumes where it stopped."
                    ) from last_err
                print(f"    [gemini] all keys cooling down, sleeping {int(wait)}s")
                time.sleep(wait)
                continue
            for i in usable:
                try:
                    result = fn(self._client(i))
                    self.index = i
                    return result
                except Exception as e:
                    last_err = e
                    if _is_quota_error(e):
                        print(f"    [gemini] key #{i + 1} quota hit — rotating")
                        self.cooldown[i] = time.time() + 120
                    elif _is_auth_error(e):
                        print(f"    [gemini] key #{i + 1} rejected (auth) — marking dead")
                        self.dead.add(i)
                    else:
                        raise
        raise RuntimeError(f"Gemini call failed on every key ({what})") from last_err

    def check(self) -> list[tuple[int, bool, str]]:
        """Validate each key with a cheap call. Returns (index, ok, note) — no secrets."""
        results = []
        for i in range(len(self.keys)):
            try:
                self._client(i).models.list()
                results.append((i + 1, True, "ok"))
            except Exception as e:
                kind = "invalid" if _is_auth_error(e) else ("quota" if _is_quota_error(e) else "error")
                results.append((i + 1, False, f"{kind}: {str(e)[:120]}"))
        return results

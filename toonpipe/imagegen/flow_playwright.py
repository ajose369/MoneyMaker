"""Fully-automated Google Flow image backend (Playwright browser automation).

Drives labs.google/fx/tools/flow in a real Chrome window to generate images
with Nano Banana for free — no API key, no manual paste. This is the plan's
"Option B": it automates a web UI, which is a gray area under Google's ToS
and could put the signed-in Google account at risk. Use a dedicated account
if possible; conservative random delays are applied between generations
(config flow_min_gap_s / flow_max_gap_s).

One-time setup:
  pip install playwright          (Chrome itself is used, no browser download)
  python -m toonpipe flow-login   (sign in with Google in the opened window;
                                   optionally open/create the Flow project to
                                   reuse and switch its output type to Image —
                                   the setting persists per project)

The sign-in lives in a dedicated browser profile (config flow_profile_dir,
default .flow_profile/ — gitignored) and is reused forever after.

Failure diagnostics: every hard failure saves a screenshot + HTML dump under
logs/ so the selectors can be adjusted without re-running blind.
"""

from __future__ import annotations

import atexit
import random
import re
import time
from datetime import datetime
from pathlib import Path

from ..config import LOGS_DIR, ROOT, Config
from .base import ImageBackend

FLOW_URL = "https://labs.google/fx/tools/flow"


class FlowDriver:
    """One shared browser session for the whole process."""

    _instance: "FlowDriver | None" = None

    @classmethod
    def shared(cls, cfg: Config) -> "FlowDriver":
        if cls._instance is None:
            cls._instance = cls(cfg)
            cls._instance.start()
        return cls._instance

    def __init__(self, cfg: Config):
        self.cfg = cfg
        self._pw = None
        self._ctx = None
        self.page = None
        self._project_ready = False

    # ---------- lifecycle ----------

    def start(self, force_headed: bool = False) -> None:
        try:
            from playwright.sync_api import sync_playwright
        except ImportError:
            raise RuntimeError("playwright is not installed — pip install playwright")
        self._pw = sync_playwright().start()
        profile = Path(str(self.cfg.get("flow_profile_dir", ""))) if self.cfg.get("flow_profile_dir") \
            else ROOT / ".flow_profile"
        if not profile.is_absolute():
            profile = ROOT / profile
        profile.mkdir(parents=True, exist_ok=True)
        self._profile = profile
        headless = bool(self.cfg.get("flow_headless", False)) and not force_headed
        args = ["--disable-blink-features=AutomationControlled"]
        # Invisible-but-real alternative to headless: park the window far
        # off-screen. Indistinguishable from a normal browser to Google.
        if not headless and not force_headed and bool(self.cfg.get("flow_offscreen", False)):
            args.append("--window-position=-32000,-32000")
        kwargs = dict(
            user_data_dir=str(profile),
            headless=headless,
            viewport={"width": 1440, "height": 900},
            args=args,
        )
        try:
            # Real Chrome: Google sign-in and anti-bot checks behave far better
            # than with the bundled Chromium build.
            self._ctx = self._pw.chromium.launch_persistent_context(channel="chrome", **kwargs)
        except Exception:
            self._ctx = self._pw.chromium.launch_persistent_context(**kwargs)
        self.page = self._ctx.pages[0] if self._ctx.pages else self._ctx.new_page()
        atexit.register(self.close)

    def close(self) -> None:
        for closer in (lambda: self._ctx.close(), lambda: self._pw.stop()):
            try:
                closer()
            except Exception:
                pass
        self._ctx = None
        self._pw = None
        FlowDriver._instance = None

    # ---------- diagnostics ----------

    def _dump_debug(self, label: str) -> str:
        LOGS_DIR.mkdir(exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        base = LOGS_DIR / f"flow_{label}_{stamp}"
        try:
            self.page.screenshot(path=str(base.with_suffix(".png")), full_page=False)
            base.with_suffix(".html").write_text(self.page.content(), encoding="utf-8")
        except Exception:
            pass
        return str(base)

    # ---------- navigation ----------

    def _login_wall(self) -> bool:
        url = self.page.url
        if "accounts.google.com" in url or "signin" in url.lower():
            return True
        try:
            return self.page.get_by_text(re.compile(r"^sign in$", re.I)).first.is_visible(timeout=1500)
        except Exception:
            return False

    def _click_through_landing(self) -> None:
        """The logged-out tool URL shows a marketing page whose 'Create with
        Google Flow' button leads to sign-in (or the tool when signed in)."""
        page = self.page
        try:
            btn = page.get_by_role("link", name=re.compile("create with google flow", re.I)).first
            if not (btn.count() and btn.is_visible()):
                btn = page.get_by_role("button", name=re.compile("create with google flow", re.I)).first
            if not (btn.count() and btn.is_visible()):
                btn = page.get_by_text(re.compile("create with google flow", re.I)).first
            if btn.count() and btn.is_visible():
                btn.click()
                page.wait_for_timeout(5_000)
        except Exception:
            pass

    def ensure_project(self) -> None:
        if self._project_ready:
            return
        page = self.page
        project_url = str(self.cfg.get("flow_project_url", "") or "").strip()
        url_file = self._profile / "last_project_url.txt"
        if not project_url and url_file.exists():
            # Reuse the project the automation created last time instead of
            # spawning a new Flow project per run.
            project_url = url_file.read_text(encoding="utf-8").strip()
        # Deep links (e.g. the image editor) poison navigation — always land
        # on the project ROOT.
        rooted = re.match(r"(.*?/project/[^/?#]+)", project_url or "")
        if rooted:
            project_url = rooted.group(1)
        page.goto(project_url or FLOW_URL, wait_until="domcontentloaded", timeout=60_000)
        page.wait_for_timeout(4_000)
        self._click_through_landing()
        if self._login_wall():
            self._dump_debug("login_wall")
            raise RuntimeError(
                "Google Flow is not signed in for the automation profile — run:\n"
                "  python -m toonpipe flow-login\n"
                "sign in once, then re-run this stage."
            )
        if "/project/" not in page.url:
            # Tool home — open a new project.
            btn = page.get_by_role("button", name=re.compile(r"new project", re.I)).first
            try:
                btn.click(timeout=10_000)
                page.wait_for_url(re.compile(r"/project/"), timeout=30_000)
            except Exception:
                dump = self._dump_debug("new_project")
                raise RuntimeError(
                    f"Could not open a Flow project automatically (debug: {dump}.png). "
                    "Set flow_project_url in config.yaml to an existing project URL."
                )
            page.wait_for_timeout(3_000)
        rooted = re.match(r"(.*?/project/[^/?#]+)", page.url)
        url_file.write_text(rooted.group(1) if rooted else page.url, encoding="utf-8")
        self._exit_editor()
        self._ensure_image_mode()
        self._project_ready = True

    def _ensure_image_mode(self) -> None:
        """Best-effort switch of the output type to Image (Nano Banana).
        The choice persists per Flow project, so if this ever stops matching
        the UI, switching once by hand in the project is enough."""
        page = self.page
        try:
            current = page.get_by_role("button", name=re.compile(r"^\s*video\s*$", re.I)).first
            if current.count() and current.is_visible():
                current.click()
                page.wait_for_timeout(800)
                opt = page.get_by_role("menuitem", name=re.compile(r"image", re.I)).first
                if not (opt.count() and opt.is_visible()):
                    opt = page.get_by_text(re.compile(r"^\s*image\s*$", re.I)).last
                opt.click(timeout=5_000)
                page.wait_for_timeout(800)
                print("    [flow_auto] switched output type to Image")
        except Exception:
            print("    [flow_auto] could not verify Image output mode — if videos "
                  "generate instead of images, open the Flow project once and set "
                  "the output type to Image (it persists per project).")

    # ---------- generation ----------

    def _exit_editor(self) -> None:
        """Flow lands in the full-screen image editor ('What do you want to
        change?' + Done) — and even RESTORES it server-side when the project
        reopens. Typing there EDITS the open image instead of generating a new
        one. The reliable marker is the toolbar's Done button (the edit box has
        no real placeholder attribute to match on)."""
        page = self.page
        try:
            for _ in range(3):  # editors can nest a version-history view
                # Accessible name is "check Done" — the icon glyph text
                # ("check") prepends, so match on the trailing word only.
                done = page.get_by_role("button", name=re.compile(r"\bdone\s*$", re.I))
                clicked = False
                for i in range(done.count()):
                    el = done.nth(i)
                    if el.is_visible():
                        el.click()
                        page.wait_for_timeout(2_000)
                        print("    [flow_auto] closed the image editor view (Done)")
                        clicked = True
                        break
                if not clicked:
                    return
        except Exception:
            pass

    def _prompt_box(self):
        page = self.page
        for locate in (
            # The composer is the session panel's textarea ("What do you want
            # to create?"). NEVER match aria-label="Editable text" — that is
            # the project TITLE field and typing there renames the project.
            lambda: page.get_by_placeholder(re.compile(r"what do you want to create|describe", re.I)).last,
            lambda: page.locator("textarea").last,
            lambda: page.get_by_role("textbox").last,
        ):
            try:
                box = locate()
                if box.count() and box.is_visible():
                    ph = (box.get_attribute("placeholder") or "").lower()
                    if "change" in ph:  # the editor's edit box — never type here
                        continue
                    return box
            except Exception:
                continue
        dump = self._dump_debug("prompt_box")
        raise RuntimeError(f"Could not find the Flow prompt box (debug: {dump}.png)")

    def _click_send(self, box) -> bool:
        """Click the send arrow closest to the composer. Instrumented run
        2026-07-15 proved every 'Create'/'Generate' button on the page does
        something else entirely (one opens the image EDITOR) — the only safe
        click target is the composer's own send icon."""
        page = self.page
        try:
            bb = box.bounding_box() or {}
            cands = page.locator("button:visible", has_text=re.compile(r"arrow_forward|send", re.I))
            best, best_d = None, float("inf")
            for i in range(cands.count()):
                el = cands.nth(i)
                r = el.bounding_box()
                if not r:
                    continue
                d = (r["x"] - bb.get("x", 0)) ** 2 + (r["y"] - bb.get("y", 0)) ** 2
                if d < best_d:
                    best, best_d = el, d
            if best is not None:
                best.click()
                return True
        except Exception:
            pass
        return False

    def _visible_image_srcs(self) -> set[str]:
        return set(self._ordered_image_srcs())

    def _ordered_image_srcs(self) -> list[str]:
        """Result image srcs in DOM order (dedup, first occurrence wins).
        Batch matching relies on this order lining up with prompt order —
        which direction the gallery inserts new tiles is verified live before
        batching is trusted (flow_batch_reverse flips it if newest-first)."""
        srcs = self.page.evaluate(
            "() => Array.from(document.querySelectorAll('img'))"
            ".filter(i => i.naturalWidth >= 400)"
            ".map(i => i.currentSrc || i.src).filter(Boolean)"
        )
        seen: set[str] = set()
        out: list[str] = []
        for s in srcs:
            if s not in seen:
                seen.add(s)
                out.append(s)
        return out

    def _fetch_image(self, src: str) -> bytes:
        if src.startswith(("blob:", "data:")):
            data = self.page.evaluate(
                """async (url) => {
                    const r = await fetch(url);
                    const b = await r.arrayBuffer();
                    return Array.from(new Uint8Array(b));
                }""",
                src,
            )
            return bytes(data)
        resp = self.page.request.get(src)
        if not resp.ok:
            raise RuntimeError(f"image fetch failed: HTTP {resp.status}")
        return resp.body()

    def generate_image(self, prompt: str, out_path: Path,
                       timeout_s: int = 240) -> Path:
        self.ensure_project()
        page = self.page
        # Close any lingering dialog/overlay (e.g. the asset picker) that
        # would swallow clicks meant for the composer.
        page.keyboard.press("Escape")
        page.wait_for_timeout(400)
        if "/edit/" in page.url:
            # Deterministic escape from the image editor: back to project root.
            rooted = re.match(r"(.*?/project/[^/?#]+)", page.url)
            if rooted:
                page.goto(rooted.group(1), wait_until="domcontentloaded", timeout=60_000)
                page.wait_for_timeout(3_000)
                print("    [flow_auto] left the image editor (navigated to project root)")
        self._exit_editor()
        before = self._visible_image_srcs()

        box = self._prompt_box()
        box.click()
        page.keyboard.press("Control+a")
        page.keyboard.press("Delete")
        box.type(prompt, delay=random.uniform(4, 12))
        page.wait_for_timeout(500)
        # The composer is a chat box: Enter is the ONLY safe submit (the
        # page's Create/Generate buttons do other things — one opens the
        # image editor). A successful send clears the composer; if it still
        # holds text, click the composer's own send arrow.
        box.press("Enter")
        page.wait_for_timeout(1_500)
        try:
            unsent = bool((box.inner_text() or "").strip())
        except Exception:
            unsent = False
        if unsent and self._click_send(box):
            print("    [flow_auto] Enter did not send — clicked the send arrow")

        start = time.time()
        deadline = start + timeout_s
        nudged = False
        while time.time() < deadline:
            page.wait_for_timeout(2_500)
            new = self._visible_image_srcs() - before
            if not new and not nudged and time.time() - start > 30:
                nudged = True
                try:
                    still_unsent = bool((box.inner_text() or "").strip())
                except Exception:
                    still_unsent = False
                # An empty composer means the send DID go through and the
                # render is simply still in flight — nudging would double-send.
                if still_unsent and self._click_send(box):
                    print("    [flow_auto] no generation yet — clicked the send arrow as nudge")
                continue
            if new:
                # Let remaining variants finish rendering, then take the largest.
                page.wait_for_timeout(4_000)
                new = self._visible_image_srcs() - before
                best, best_len = None, -1
                for src in new:
                    try:
                        data = self._fetch_image(src)
                    except Exception:
                        continue
                    if len(data) > best_len:
                        best, best_len = data, len(data)
                if best and best_len > 20_000:
                    out_path.parent.mkdir(parents=True, exist_ok=True)
                    tmp = out_path.with_suffix(".dl")
                    tmp.write_bytes(best)
                    from PIL import Image
                    Image.open(tmp).convert("RGB").save(out_path)
                    tmp.unlink(missing_ok=True)
                    return out_path
        dump = self._dump_debug("generation_timeout")
        raise RuntimeError(
            f"Flow generated nothing within {timeout_s}s for this prompt "
            f"(debug: {dump}.png). Possible causes: daily credits exhausted, "
            "output type set to Video, or a UI change."
        )


    def generate_batch(self, prompts: list[str], out_paths: list[Path],
                       timeout_s: int | None = None) -> list[Path]:
        """Ask the agent for N images in ONE message to save agent-quota (one
        message per batch instead of per image). Matches returned images to
        out_paths in gallery order. Raises FlowBatchMismatch if the count is
        not exactly N so the caller can fall back to per-image generation —
        that keeps a bad batch from silently mis-labelling scenes.

        Order caveat: this assumes the gallery lists new tiles oldest-first
        (prompt order). Set flow_batch_reverse if a live check shows newest-
        first. Verify order before trusting batching for distinct scenes.
        """
        assert len(prompts) == len(out_paths) and prompts
        n = len(prompts)
        if timeout_s is None:
            timeout_s = 120 + 75 * n
        self.ensure_project()
        page = self.page
        page.keyboard.press("Escape")
        page.wait_for_timeout(400)
        if "/edit/" in page.url:
            rooted = re.match(r"(.*?/project/[^/?#]+)", page.url)
            if rooted:
                page.goto(rooted.group(1), wait_until="domcontentloaded", timeout=60_000)
                page.wait_for_timeout(3_000)
        self._exit_editor()
        before = set(self._ordered_image_srcs())

        message = (
            f"Generate exactly {n} images — ONE image for each of the {n} "
            "numbered descriptions below, in the same order. Produce a single "
            "image per description (no variations or alternates). Keep every "
            "image in the SAME art style.\n\n"
            + "\n\n".join(f"{i + 1}. {p}" for i, p in enumerate(prompts))
        )
        box = self._prompt_box()
        box.click()
        page.keyboard.press("Control+a")
        page.keyboard.press("Delete")
        box.type(message, delay=random.uniform(1, 4))
        page.wait_for_timeout(500)
        box.press("Enter")
        page.wait_for_timeout(1_500)
        try:
            unsent = bool((box.inner_text() or "").strip())
        except Exception:
            unsent = False
        if unsent and self._click_send(box):
            print("    [flow_auto] batch: Enter did not send — clicked the send arrow")

        start = time.time()
        deadline = start + timeout_s
        stable_since = None
        last_count = -1
        nudged = False
        while time.time() < deadline:
            page.wait_for_timeout(3_000)
            new = [s for s in self._ordered_image_srcs() if s not in before]
            if len(new) != last_count:
                last_count = len(new)
                stable_since = time.time()
            if not new and not nudged and time.time() - start > 30:
                nudged = True
                try:
                    still = bool((box.inner_text() or "").strip())
                except Exception:
                    still = False
                if still and self._click_send(box):
                    print("    [flow_auto] batch: nudged the send arrow")
                continue
            # Settle: reached the target count and stable for a few polls.
            if len(new) >= n and stable_since and time.time() - stable_since > 10:
                break

        new = [s for s in self._ordered_image_srcs() if s not in before]
        if bool(self.cfg.get("flow_batch_reverse", False)):
            new = list(reversed(new))
        if len(new) != n:
            dump = self._dump_debug("batch_mismatch")
            raise FlowBatchMismatch(
                f"batch expected {n} images, got {len(new)} (debug: {dump}.png)"
            )
        from PIL import Image
        saved: list[Path] = []
        for src, out_path in zip(new, out_paths):
            data = self._fetch_image(src)
            if len(data) < 20_000:
                raise FlowBatchMismatch(f"batch image for {out_path.name} too small")
            out_path.parent.mkdir(parents=True, exist_ok=True)
            tmp = out_path.with_suffix(".dl")
            tmp.write_bytes(data)
            Image.open(tmp).convert("RGB").save(out_path)
            tmp.unlink(missing_ok=True)
            saved.append(out_path)
        return saved


class FlowBatchMismatch(RuntimeError):
    """Raised when a batch returns a different image count than requested."""


class FlowAutoBackend(ImageBackend):
    def generate(self, prompt: str, ref_images: list[Path], out_path: Path) -> Path:
        drv = FlowDriver.shared(self.cfg)
        result = drv.generate_image(prompt, out_path)
        self._pause()
        return result

    def generate_batch(self, prompts: list[str], out_paths: list[Path]) -> list[Path]:
        drv = FlowDriver.shared(self.cfg)
        result = drv.generate_batch(prompts, out_paths)
        self._pause()
        return result

    def _pause(self) -> None:
        lo = float(self.cfg.get("flow_min_gap_s", 12))
        hi = float(self.cfg.get("flow_max_gap_s", 30))
        time.sleep(random.uniform(lo, max(lo, hi)))


def flow_login(cfg: Config) -> None:
    """Interactive one-time sign-in for the automation profile."""
    drv = FlowDriver(cfg)
    drv.start(force_headed=True)
    drv.page.goto(FLOW_URL, wait_until="domcontentloaded", timeout=60_000)
    drv.page.wait_for_timeout(3_000)
    drv._click_through_landing()
    print(
        "\nA Chrome window is open on Google Flow.\n"
        "  1. Sign in with the Google account to use for image generation\n"
        "     (a dedicated account is recommended).\n"
        "  2. Optional but recommended: open or create the Flow project to reuse\n"
        "     and switch its output type to Image — then copy its URL into\n"
        "     config.yaml as flow_project_url.\n"
    )
    input("Press Enter here when you are done... ")
    if "/project/" in drv.page.url:
        print(f"Current project URL (for config.yaml flow_project_url):\n  {drv.page.url}")
    drv.close()
    print("Sign-in saved. The flow_auto backend will reuse it from now on.")

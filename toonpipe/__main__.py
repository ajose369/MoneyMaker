"""toonpipe CLI.

  python -m toonpipe autopilot [--topic "..."] [--slug existing-project]
  python -m toonpipe new "topic here"
  python -m toonpipe run <stage> --slug <slug>       (stage: story|characters|environments|
                                                      scene_images|audio|video|assemble|
                                                      metadata|publish|all)
  python -m toonpipe status
  python -m toonpipe check                            (validate keys/tools, no secrets printed)
  python -m toonpipe auth                             (one-time YouTube OAuth)
  python -m toonpipe flow-login                       (one-time Google sign-in for flow_auto images)
"""

from __future__ import annotations

import argparse
import sys

# Force UTF-8 console output: titles/metadata contain emoji (🤯) and content can
# be Malayalam/Tamil/Hindi, which crash on Windows' default cp1252 stdout —
# especially when piped (non-tty falls back to the locale encoding).
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

from .config import load_config
from .manifest import Manifest, next_slug
from .pipeline import STAGES, autopilot, run_stage, status
from . import story as story_mod


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="toonpipe", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_auto = sub.add_parser("autopilot", help="full run: topic -> published video, no human input")
    p_auto.add_argument("--topic", help="story premise (omit to auto-generate a fresh one)")
    p_auto.add_argument("--slug", help="resume an existing project instead of creating one")

    p_new = sub.add_parser("new", help="create a project from a topic (no stages run)")
    p_new.add_argument("topic")

    p_run = sub.add_parser("run", help="run one stage (or 'all') for a project")
    p_run.add_argument("stage", choices=STAGES + ["all"])
    p_run.add_argument("--slug", required=True)

    sub.add_parser("status", help="list projects and their progress")
    sub.add_parser("check", help="validate keys/tools/models without running anything")
    sub.add_parser("auth", help="one-time interactive YouTube OAuth")
    sub.add_parser("flow-login", help="one-time Google sign-in for the flow_auto image backend")

    args = parser.parse_args(argv)

    if args.cmd == "autopilot":
        autopilot(topic=args.topic, slug=args.slug)

    elif args.cmd == "new":
        m = Manifest.create(next_slug(args.topic))
        m.topic = args.topic
        story_mod.remember_topic(args.topic)
        m.save()
        print(f"Created project '{m.slug}'. Run: python -m toonpipe autopilot --slug {m.slug}")

    elif args.cmd == "run":
        m = Manifest.load(args.slug)
        cfg = load_config(m.dir)
        stages = STAGES if args.stage == "all" else [args.stage]
        for s in stages:
            if args.stage == "all" and m.is_done(s):
                print(f"[skip] {s} (done)")
                continue
            run_stage(s, m, cfg)

    elif args.cmd == "status":
        status()

    elif args.cmd == "check":
        from .pipeline import check
        return 0 if check() else 1

    elif args.cmd == "auth":
        from .publish import get_credentials
        get_credentials(load_config(), interactive=True)
        print("YouTube auth complete — uploads will now run unattended.")

    elif args.cmd == "flow-login":
        from .imagegen.flow_playwright import flow_login
        flow_login(load_config())

    return 0


if __name__ == "__main__":
    sys.exit(main())

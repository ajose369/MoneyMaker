"""toonpipe CLI.

  python -m toonpipe autopilot [--topic "..."] [--slug existing-project]
  python -m toonpipe new "topic here"
  python -m toonpipe run <stage> --slug <slug>       (stage: story|characters|environments|
                                                      scene_images|audio|video|assemble|
                                                      metadata|publish|all)
  python -m toonpipe status
  python -m toonpipe check                            (validate keys/tools, no secrets printed)
  python -m toonpipe auth                             (one-time YouTube OAuth)
"""

from __future__ import annotations

import argparse
import sys

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

    return 0


if __name__ == "__main__":
    sys.exit(main())

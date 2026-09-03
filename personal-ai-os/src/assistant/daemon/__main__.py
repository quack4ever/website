"""Entry point so launchd can run: python3 -m assistant.daemon --serve"""
from __future__ import annotations

import argparse
import sys


def main() -> int:
    parser = argparse.ArgumentParser(
        prog="assistant.daemon",
        description="The Personal AI OS background service.")
    parser.add_argument("--serve", action="store_true",
                        help="Run the daemon in the foreground (launchd uses this).")
    parser.add_argument("--no-scheduler", action="store_true",
                        help="Serve requests but do not run scheduled jobs.")
    parser.add_argument("--tick", action="store_true",
                        help="Run any due scheduled jobs once, then exit.")
    args = parser.parse_args()

    from ..errors import AssistantError

    try:
        if args.tick:
            from .scheduler import Scheduler
            for result in Scheduler().tick_once():
                print(result)
            return 0
        if args.serve:
            from .server import serve
            serve(with_scheduler=not args.no_scheduler)
            return 0
    except AssistantError as exc:
        print(exc.human(), file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 0

    parser.print_help()
    return 2


if __name__ == "__main__":
    raise SystemExit(main())

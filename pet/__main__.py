"""Entry point: /usr/bin/python3 -m pet [--url http://localhost:8420]"""

from __future__ import annotations

import argparse
import logging
import signal
import sys

from . import gtk3  # noqa: F401  (pins GI versions; must precede gi.repository)
from gi.repository import Gdk, GLib, GLibUnix, Gtk

from .config import SIZES, Config
from .window import DEFAULT_FPS, PetWindow

DEFAULT_URL = "http://localhost:8420"
APP_NAME = "poketokenbar-pet"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="pet", description="PokeTokenBar floating desktop pet")
    parser.add_argument("--url", default=DEFAULT_URL, help=f"backend base URL (default {DEFAULT_URL})")
    parser.add_argument("--interval", type=float, default=5.0, help="seconds between state polls")
    parser.add_argument("--size", type=int, choices=SIZES, help="override the saved size")
    parser.add_argument("--no-shape", action="store_true", help="disable click-through of transparent areas")
    parser.add_argument(
        "--fps", type=int, default=DEFAULT_FPS, help=f"animation frame rate (default {DEFAULT_FPS})"
    )
    parser.add_argument("--verbose", "-v", action="store_true")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
    )

    # Otherwise WM_CLASS reads "__main__.py", which is what window rules key off.
    GLib.set_prgname(APP_NAME)
    Gdk.set_program_class("PokeTokenBar-Pet")

    config = Config()
    if args.size:
        config.set(size=args.size)

    window = PetWindow(args.url, args.interval, config, shape=not args.no_shape, fps=args.fps)
    window.run()

    # Ctrl-C has to be routed through the GLib loop or it is simply swallowed.
    for sig in (signal.SIGINT, signal.SIGTERM):
        GLibUnix.signal_add(GLib.PRIORITY_DEFAULT, sig, lambda *_: (window.quit(), False)[1])

    logging.getLogger("pet").info("pet running against %s -- right-click it for the menu", args.url)
    Gtk.main()
    return 0


if __name__ == "__main__":
    sys.exit(main())

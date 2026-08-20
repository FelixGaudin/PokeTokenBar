"""Pins every GI namespace to its GTK3-era version.

Import this before anything else touches gi.repository. Without it, importing `Gdk`
ahead of `Gtk` resolves to GDK 4.0 (the newest installed) and every later GTK3 import
fails with a version clash — an easy trap, since alphabetical import order does it.
"""

from __future__ import annotations

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("GLibUnix", "2.0")
gi.require_version("Gdk", "3.0")
gi.require_version("GdkPixbuf", "2.0")
gi.require_version("Pango", "1.0")
gi.require_version("PangoCairo", "1.0")

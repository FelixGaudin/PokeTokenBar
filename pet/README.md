# PokeTokenBar floating pet (Linux)

A desktop pet that shows the Pokemon you are currently raising, floating above your
other windows. Port of the macOS app's floating pet; design notes in
`../docs/floating-pet-linux.md`.

```bash
/usr/bin/python3 -m pet                       # from the repo root
/usr/bin/python3 -m pet --url http://localhost:8420 --size 128
```

**Use `/usr/bin/python3`, not a venv.** The pet needs the system PyGObject
(`python-gobject`) and pycairo; a venv without `--system-site-packages` cannot see them.
There is nothing to pip install.

| Input               | Action                                        |
| ------------------- | --------------------------------------------- |
| Hover               | Callout: species, progress, today, burn, limits |
| (always visible)    | EXP bar under the sprite: progress to the next form, or incubation |
| Left click          | Opens the web UI                              |
| Left drag           | Moves the pet (position is remembered)        |
| Right click         | Menu: size, always-on-top, animation, EXP bar, autostart, quit |

Preferences live in `~/.config/poketokenbar/pet.json`; autostart is a checkbox in the
menu, which writes `~/.config/autostart/poketokenbar-pet.desktop`.

The pet is a read-only client of `/api/state` and `/api/sprite/...`. It stores no game
state, so it can be killed and restarted freely, and it degrades quietly (dimmed, backing
off) when the backend is not running.

Costs roughly 1.6-1.9% of one core while animating with the EXP bar (1.4% without it) and
0.6% while the egg is incubating; drop `--fps` if you want less. It holds a single frame in the `sleep` state, so an idle machine
stays idle.

## Options

| Flag | Purpose |
| --- | --- |
| `--url` | Backend base URL (default `http://localhost:8420`) |
| `--interval` | Seconds between state polls (default 5) |
| `--size` | 48, 96, 128 or 192; overrides the saved size |
| `--fps` | Animation frame rate (default 10, matching the sprites' own 100ms frames) |
| `--no-shape` | Disable click-through of transparent areas |
| `--verbose` | Debug logging |

## Requirements

X11 with a compositor. Verified on Cinnamon/muffin. Wayland is not supported: setting
always-on-top and absolute position needs `wlr-layer-shell` (or a GNOME extension), which
is why placement is kept in one place in `window.py` for a later backend.

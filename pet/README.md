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

| Input                      | Action                                                                                |
| -------------------------- | ------------------------------------------------------------------------------------- |
| Hover                      | Callout: species, growth, today (and how long it has sulked), burn, limits, prayers   |
| (always visible)           | EXP bar under the sprite: progress to the next form, or incubation                    |
| Left click on the egg      | Prays over it: a 🙏 floats up and the egg wobbles (cosmetic, never affects the hatch) |
| Left click on a Pokemon    | Opens the web UI                                                                      |
| Left click (either)        | Also answers waiting agents and dismisses a "done" bubble; working agents carry on    |
| Wiggle the pointer over it | Petting: a heart pops up (three quick back-and-forth strokes, no button held)         |
| Left drag                  | Moves the pet (position is remembered; a drag during an agent run sets the new home)  |
| Right click                | Menu, see below                                                                       |

Right-click menu: Open PokeTokenBar (always opens, even over an egg), Refresh now, Size,
Always on top, Animated sprite, Progress bar, **React to agent activity** (coding agents,
below; on by default), **Show back view** (always show the companion from behind; the
same setting as in the web UI), Start with session, Quit.

Above the pet, one thing at a time: a speech bubble (limit alert, then an agent's result,
then an agent's question) wins over the petting heart, which wins over the hover callout.
Hatches, evolutions, graduations and the like pop the callout for 6 seconds on their own.

The Pokemon turns its back after 3 hours without token usage (it is sulking) and faces you
again as soon as you use tokens. Sulking is not sleep: it keeps moving.

Preferences live in `~/.config/poketokenbar/pet.json`; autostart is a checkbox in the
menu, which writes `~/.config/autostart/poketokenbar-pet.desktop`.

The pet reads `/api/state` and `/api/sprite/...`, and only writes prayers and the back-view
setting. It stores no game state, so it can be killed and restarted freely, and it degrades
quietly (dimmed, backing off) when the backend is not running. When the network comes back
after an outage it asks the backend for an immediate re-scan (at most once every 5 s). A
sprite download that fails shows the static image and retries the animation after 2, 4, 8,
16, then every 30 s.

Costs roughly 1.6-1.9% of one core while animating with the EXP bar (1.4% without it) and
0.6% while the egg is incubating; drop `--fps` if you want less. It holds a single frame in
the `sleep` state, so an idle machine stays idle. The agent bob, the run, the prayer emoji
and the heart run their own timers only while they are on screen.

## Coding agents

With **React to agent activity** on, a coding agent can drive the pet: it bobs while the
agent works, says so when the agent needs you, and runs to your pointer with a "done"
bubble when the agent finishes, then walks back home. Agents report to the backend with
`POST /api/agent`; the pet picks events up on its next poll (up to `--interval` later).

| `state`   | On the pet                                                                      |
| --------- | ------------------------------------------------------------------------------- |
| `working` | Bobs. Falls back to idle after 10 minutes without news                          |
| `waiting` | "Your agent needs you" (or `<label> · waiting`) until the next event or a click |
| `done`    | "Done!" (or `<label> · done`) for 6 seconds, running to the pointer             |
| `idle`    | Clears that agent                                                               |

`label` names the agent (several labels are tracked separately, up to 8); `message` is
shown in waiting and done bubbles. Both are cleaned and capped (24 and 80 characters) by
the backend. The bob and the run are skipped in power-saver mode, when the desktop has
animations turned off, and when "Animated sprite" is off; the bubbles still show.

**Claude Code**: save this as `~/.claude/poketokenbar.sh`. It labels each session with its
project folder name, so two sessions in one folder share a label. `curl -m 2` keeps a
stopped backend from stalling Claude Code, and the hook never fails a turn.

```sh
#!/bin/sh
# PokeTokenBar agent hook: sh ~/.claude/poketokenbar.sh <working|waiting|done|idle> [message]
# Labels the agent with the project folder name. Silent when the backend is not running.
url="${PTB_URL:-http://localhost:8420}/api/agent"
state=$1
message=$2
set -- -fsS -m 2 -o /dev/null -X POST -G "$url" \
  --data-urlencode "state=$state" \
  --data-urlencode "label=${CLAUDE_PROJECT_DIR##*/}"
[ -n "$message" ] && set -- "$@" --data-urlencode "message=$message"
curl "$@" >/dev/null 2>&1 || true
exit 0
```

Then add the hooks to `~/.claude/settings.json`. The `Notification` matcher skips Claude
Code's idle reminder, which would otherwise leave a "waiting" bubble after every turn.
`Stop` does not fire when you interrupt a turn (Esc), so the pet keeps working until the
10-minute timeout or an `idle` event.

```json
{
  "hooks": {
    "UserPromptSubmit": [{ "hooks": [{ "type": "command", "command": "sh ~/.claude/poketokenbar.sh working || true" }] }],
    "PostToolUse": [{ "matcher": "*", "hooks": [{ "type": "command", "command": "sh ~/.claude/poketokenbar.sh working || true" }] }],
    "PreToolUse": [{ "matcher": "AskUserQuestion", "hooks": [{ "type": "command", "command": "sh ~/.claude/poketokenbar.sh waiting || true" }] }],
    "Notification": [{ "matcher": "permission_prompt|elicitation_dialog", "hooks": [{ "type": "command", "command": "sh ~/.claude/poketokenbar.sh waiting || true" }] }],
    "Stop": [{ "hooks": [{ "type": "command", "command": "sh ~/.claude/poketokenbar.sh done || true" }] }]
  }
}
```

**Any other agent or script**: one line, with query parameters (or a JSON body with the
same keys):

```sh
curl -fsS -m 2 -X POST -G http://localhost:8420/api/agent --data-urlencode state=done \
  --data-urlencode label=my-app --data-urlencode 'message=Build passed' >/dev/null 2>&1 || true
```

Anything that can reach the backend's port can send these events, as with its other POST
endpoints; the cleaning and the caps are the protection.

## Options

| Flag         | Purpose                                                                   |
| ------------ | ------------------------------------------------------------------------- |
| `--url`      | Backend base URL (default `http://localhost:8420`)                        |
| `--interval` | Seconds between state polls (default 5)                                   |
| `--size`     | 48, 96, 128, 192, 256, 320 or 384; overrides the saved size               |
| `--fps`      | Animation frame rate (default 10, matching the sprites' own 100ms frames) |
| `--no-shape` | Disable click-through of transparent areas                                |
| `--verbose`  | Debug logging                                                             |

## Tests

The pure modules (`agent.py`, `prayer.py`, `petting.py`, `fmt.py`) import no `gi`, so their
tests run under the repo venv: `.venv/bin/python -m pytest pet/tests -q`.

## Requirements

X11 with a compositor. Verified on Cinnamon/muffin. Wayland is not supported: setting
always-on-top and absolute position needs `wlr-layer-shell` (or a GNOME extension), which
is why placement is kept in one place in `window.py` for a later backend.

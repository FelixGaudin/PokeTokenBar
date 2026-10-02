# PokeTokenBar Web

A browser port of [PokeTokenBar](https://github.com/chattymin/PokeTokenBar) by chattymin,
which is macOS-only. Same idea: your AI coding token usage incubates an egg, hatches a
Pokémon, evolves it through its real evolution tree, and graduates it into a permanent
Pokédex — while giving you precise numbers on what you actually spent.

Runs in Docker on any platform. Nothing leaves your machine except PokéAPI lookups,
sprite downloads, and (optionally) the official limits call.

```
┌─ Home ────────────────────────────────────────────────┐
│  [sprite]  Tentacruel   RARE   ✨ Shiny   Quirky      │
│            Final form                                  │
│            ▓▓▓▓▓▓▓▓▓░░░░░░  1.01B to graduation       │
│            Today's work is piling up.                  │
│                                                        │
│  Today's tokens                                        │
│  265.8M   265,800,000                        $49.25   │
│  This week 1.33B    This month 5.32B    Burn 87K/min  │
│                                                        │
│  Limits (official)                              Max 20x│
│  5-hour session                                   62%  │
│  Weekly                                           31%  │
└────────────────────────────────────────────────────────┘
```

## Quick start

```bash
cp .env.example .env      # set PTB_TZ, and PTB_UID/PTB_GID if your uid is not 1000
docker compose up -d
open http://localhost:8420
```

That's it. Your save, the PokéAPI cache and the sprite cache land in `./data/`.

**The uid matters.** Claude Code's logs are mode `600` inside a `700` directory, so the
container has to run as the user that owns them. Check with `id -u` / `id -g` and set
`PTB_UID` / `PTB_GID` in `.env` if they aren't 1000.

**The Claude mount is optional.** Without it the app still starts and serves normally — it
just has nothing to count, and says so in a banner rather than showing a silent zero. To run
with no access to your Claude config at all, point `PTB_CLAUDE_DIR` at an empty directory:

```bash
mkdir -p /tmp/empty && PTB_CLAUDE_DIR=/tmp/empty docker compose up -d
```

You get the UI, the shop and the Pokédex; token tracking stays idle until a real log
directory is mounted. The distinction is reported at `/api/state` under `meta`:
`log_roots_present`, `log_files_found` and `source_warning`.

Confirm it found your logs:

```bash
curl -s localhost:8420/api/health | python3 -m json.tool
```

## What it tracks

Claude Code, read from `~/.claude/projects/**/*.jsonl`. The parser keeps
`type: "assistant"` lines and their four token counts, then prices them per model.

Session resume and sidechains write the same turn into more than one file, so entries are
de-duplicated on `(message.id, requestId)`. Streaming also re-logs a message as it grows —
`cache_read` and `input` stay fixed while `output` climbs — so the largest total wins.
Keeping the first occurrence would badly under-count cost.

| Window | How it's derived |
| --- | --- |
| Today | Local calendar day in `PTB_TZ` |
| This week | Monday-based week to date |
| This month | Calendar month to date |
| 5-hour block | Rolling window, used for the burn rate |
| Burn rate | Block tokens ÷ minutes since the block's first turn |

Forked sessions replay earlier turns under a later timestamp. When the same turn shows up
more than once, the earliest time is kept, so a fork never drags old usage into today.

**Costs.** Claude Code appends `type: "cost-state"` ledgers to each session file. When one
is present, its per-model `costUSD` is what you see: it is spread over that model's turns in
proportion to tokens, and the last ledger in a file wins. Turns with no ledger are priced
from the table below (USD per million tokens; cache writes at the 5-minute rate). Current
Claude models serve their 1M context at standard rates, so there's no long-context tier.

| Model                                     | Input | Output | Cache write | Cache read |
| ----------------------------------------- | ----: | -----: | ----------: | ---------: |
| `claude-opus-5-5`                         |    $4 |    $20 |       $5.00 |      $0.20 |
| `claude-opus-5`, `claude-opus-4-8/4-7/4-6` |    $5 |    $25 |       $6.25 |      $0.50 |
| `claude-sonnet-5`                         |    $2 |    $10 |       $2.50 |      $0.20 |
| `claude-sonnet-4-6`, `-4-5`, `-4`         |    $3 |    $15 |       $3.75 |      $0.30 |
| `claude-opus-4` (2025-05-14)              |   $15 |    $75 |      $18.75 |      $1.50 |
| `claude-haiku-4-5`                        |    $1 |     $5 |       $1.25 |      $0.10 |
| `claude-fable-5`                          |   $10 |    $50 |      $12.50 |      $1.00 |
| `claude-fable-5-1`                        |   $10 |    $50 |      $12.50 |      $0.25 |

There is no family fallback: an unknown model id has no price rather than borrowing a
guess. Its cost reads **Unavailable** (logged once per model so a missing row gets
noticed), and a total that is only partly priced shows the known part.

The Home tab shows a day-by-day bar chart of the current month, and a **usage recap** for
any calendar week, month or year. Logs only cover the current month, so each refresh copies
daily totals into a ledger kept in the save (this year and last year), which also records
the first day it covers — "no usage" and "not recorded yet" are told apart.

### Adding another provider

The upstream app also reads Codex, Gemini CLI, Cursor, OpenCode and others. Only Claude
Code is implemented here (it's what this machine had). To add one, write a reader in
`backend/app/readers/` that returns `Entry` objects and register it in
`AppRuntime.refresh`'s provider map — the companion ledger is already per-provider, so
nothing else needs to change.

## Official limits

Optional, on by default. The container reads `~/.claude/.credentials.json` (mounted
read-only), takes the OAuth access token, and calls
`https://api.anthropic.com/api/oauth/usage` for your real 5-hour and weekly percentages
plus your plan label.

Set `PTB_LIMITS_ENABLED=0` to skip the credential read and the outbound call entirely. The
rolling 5-hour block is computed from local logs either way, so you keep a usable
burn-rate view.

Failures degrade quietly and never affect token accounting. A rejected or expired token
surfaces as "run `/login`" in the UI, and a 429 backs off using the server's `Retry-After`.
The credentials file is re-read on every fetch, so a `/login` to another account shows up on
the next poll; if the file disappears, the last good token is used until it expires.

Each bar carries a **pace marker**: where an even burn across the window would sit now. Its
colour follows how far ahead or behind that pace you are (six tiers, from "Well under pace"
to "Very fast"); hover a row for the details. Settings has a **Used / Remaining** switch.
The account the limits belong to is read from `/api/oauth/profile` and shown next to the
plan.

### Several Claude accounts

Claude Code run with `CLAUDE_CONFIG_DIR=/path` keeps a whole login in that folder. Mount
each extra folder read-only and it gets its own tab of limits, its own Rare Candy grants,
and its `projects/` joins the token scan:

```yaml
    volumes:
      - ${HOME}/.claude-work:/host/accounts/.claude-work:ro
    environment:
      PTB_CLAUDE_ACCOUNTS_ROOT: /host/accounts   # .claude-* / .claude_* folders with a login
      # or list them explicitly, optionally labelled: work=/host/accounts/.claude-work
      # PTB_CLAUDE_ACCOUNT_DIRS: work=/host/accounts/.claude-work
```

An account's id is a hash of its mount path (and label), so keep mount points stable. A
folder holding the same login as another tab is listed once. An extra account only earns
candy after it has been seen below 100%, so mounting one that is already full pays nothing.
The companion looks worn out when any account is at its limit.

### Why not an API key?

The 5-hour and weekly windows are a **subscription** measurement, and no API key can read
them:

| Credential | Gets you |
| --- | --- |
| OAuth token (what this uses) | 5-hour + weekly utilization, plan label |
| Standard key (`sk-ant-api...`) | Inference only — there is no "my usage" endpoint |
| Admin key (`sk-ant-admin01-...`) | Org usage and cost reports, incl. a Claude Code per-user endpoint that does cover subscription plans — but daily buckets with a ~1h lag, and no limit percentages |

The Admin API is worth knowing about if you run Claude Code on **several machines**, since
local logs only ever see the machine they are on. It is too coarse to drive the game (the
companion needs per-minute deltas), but it would work as a reconciliation source. Not
implemented here.

## The game

Progression is driven purely by tokens. The balance constants are ported verbatim from the
macOS app so a save feels the same.

| Step | Cost |
| --- | --- |
| Egg hatches | 5M tokens (overflow carries into the hatchling) |
| Graduation total | 750M common · 1.875B uncommon · 3B rare · 6B legendary |

Within a line of *k* forms, form *i* costs `T·i / (k(k+1)/2)`. That sums to exactly `T`, so
the same rarity costs the same whether it has one stage or three — later stages just cost
more than earlier ones.

- **Rarity** comes from the species' real `capture_rate`, which is also the hatch weight.
  Caterpie (255) is 85× likelier than Mewtwo (3); the legendary group lands near 0.8%. A
  base whose finals you've already collected is weighted at half, nudging toward new
  species without closing off shiny hunting.
- **Shiny** is 1/64 at hatch, 1/48 with the Shiny Charm. Decided once and kept through
  every evolution.
- **Nature** is one of 25, rolled at hatch, cosmetic, re-rollable with a Mint.
- **Ditto** — a common multi-stage hatch has a 1/128 chance of being a disguised Ditto. It
  looks ordinary (shininess included) until its first evolution threshold, where it drops
  the disguise instead of evolving.
- **Branches** (Eevee, Tyrogue…) pick one route at hatch and stick to it, preferring a
  branch that still leads to a final form you don't have.
- **Rare Candy** is granted free when you fill a limit window — 1 for a session window, 5
  for a weekly one. Edge-triggered, so it pays once per crossing and re-arms when the
  window drops below 100% or its reset time changes. The Bag feeds several at once, with a
  preview of what that does (evolve, carry-over, graduate, leftover XP).
- **Repeat hatches** of a line you have already graduated grow **2×** as fast.
- **Individuals.** Every Pokémon gets persistent IVs, gender, ability, level (5 at hatch, 100
  at graduation) and its last four level-up moves. Click a Pokédex cell for its detail page:
  actual stats, species data and the complete move list, from PokéAPI.
- **Unown** hatches as one of its 28 letters; uncollected letters are twice as likely.
- **Difficulty.** Settings has two multipliers, 10%–200%: growth (egg and stage
  thresholds) and shop prices. Changing growth keeps the fraction you have earned and never
  evolves or hatches on the spot. It is a preference, not part of the save.

### Shop

Every token you have ever spent is currency. Buying only draws down the wallet — it never
touches your growth meter or your usage stats.

| Item | Price | Effect |
| --- | --- | --- |
| Rare Candy | 500M | +100M growth. Deliberately smaller than the cheapest evolution, so one candy can never chain two stages. |
| Mint | 100M | Re-rolls nature. |
| Shiny Charm | 3B | Held. Shiny odds 1/64 → 1/48 on all future hatches. |
| Egg | 1B | Releases your current Pokémon, starts a fresh egg. |
| Fine Egg | 2.5B | Guarantees Uncommon or better. |
| Prime Egg | 4B | Guarantees Rare or better (~10% Legendary). |

Eggs can only be bought while a Pokémon is active — an egg always means sending the current
one off. A released Pokémon stays in the Pokédex (marked Released) but does not count as
graduated: it keeps full hatch odds and earns no repeat boost. Sending off a shiny or a
legendary asks twice. There's no legendary-only egg: that tier is decided by the
`is_legendary` flag, which can't be expressed as a capture-rate floor.

## Backups

Every 12 hours, once there is progress, the save is copied to
`data/.snapshots/state.json/` (the newest 10 are kept). Settings lists them and can take one
now or restore one; restoring first snapshots the current state. If `state.json` is ever
unreadable at startup, it is moved to `state.json.corrupt` and the newest snapshot is
restored. Migrating a save from before individual values also writes a one-time
`state.pre-profiles-v1.json`.

## Configuration

Every value has a working default.

| Variable | Default | Purpose |
| --- | --- | --- |
| `PTB_PORT` | `8420` | Host port |
| `PTB_UID` / `PTB_GID` | `1000` | Must own `~/.claude` |
| `PTB_TZ` | `UTC` | Decides which tokens count as "today" |
| `PTB_POLL_INTERVAL` | `60` | Seconds between log re-scans |
| `PTB_LIMITS_ENABLED` | `1` | Official limits on/off |
| `PTB_LIMITS_INTERVAL` | `300` | Seconds between limit fetches |
| `PTB_CLAUDE_DIR` | `~/.claude` | If your Claude config lives elsewhere |
| `PTB_CLAUDE_ROOTS` | — | Comma-separated log roots (set by compose) |
| `PTB_CLAUDE_ACCOUNTS_ROOT` | — | Folder searched for extra `.claude-*` account folders |
| `PTB_CLAUDE_ACCOUNT_DIRS` | — | Extra account folders, `label=path`, comma-separated |
| `PTB_DEFAULT_CLAUDE_JSON` | — | `~/.claude.json`, to name the default account offline |
| `PTB_DATA_DIR` | `/data` | Save, preferences, snapshots + caches |

## How it fits together

The refresh loop runs **server-side**, so your Pokémon keeps incubating and evolving
whether or not the page is open. The browser just polls `/api/state` every 5 seconds.

```
~/.claude/projects/**/*.jsonl ──► reader ──► usage windows ──┐
                                                             ├──► companion ──► ./data/state.json
api.anthropic.com/api/oauth/usage ──► limits ────────────────┘         │
                                                                       ▼
PokéAPI (species, chains) ─────────────────────────────► ./data/cache/
PokeAPI/sprites ───────────────────────────────────────► ./data/sprites/
```

Usage is credited as a **delta**: the loop compares each provider's cumulative daily total
against the last observation and banks only the growth. Consequences worth knowing:

- Usage logged before you first ran this app is never retroactively credited. The first
  refresh sets a baseline, so you start from a cold egg rather than an instant Pokédex.
- A new day credits that whole day (totals from different days aren't comparable).
- If a provider's total drops (a pruned log), only that provider's mark is rebased.
- An empty or stale refresh can't move the ledger, so a failed scan never awards tokens.

Sprites are proxied and cached, so the browser only ever talks to this server — the page
works offline once a sprite has been seen. Animated Gen-V art only exists up to national
dex #649; beyond that it falls back to the static PNG automatically.

Parsed log entries are cached per file, keyed on mtime and size. A cold scan of 82 session
files takes ~0.8s; warm scans are ~25ms.

## API

| Endpoint | Purpose |
| --- | --- |
| `GET /api/state` | Everything the UI renders |
| `GET /api/health` | Log roots found, credential mounted, last refresh |
| `POST /api/refresh` | Force a re-scan now |
| `POST /api/shop/item` · `/api/shop/egg` | Purchases |
| `POST /api/bag/candy` (`{count}`) · `/api/bag/mint` | Use an item |
| `GET /api/pokemon/{id}?form=` | Detail page: stats, moves, individuals |
| `GET /api/recap?scope=week\|month\|year&offset=` | Usage recap |
| `POST /api/settings/difficulty` · `/limit-display` | Preferences |
| `GET` / `POST /api/snapshots` · `POST /api/snapshots/{id}/restore` | Backups |
| `GET` / `POST /api/save` | Export / import your save |
| `GET /api/sprite/{id}?animated=&shiny=&form=` | Cached sprite proxy |
| `GET /docs` | Interactive OpenAPI schema |

Importing a save re-seeds the usage baseline from *this* machine's logs, so today's
existing tokens aren't credited twice.

## Development

```bash
# backend
python3 -m venv .venv && .venv/bin/pip install -e 'backend[dev]'
cd backend && PTB_DATA_DIR=./devdata PTB_STATIC_DIR= PTB_TZ=Europe/Brussels \
  ../.venv/bin/python -m uvicorn app.main:app --reload --port 8000

# frontend (proxies /api to :8000)
cd frontend && npm install && npm run dev
```

```bash
cd backend && ../.venv/bin/python -m pytest -q
cd frontend && npm run typecheck
```

The test suite covers the parts worth pinning down: log parsing and de-duplication, window
boundaries (month start, midnight), the delta ledger's edge cases, threshold arithmetic,
the full hatch → evolve → graduate path against a stubbed PokéAPI, shop and wallet rules,
edge-triggered candy grants, and pricing.

## Attribution

A port of [PokeTokenBar](https://github.com/chattymin/PokeTokenBar) by chattymin (MIT).

Unofficial, non-commercial fan project. No affiliation with Nintendo, Game Freak,
Creatures Inc. or The Pokémon Company. Pokémon data and sprites are fetched from
[PokéAPI](https://pokeapi.co) at runtime and cached locally — none are bundled here.

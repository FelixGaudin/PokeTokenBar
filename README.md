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

Costs use the current published rates (USD per million tokens). Cache writes are the
5-minute-TTL rate (1.25× input); cache reads are 0.1× input. Current Claude models serve
their 1M context at standard rates, so there's no long-context tier.

| Model | Input | Output | Cache write | Cache read |
| --- | --- | --- | --- | --- |
| `claude-opus-5`, `claude-opus-4-8` | $5 | $25 | $6.25 | $0.50 |
| `claude-sonnet-5`, `claude-sonnet-4-6` | $3 | $15 | $3.75 | $0.30 |
| `claude-haiku-4-5` | $1 | $5 | $1.25 | $0.10 |
| `claude-fable-5` | $10 | $50 | $12.50 | $1.00 |

Unknown model IDs fall back by family, so a new point release still prices correctly
instead of silently reading as free.

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

Or set `PTB_OAUTH_TOKEN` and the credentials file is never opened — the token you supply
is used directly. `/api/health` reports which source is active as `limits_token_source`.
Reading the mounted file is the default because it picks up token refreshes automatically;
a pasted token expires and has to be replaced by hand.

Failures degrade quietly and never affect token accounting. A rejected or expired token
surfaces as "run `/login`" in the UI, and a 429 backs off using the server's `Retry-After`.

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
  window resets.

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

A released Pokémon simply disappears — it does not graduate, so your Pokédex and the
branch-choice weights are untouched, as if it had never been drawn. There's no
legendary-only egg: that tier is decided by the `is_legendary` flag, which can't be
expressed as a capture-rate floor.

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
| `PTB_OAUTH_TOKEN` | — | Supply the token directly; skips reading the credentials file |
| `PTB_CLAUDE_DIR` | `~/.claude` | If your Claude config lives elsewhere |
| `PTB_CLAUDE_ROOTS` | — | Comma-separated log roots (set by compose) |
| `PTB_DATA_DIR` | `/data` | Save + caches |

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
| `POST /api/bag/candy` · `/api/bag/mint` | Use an item |
| `GET` / `POST /api/save` | Export / import your save |
| `GET /api/sprite/{id}?animated=&shiny=` | Cached sprite proxy |
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
cd backend && ../.venv/bin/python -m pytest -q   # 53 tests
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

import { useCallback, useEffect, useRef, useState } from "react";
import { api } from "../lib/api";
import { tokens } from "../lib/format";
import { Panel, Sprite } from "./Primitives";
import type { SnapshotView, StateView } from "../types";

/** Exactly 100 % within this distance of its slider position. */
const DEFAULT_SNAP_WIDTH = 0.01;

function clampDifficulty(v: number, lo: number, hi: number): number {
  if (!Number.isFinite(v)) return 1;
  return Math.min(Math.max(v, lo), hi);
}

function difficultyPosition(v: number, lo: number, hi: number): number {
  return Math.log(clampDifficulty(v, lo, hi) / lo) / Math.log(hi / lo);
}

/** Two significant figures: 1.234 -> 1.2, 0.0123 -> 0.012. */
function snapDifficulty(v: number, lo: number): number {
  if (v <= 0) return lo;
  const magnitude = Math.pow(10, Math.floor(Math.log10(v)) - 1);
  // Re-parse to drop float noise such as 0.30000000000000004.
  return Number((Math.round(v / magnitude) * magnitude).toPrecision(2));
}

function difficultyAt(position: number, lo: number, hi: number): number {
  const p = Math.min(Math.max(position, 0), 1);
  if (Math.abs(p - difficultyPosition(1, lo, hi)) < DEFAULT_SNAP_WIDTH) return 1;
  return snapDifficulty(lo * Math.pow(hi / lo, p), lo);
}

function DifficultyRow({
  id,
  label,
  value,
  lo,
  hi,
  disabled,
  onChange,
}: {
  id: string;
  label: string;
  value: number;
  lo: number;
  hi: number;
  disabled: boolean;
  onChange: (v: number) => void;
}) {
  return (
    <div className="difficulty__row">
      <label htmlFor={id}>{label}</label>
      <input
        id={id}
        type="range"
        min={0}
        max={1}
        step="any"
        disabled={disabled}
        value={difficultyPosition(value, lo, hi)}
        onChange={(e) => onChange(difficultyAt(Number(e.target.value), lo, hi))}
      />
      <span className="difficulty__value">{Math.round(value * 100)}%</span>
    </div>
  );
}

function snapshotDate(iso: string): string {
  const d = new Date(iso);
  return Number.isNaN(d.getTime())
    ? iso
    : d.toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" });
}

const LANGUAGES = [
  { code: "en", label: "English" },
  { code: "ko", label: "한국어" },
  { code: "ja", label: "日本語" },
  { code: "es", label: "Español" },
  { code: "fr", label: "Français" },
  { code: "de", label: "Deutsch" },
];

export function Settings({
  state,
  busy,
  onLanguage,
  onDifficulty,
  onLimitDisplay,
  onImported,
  notify,
}: {
  state: StateView;
  busy: boolean;
  onLanguage: (code: string) => void;
  onDifficulty: (growth: number, shop: number) => void;
  onLimitDisplay: (mode: "used" | "remaining") => void;
  onImported: () => void;
  notify: (message: string, tone?: "ok" | "error") => void;
}) {
  const fileInput = useRef<HTMLInputElement>(null);
  const [importing, setImporting] = useState(false);
  const { meta } = state;
  const lo = meta.difficulty_min;
  const hi = meta.difficulty_max;
  const [draft, setDraft] = useState({ growth: meta.growth_difficulty, shop: meta.shop_difficulty });
  const [synced, setSynced] = useState({ growth: meta.growth_difficulty, shop: meta.shop_difficulty });
  if (synced.growth !== meta.growth_difficulty || synced.shop !== meta.shop_difficulty) {
    // Saved (here or elsewhere): the draft follows the stored values.
    const stored = { growth: meta.growth_difficulty, shop: meta.shop_difficulty };
    setSynced(stored);
    setDraft(stored);
  }
  const draftDirty = draft.growth !== meta.growth_difficulty || draft.shop !== meta.shop_difficulty;

  const [snapshots, setSnapshots] = useState<SnapshotView[] | null>(null);
  const [snapshotBusy, setSnapshotBusy] = useState(false);

  const loadSnapshots = useCallback(async () => {
    try {
      setSnapshots(await api.snapshots());
    } catch (err) {
      notify(err instanceof Error ? err.message : "Could not list snapshots", "error");
    }
  }, [notify]);

  useEffect(() => {
    void loadSnapshots();
  }, [loadSnapshots]);

  async function handleCreateSnapshot() {
    setSnapshotBusy(true);
    try {
      await api.createSnapshot();
      notify("Snapshot created");
    } catch (err) {
      notify(err instanceof Error ? err.message : "Snapshot failed", "error");
    } finally {
      setSnapshotBusy(false);
      void loadSnapshots();
    }
  }

  async function handleRestore(snap: SnapshotView) {
    const body =
      "Restore this snapshot?\n\n" +
      `Target snapshot: ${snap.dex_count} in Pokédex · ${tokens(snap.lifetime_tokens)} lifetime\n` +
      `Created: ${snapshotDate(snap.created_at)}\n` +
      `Current state: ${state.collection.total} in Pokédex · ${tokens(state.wallet.used_since_install)} lifetime\n\n` +
      "Your current state will be backed up as a new snapshot before restoring.";
    if (!window.confirm(body)) return;
    setSnapshotBusy(true);
    try {
      const result = await api.restoreSnapshot(snap.id);
      notify(
        result.message ??
          `Restored — ${snap.dex_count} in Pokédex · ${tokens(snap.lifetime_tokens)} lifetime`,
      );
      onImported();
    } catch (err) {
      notify(err instanceof Error ? err.message : "Restore failed", "error");
    } finally {
      setSnapshotBusy(false);
      void loadSnapshots();
    }
  }

  // Changing the language has no visible effect on this tab otherwise, which reads
  // as the control being broken. Echo the names it actually affects.
  const preview = [
    state.companion.active?.name,
    ...state.collection.pokedex.slice(0, 3).map((p) => p.name),
  ].filter((n): n is string => Boolean(n));

  async function handleImport(file: File) {
    setImporting(true);
    try {
      const text = await file.text();
      const resp = await fetch("/api/save", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: text,
      });
      if (!resp.ok) {
        const body = await resp.json().catch(() => ({}));
        throw new Error(body.detail ?? resp.statusText);
      }
      notify("Save imported");
      onImported();
    } catch (err) {
      notify(err instanceof Error ? err.message : "Import failed", "error");
    } finally {
      setImporting(false);
      if (fileInput.current) fileInput.current.value = "";
    }
  }

  return (
    <div className="stack">
      <Panel title="Pokémon names">
        <div className="row">
          <label htmlFor="lang">Language</label>
          <select
            id="lang"
            disabled={busy}
            value={state.meta.language}
            onChange={(e) => onLanguage(e.target.value)}
          >
            {LANGUAGES.map((l) => (
              <option key={l.code} value={l.code}>
                {l.label}
              </option>
            ))}
          </select>
        </div>
        {preview.length > 0 ? (
          <p className="note">
            Now showing:{" "}
            {preview.map((name, i) => (
              <span key={i}>
                {i > 0 && ", "}
                <strong>{name}</strong>
              </span>
            ))}
          </p>
        ) : (
          <p className="note">
            No Pokémon to preview yet — the name will apply as soon as your egg hatches.
          </p>
        )}
        <p className="note">
          Names come from PokéAPI's own translations. Already-graduated Pokémon keep the
          names stored when they were caught.
        </p>
      </Panel>

      <Panel title="Difficulty">
        <div className="difficulty">
          <DifficultyRow
            id="growth-difficulty"
            label="Growth"
            value={draft.growth}
            lo={lo}
            hi={hi}
            disabled={busy}
            onChange={(growth) => setDraft((d) => ({ ...d, growth }))}
          />
          <DifficultyRow
            id="shop-difficulty"
            label="Shop prices"
            value={draft.shop}
            lo={lo}
            hi={hi}
            disabled={busy}
            onChange={(shop) => setDraft((d) => ({ ...d, shop }))}
          />
        </div>
        <p className="note">
          {Math.round(lo * 100)}%–{Math.round(hi * 100)}% · Percentages of the default
          balance — lower grows faster and costs less, higher does the opposite
        </p>
        {draftDirty && (
          <div className="row difficulty__save">
            <button
              className="btn btn--primary"
              disabled={busy}
              onClick={() => onDifficulty(draft.growth, draft.shop)}
            >
              Save
            </button>
          </div>
        )}
      </Panel>

      <Panel title="Limits">
        <div className="row row--wrap">
          <span className="row__label" id="limit-display-label">
            Limit display
          </span>
          <div className="segmented" role="radiogroup" aria-labelledby="limit-display-label">
            {(["used", "remaining"] as const).map((mode) => (
              <button
                key={mode}
                role="radio"
                aria-checked={meta.limit_display === mode}
                className={meta.limit_display === mode ? "is-active" : ""}
                disabled={busy}
                onClick={() => meta.limit_display !== mode && onLimitDisplay(mode)}
              >
                {mode === "used" ? "Used" : "Remaining"}
              </button>
            ))}
          </div>
        </div>
        <p className="note">
          Shows how much of each rate-limit window is used, or how much is left. Colours
          and alerts always follow the real usage.
        </p>
      </Panel>

      <Panel title="Save file">
        <div className="row row--wrap">
          <a className="btn" href="/api/save" download>
            Export save
          </a>
          <button
            className="btn"
            disabled={busy || importing}
            onClick={() => fileInput.current?.click()}
          >
            {importing ? "Importing…" : "Import save"}
          </button>
          <input
            ref={fileInput}
            type="file"
            accept="application/json,.json"
            hidden
            onChange={(e) => {
              const file = e.target.files?.[0];
              if (file) void handleImport(file);
            }}
          />
        </div>
        <p className="note">
          Importing replaces your whole collection. The usage baseline is re-seeded from
          this machine's logs, so today's existing tokens are not credited twice.
        </p>

        <div className="snapshots">
          <div className="snapshots__head">
            <div>
              <h3 className="snapshots__title">Backups (snapshots)</h3>
              <p className="note snapshots__hint">
                Saves an instant local restore point of your progress (keeps up to 10)
              </p>
            </div>
            <button
              className="btn"
              disabled={busy || snapshotBusy}
              onClick={() => void handleCreateSnapshot()}
            >
              Create snapshot
            </button>
          </div>
          {snapshots === null ? null : snapshots.length === 0 ? (
            <p className="empty snapshots__empty">No snapshots saved yet</p>
          ) : (
            <ul className="snapshots__list">
              {snapshots.map((snap) => (
                <li key={snap.id} className="snapshot">
                  <span className="snapshot__mon" aria-hidden="true">
                    {snap.current_species_id !== null ? (
                      <Sprite
                        speciesId={snap.current_species_id}
                        shiny={snap.current_is_shiny}
                        animated={false}
                        size={28}
                      />
                    ) : (
                      "🥚"
                    )}
                  </span>
                  <span className="snapshot__text">
                    <span className="snapshot__date">{snapshotDate(snap.created_at)}</span>
                    <span className="snapshot__caption">
                      Pokédex {snap.dex_count} · {tokens(snap.lifetime_tokens)} lifetime
                    </span>
                  </span>
                  <button
                    className="btn snapshot__restore"
                    disabled={busy || snapshotBusy}
                    onClick={() => void handleRestore(snap)}
                  >
                    Restore
                  </button>
                </li>
              ))}
            </ul>
          )}
        </div>
      </Panel>

      <Panel title="Where the numbers come from">
        <dl className="facts">
          <dt>Log roots</dt>
          <dd>
            {state.meta.log_roots.map((root) => (
              <code key={root}>
                {root}
                {state.meta.log_roots_present.includes(root) ? " ✓" : " — not found"}
              </code>
            ))}
          </dd>
          <dt>Session files seen</dt>
          <dd>{state.meta.log_files_found}</dd>
          <dt>Providers</dt>
          <dd>{state.meta.providers.join(", ")}</dd>
          <dt>Timezone</dt>
          <dd>{state.meta.timezone}</dd>
          <dt>Refresh interval</dt>
          <dd>{state.meta.poll_interval}s</dd>
          <dt>Last refresh</dt>
          <dd>
            {state.meta.last_refresh
              ? new Date(state.meta.last_refresh).toLocaleTimeString()
              : "never"}
          </dd>
          <dt>Files parsed last scan</dt>
          <dd>{state.usage.scanned_files}</dd>
          <dt>Official limits</dt>
          <dd>
            {state.limits.enabled
              ? state.limits.available
                ? `available${state.limits.plan ? ` · ${state.limits.plan}` : ""}`
                : `unavailable — ${state.limits.error ?? "unknown"}`
              : "disabled"}
          </dd>
          {state.limits.accounts.length > 1 && (
            <>
              <dt>Claude accounts</dt>
              <dd>{state.limits.accounts.map((a) => a.title).join(", ")}</dd>
            </>
          )}
        </dl>
      </Panel>

      <Panel title="About">
        <p className="note">
          A web port of{" "}
          <a href="https://github.com/chattymin/PokeTokenBar" target="_blank" rel="noreferrer">
            PokeTokenBar
          </a>{" "}
          by chattymin (MIT). An unofficial, non-commercial fan project with no
          affiliation to Nintendo, Game Freak, Creatures Inc. or The Pokémon Company.
          Pokémon data and sprites are fetched from PokéAPI at runtime and cached
          locally; none are bundled here.
        </p>
      </Panel>
    </div>
  );
}

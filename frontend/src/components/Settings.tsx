import { useRef, useState } from "react";
import { Panel } from "./Primitives";
import type { StateView } from "../types";

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
  onImported,
  notify,
}: {
  state: StateView;
  busy: boolean;
  onLanguage: (code: string) => void;
  onImported: () => void;
  notify: (message: string, tone?: "ok" | "error") => void;
}) {
  const fileInput = useRef<HTMLInputElement>(null);
  const [importing, setImporting] = useState(false);

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
      </Panel>

      <Panel title="Where the numbers come from">
        <dl className="facts">
          <dt>Log roots</dt>
          <dd>
            {state.meta.log_roots.map((root) => (
              <code key={root}>{root}</code>
            ))}
          </dd>
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

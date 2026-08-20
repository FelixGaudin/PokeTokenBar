import { useCallback, useEffect, useRef, useState } from "react";
import { api } from "./lib/api";
import { tokens } from "./lib/format";
import { Bag } from "./components/Bag";
import { Collection } from "./components/Collection";
import { Home } from "./components/Home";
import { Settings } from "./components/Settings";
import { Shop } from "./components/Shop";
import { Sprite } from "./components/Primitives";
import type { CompanionEvent, Rarity, StateView } from "./types";

const TABS = ["Home", "Shop", "Bag", "Collection", "Settings"] as const;
type Tab = (typeof TABS)[number];

const POLL_MS = 5_000;

interface Toast {
  id: number;
  message: string;
  tone: "ok" | "error";
}

const EVENT_COPY: Record<CompanionEvent["kind"], (e: CompanionEvent) => string> = {
  hatch: (e) => (e.shiny ? `A shiny ${e.name} hatched!` : `${e.name} hatched!`),
  evolve: (e) => `Evolved into ${e.name}!`,
  graduate: (e) => `${e.name} graduated to the Pokédex!`,
  ditto_reveal: (e) => `${e.disguise_name ?? "It"} was a Ditto all along!`,
};

export default function App() {
  const [state, setState] = useState<StateView | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [tab, setTab] = useState<Tab>("Home");
  const [busy, setBusy] = useState(false);
  const [toasts, setToasts] = useState<Toast[]>([]);
  const [celebration, setCelebration] = useState<CompanionEvent | null>(null);
  const toastId = useRef(0);

  const notify = useCallback((message: string, tone: "ok" | "error" = "ok") => {
    const id = ++toastId.current;
    setToasts((prev) => [...prev, { id, message, tone }]);
    window.setTimeout(() => setToasts((prev) => prev.filter((t) => t.id !== id)), 4200);
  }, []);

  const load = useCallback(
    async (force = false) => {
      try {
        const next = force ? await api.refresh() : await api.state();
        setState(next);
        setError(null);
        // The server hands out each flourish exactly once, so showing it here
        // cannot double-fire across polls.
        if (next.companion.event) setCelebration(next.companion.event);
      } catch (err) {
        setError(err instanceof Error ? err.message : String(err));
      }
    },
    [],
  );

  useEffect(() => {
    void load();
    const timer = window.setInterval(() => void load(), POLL_MS);
    return () => window.clearInterval(timer);
  }, [load]);

  useEffect(() => {
    if (!celebration) return;
    const timer = window.setTimeout(() => setCelebration(null), 5000);
    return () => window.clearTimeout(timer);
  }, [celebration]);

  const act = useCallback(
    async (fn: () => Promise<{ message: string | null }>) => {
      setBusy(true);
      try {
        const result = await fn();
        if (result.message) notify(result.message);
        await load();
      } catch (err) {
        notify(err instanceof Error ? err.message : String(err), "error");
      } finally {
        setBusy(false);
      }
    },
    [load, notify],
  );

  if (!state) {
    return (
      <div className="boot">
        <span className="boot__egg" aria-hidden="true">
          🥚
        </span>
        <p>{error ? `Cannot reach the server: ${error}` : "Reading your token logs…"}</p>
      </div>
    );
  }

  const wallet = state.wallet.available_tokens;
  const bagCount = state.bag.reduce((sum, item) => sum + (item.passive ? 0 : item.count), 0);

  return (
    <div className="app">
      <header className="topbar">
        <div className="topbar__brand">
          <span className="topbar__mark" aria-hidden="true">
            {state.companion.active ? (
              <Sprite
                speciesId={state.companion.active.species_id}
                shiny={state.companion.active.is_shiny}
                size={34}
              />
            ) : (
              "🥚"
            )}
          </span>
          <span>
            <strong>PokeTokenBar</strong>
            <small>{tokens(state.usage.today.total_tokens)} today</small>
          </span>
        </div>

        <nav className="tabs" role="tablist">
          {TABS.map((name) => (
            <button
              key={name}
              role="tab"
              aria-selected={tab === name}
              className={`tabs__btn ${tab === name ? "is-active" : ""}`}
              onClick={() => setTab(name)}
            >
              {name}
              {name === "Bag" && bagCount > 0 && <span className="tabs__count">{bagCount}</span>}
              {name === "Collection" && state.collection.total > 0 && (
                <span className="tabs__count">{state.collection.total}</span>
              )}
            </button>
          ))}
        </nav>

        <div className="topbar__right">
          <span className="topbar__wallet" title="Spendable tokens">
            💰 {tokens(wallet)}
          </span>
          <button
            className="btn btn--ghost"
            disabled={busy}
            onClick={() => void load(true)}
            title="Re-scan the logs now"
          >
            Refresh
          </button>
        </div>
      </header>

      {error && (
        <div className="banner banner--error">
          Lost contact with the server ({error}). Retrying every {POLL_MS / 1000}s.
        </div>
      )}

      {state.meta.source_warning && (
        <div className="banner banner--warn">{state.meta.source_warning}</div>
      )}

      <main className="content">
        {tab === "Home" && <Home state={state} />}
        {tab === "Shop" && (
          <Shop
            state={state}
            busy={busy}
            onBuyItem={(kind) => void act(() => api.buyItem(kind))}
            onBuyEgg={(tier: Rarity | null) => void act(() => api.buyEgg(tier))}
          />
        )}
        {tab === "Bag" && (
          <Bag
            state={state}
            busy={busy}
            onUseCandy={() => void act(() => api.useCandy())}
            onUseMint={() => void act(() => api.useMint())}
          />
        )}
        {tab === "Collection" && <Collection state={state} />}
        {tab === "Settings" && (
          <Settings
            state={state}
            busy={busy}
            onLanguage={(code) => void act(() => api.setLanguage(code))}
            onImported={() => void load()}
            notify={notify}
          />
        )}
      </main>

      {celebration && (
        <div className="celebration" role="status" onClick={() => setCelebration(null)}>
          <div className={`celebration__card ${celebration.shiny ? "is-shiny" : ""}`}>
            {celebration.species_id && (
              <Sprite
                speciesId={celebration.species_id}
                shiny={Boolean(celebration.shiny)}
                size={120}
                alt=""
              />
            )}
            <p className="celebration__text">{EVENT_COPY[celebration.kind](celebration)}</p>
          </div>
        </div>
      )}

      <div className="toasts" aria-live="polite">
        {toasts.map((t) => (
          <div key={t.id} className={`toast toast--${t.tone}`}>
            {t.message}
          </div>
        ))}
      </div>
    </div>
  );
}

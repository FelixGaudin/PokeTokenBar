import type {
  PokemonDetailView,
  Rarity,
  RecapScope,
  RecapView,
  SnapshotView,
  StateView,
} from "../types";

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const resp = await fetch(path, {
    ...init,
    headers: { "Content-Type": "application/json", ...(init?.headers ?? {}) },
  });
  if (!resp.ok) {
    // FastAPI puts the reason in `detail`; fall back to the status text.
    let detail = resp.statusText;
    try {
      const body = await resp.json();
      if (body && typeof body.detail === "string") detail = body.detail;
    } catch {
      /* body was not JSON — keep the status text */
    }
    throw new Error(detail);
  }
  return (await resp.json()) as T;
}

export const api = {
  state: () => request<StateView>("/api/state"),
  refresh: () => request<StateView>("/api/refresh", { method: "POST" }),
  buyItem: (kind: string) =>
    request<{ ok: boolean; message: string | null }>("/api/shop/item", {
      method: "POST",
      body: JSON.stringify({ kind }),
    }),
  buyEgg: (tier: Rarity | null) =>
    request<{ ok: boolean; message: string | null }>("/api/shop/egg", {
      method: "POST",
      body: JSON.stringify({ tier }),
    }),
  useCandy: (count = 1) =>
    request<{ ok: boolean; message: string | null }>("/api/bag/candy", {
      method: "POST",
      body: JSON.stringify({ count }),
    }),
  useMint: () =>
    request<{ ok: boolean; message: string | null }>("/api/bag/mint", { method: "POST" }),
  setDifficulty: (growth: number, shop: number) =>
    request<{ ok: boolean; message: string | null }>("/api/settings/difficulty", {
      method: "POST",
      body: JSON.stringify({ growth, shop }),
    }),
  setLimitDisplay: (mode: "used" | "remaining") =>
    request<{ ok: boolean; message: string | null }>("/api/settings/limit-display", {
      method: "POST",
      body: JSON.stringify({ mode }),
    }),
  recap: (scope: RecapScope, offset: number) =>
    request<RecapView>(`/api/recap?scope=${scope}&offset=${offset}`),
  pokemon: (speciesId: number, form?: string | null) =>
    request<PokemonDetailView>(
      `/api/pokemon/${speciesId}${form ? `?form=${encodeURIComponent(form)}` : ""}`,
    ),
  snapshots: () => request<SnapshotView[]>("/api/snapshots"),
  createSnapshot: () => request<SnapshotView>("/api/snapshots", { method: "POST" }),
  restoreSnapshot: (id: string) =>
    request<{ ok: boolean; message: string | null }>(
      `/api/snapshots/${encodeURIComponent(id)}/restore`,
      { method: "POST" },
    ),
  setLanguage: (language: string) =>
    request<{ ok: boolean; message: string | null }>("/api/settings/language", {
      method: "POST",
      body: JSON.stringify({ language }),
    }),
};

export function spriteUrl(
  speciesId: number,
  opts: { animated?: boolean; shiny?: boolean; form?: string | null } = {},
): string {
  const params = new URLSearchParams();
  params.set("animated", String(opts.animated ?? true));
  params.set("shiny", String(opts.shiny ?? false));
  // Unown A shares the plain sprite, so only other letters get a parameter; that
  // keeps one URL per image and lets the browser cache dedupe.
  if (opts.form && opts.form !== "a") params.set("form", opts.form);
  return `/api/sprite/${speciesId}?${params.toString()}`;
}

export function itemSpriteUrl(name: string): string {
  return `/api/sprite/item/${encodeURIComponent(name)}`;
}

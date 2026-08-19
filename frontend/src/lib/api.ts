import type { Rarity, StateView } from "../types";

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
  useCandy: () =>
    request<{ ok: boolean; message: string | null }>("/api/bag/candy", { method: "POST" }),
  useMint: () =>
    request<{ ok: boolean; message: string | null }>("/api/bag/mint", { method: "POST" }),
  setLanguage: (language: string) =>
    request<{ ok: boolean; message: string | null }>("/api/settings/language", {
      method: "POST",
      body: JSON.stringify({ language }),
    }),
};

export function spriteUrl(
  speciesId: number,
  opts: { animated?: boolean; shiny?: boolean } = {},
): string {
  const params = new URLSearchParams();
  params.set("animated", String(opts.animated ?? true));
  params.set("shiny", String(opts.shiny ?? false));
  return `/api/sprite/${speciesId}?${params.toString()}`;
}

export function itemSpriteUrl(name: string): string {
  return `/api/sprite/item/${encodeURIComponent(name)}`;
}

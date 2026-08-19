"""Per-model token rates, in USD per token.

Rates are declared per million tokens for readability. Cache-write rates use the
5-minute TTL multiplier (1.25x input); cache reads are 0.1x input. Current Claude
models serve their 1M context at standard rates, so there is no long-context tier
here.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ModelRate:
    input: float
    output: float
    cache_write: float
    cache_read: float

    @staticmethod
    def per_million(inp: float, out: float, cache_write: float, cache_read: float) -> "ModelRate":
        m = 1_000_000.0
        return ModelRate(inp / m, out / m, cache_write / m, cache_read / m)


ZERO = ModelRate(0.0, 0.0, 0.0, 0.0)

_OPUS = ModelRate.per_million(5, 25, 6.25, 0.5)
_SONNET = ModelRate.per_million(3, 15, 3.75, 0.3)
_HAIKU = ModelRate.per_million(1, 5, 1.25, 0.1)
_FABLE = ModelRate.per_million(10, 50, 12.5, 1.0)
_GPT = ModelRate.per_million(5, 30, 0, 0.5)

TABLE: dict[str, ModelRate] = {
    "claude-opus-5": _OPUS,
    "claude-opus-4-8": _OPUS,
    "claude-opus-4-7": _OPUS,
    "claude-opus-4-6": _OPUS,
    "claude-sonnet-5": _SONNET,
    "claude-sonnet-4-6": _SONNET,
    "claude-haiku-4-5": _HAIKU,
    "claude-haiku-4-5-20251001": _HAIKU,
    "claude-fable-5": _FABLE,
    "claude-mythos-5": _FABLE,
    "gpt-5.5": _GPT,
    "gemini-2.5-pro": ModelRate.per_million(1.25, 10, 0, 0.3125),
    "gemini-2.5-flash": ModelRate.per_million(0.30, 2.5, 0, 0.075),
    "gemini-2.0-flash": ModelRate.per_million(0.10, 0.4, 0, 0.025),
}


def rate_for(model: str) -> ModelRate:
    """Exact match first, then a family fallback so a new point release still prices."""
    exact = TABLE.get(model)
    if exact is not None:
        return exact

    m = model.lower()
    # Subscription-billed or server-priced sources report no per-token rate.
    if m.startswith("grok") or m.startswith("antigravity/"):
        return ZERO
    if "fable" in m or "mythos" in m:
        return _FABLE
    if "opus" in m:
        return _OPUS
    if "sonnet" in m:
        return _SONNET
    if "haiku" in m:
        return _HAIKU
    if any(tok in m for tok in ("gpt", "codex", "o4", "o3")):
        return _GPT
    if m.startswith("gemini"):
        if "pro" in m:
            return TABLE["gemini-2.5-pro"]
        if "flash" in m:
            return TABLE["gemini-2.5-flash"]
    return ZERO


def cost(model: str, *, input: int, output: int, cache_write: int, cache_read: int) -> float:
    r = rate_for(model)
    return (
        input * r.input
        + output * r.output
        + cache_write * r.cache_write
        + cache_read * r.cache_read
    )

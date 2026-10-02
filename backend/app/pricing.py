"""Per-model token rates, in USD per token.

Rates are declared per million tokens for readability. Cache-write rates use the
5-minute TTL multiplier; cache reads are whatever the pricing page lists (0.1x input
for most models, less for the newest ones). Current Claude models serve their 1M
context at standard rates, so there is no long-context tier here.

There is deliberately no family fallback: a model id containing "opus" is not
evidence that it shares another model's price. An unknown id has no price, and its
cost reads as "Unavailable" rather than borrowing a guess or reading as free.
"""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass

log = logging.getLogger(__name__)


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
_SONNET_4 = ModelRate.per_million(3, 15, 3.75, 0.3)

# Claude rates, checked against https://platform.claude.com/docs/en/about-claude/pricing
TABLE: dict[str, ModelRate] = {
    "claude-opus-5-5": ModelRate.per_million(4, 20, 5, 0.2),
    "claude-opus-5": _OPUS,
    "claude-sonnet-5": ModelRate.per_million(2, 10, 2.5, 0.2),
    "claude-fable-5-1": ModelRate.per_million(10, 50, 12.5, 0.25),
    "claude-fable-5": ModelRate.per_million(10, 50, 12.5, 1.0),
    "claude-opus-4-8": _OPUS,
    "claude-opus-4-7": _OPUS,
    "claude-opus-4-6": _OPUS,
    "claude-sonnet-4-6": _SONNET_4,
    "claude-sonnet-4-5-20250929": _SONNET_4,
    "claude-sonnet-4-20250514": _SONNET_4,
    "claude-opus-4-20250514": ModelRate.per_million(15, 75, 18.75, 1.5),
    "claude-haiku-4-5-20251001": ModelRate.per_million(1, 5, 1.25, 0.1),
}

_ALIASES = {
    "claude-sonnet-4": "claude-sonnet-4-20250514",
    "claude-opus-4": "claude-opus-4-20250514",
    "claude-sonnet-4-5": "claude-sonnet-4-5-20250929",
    "claude-haiku-4-5": "claude-haiku-4-5-20251001",
}

_PREFIXES = ("openai/", "anthropic/", "google/", "models/")


def model_key(model: str) -> str:
    """Normalise a model id the way the price lookup sees it."""
    key = model.strip().lower()
    for prefix in _PREFIXES:
        if key.startswith(prefix):
            key = key[len(prefix) :]
            break
    return _ALIASES.get(key, key)


def rate_for(model: str) -> ModelRate:
    return TABLE.get(model_key(model), ZERO)


def estimated_cost(
    model: str, *, input: int, output: int, cache_write: int, cache_read: int
) -> float | None:
    """Table price of one request, or None when the model has no verified rate."""
    r = TABLE.get(model_key(model))
    if r is None or min(input, output, cache_write, cache_read) < 0:
        return None
    # A zero write rate means "no supported write rate", not "free".
    if cache_write != 0 and r.cache_write == 0:
        return None
    return (
        input * r.input
        + output * r.output
        + cache_write * r.cache_write
        + cache_read * r.cache_read
    )


def cost(model: str, *, input: int, output: int, cache_write: int, cache_read: int) -> float:
    return (
        estimated_cost(
            model, input=input, output=output, cache_write=cache_write, cache_read=cache_read
        )
        or 0.0
    )


_unpriced_seen: set[str] = set()
_unpriced_lock = threading.Lock()


def first_unpriced_sighting(model: str) -> bool:
    key = model_key(model)
    with _unpriced_lock:
        if key in _unpriced_seen:
            return False
        _unpriced_seen.add(key)
        return True


def note_unpriced(model: str) -> None:
    """Log a missing price row once per process, so it is noticed but never spams."""
    if first_unpriced_sighting(model):
        log.warning(
            "Unpriced model: %s — its cost shows as Unavailable until pricing.TABLE has a row",
            model,
        )

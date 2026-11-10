"""Number formatting for the callout. Mirrors the web UI's shorthand."""

from __future__ import annotations


def tokens(n: float) -> str:
    n = float(n)
    for limit, suffix in ((1e12, "T"), (1e9, "B"), (1e6, "M"), (1e3, "K")):
        if abs(n) >= limit:
            scaled = n / limit
            digits = 1 if abs(scaled) < 100 else 0
            return f"{scaled:.{digits}f}{suffix}"
    return f"{n:.0f}"


def cost(value: float) -> str:
    return f"${value:,.2f}"


def percent(fraction: float) -> str:
    return f"{fraction * 100:.0f}%"


def rate(per_minute: float) -> str:
    return f"{tokens(per_minute)}/min"


def sulking(idle_seconds: float) -> str:
    """How long the companion has had its back turned: hours, then days from 48 h."""
    if idle_seconds <= 0:
        return ""
    hours = int(idle_seconds // 3600)
    if hours < 48:
        return f"Sulking for {hours} hours"
    return f"Sulking for {hours // 24} days"

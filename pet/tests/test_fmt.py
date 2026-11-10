"""The sulk line of the hover callout (upstream's SulkTests hover table)."""

from __future__ import annotations

from pet.fmt import sulking

HOUR = 3600


def test_no_sulk_adds_nothing():
    assert sulking(0) == ""
    assert sulking(-5) == ""


def test_hours_below_forty_eight_and_days_from_there():
    assert sulking(3 * HOUR) == "Sulking for 3 hours"
    assert sulking(24 * HOUR) == "Sulking for 24 hours"
    assert sulking(47.9 * HOUR) == "Sulking for 47 hours"
    assert sulking(48 * HOUR) == "Sulking for 2 days"
    assert sulking(100 * HOUR) == "Sulking for 4 days"

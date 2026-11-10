"""Petting: upstream's PettingDetectorTests and the heart bubble art/anchor tables."""

from __future__ import annotations

from pet.petting import (
    HEART_ART,
    HEART_COLOURS,
    HEART_DURATION_MS,
    HEART_HEIGHT,
    HEART_WIDTH,
    TAIL_OVERLAP,
    WINDOW,
    PettingDetector,
    anchor_fraction,
    bubble_top_offset,
    min_stroke,
    opaque_top_fraction,
)

STROKE = 14


def triggers(detector: PettingDetector, waypoints, step: float = 0.05) -> int:
    t, count = 100.0, 0
    for x in waypoints:
        if detector.feed(x, t, STROKE):
            count += 1
        t += step
    return count


def test_wiggle_triggers():
    # Four back-and-forth turns = 4 reversals; the third and fourth both fire.
    assert triggers(PettingDetector(), [10, 25, 40, 25, 10, 25, 40, 25, 10, 25, 40]) == 2


def test_straight_pass_does_not_trigger():
    assert triggers(PettingDetector(), range(0, 201, 10)) == 0


def test_slow_back_and_forth_does_not_trigger():
    # 0.8 s per stroke: three reversals span 1.6 s, outside the 1.2 s window.
    assert triggers(PettingDetector(), [10, 40, 10, 40, 10, 40, 10, 40], step=0.8) == 0


def test_a_two_hertz_wiggle_triggers():
    assert triggers(PettingDetector(), [10, 40, 10, 40, 10], step=0.25) > 0


def test_long_pause_resets_state():
    detector = PettingDetector()
    assert triggers(detector, [10, 40, 10, 40]) == 0, "only 2 reversals so far"
    t = 100 + WINDOW + 1
    assert not detector.feed(10, t, STROKE)
    assert not detector.feed(40, t + 0.05, STROKE)
    assert not detector.feed(10, t + 0.10, STROKE), "the old count must not carry over"


def test_jitter_below_minimum_stroke_does_not_trigger():
    jitter = [10 if i % 2 == 0 else 18 for i in range(30)]
    assert triggers(PettingDetector(), jitter) == 0


def test_minimum_stroke_scales_the_threshold():
    small, big = PettingDetector(), PettingDetector()
    small_hits = big_hits = 0
    t = 100.0
    for x in (10, 30, 10, 30, 10, 30, 10):
        small_hits += small.feed(x, t, 14)
        big_hits += big.feed(x, t, 40)
        t += 0.05
    assert small_hits > 0
    assert big_hits == 0


def test_reset_forgets_reversals():
    detector = PettingDetector()
    assert triggers(detector, [10, 40, 10, 40]) == 0
    detector.reset()  # pointer left, or a button went down
    assert triggers(detector, [10, 40, 10]) == 0


def test_minimum_stroke_follows_the_pet_size():
    assert min_stroke(48) == 10
    assert min_stroke(96) == 96 * 0.15
    assert min_stroke(384) == 384 * 0.15


def test_hearts_last_about_two_seconds():
    assert HEART_DURATION_MS == 1800


# ------------------------------------------------------------------- the art


def test_heart_art_rows_share_one_width():
    assert all(len(row) == len(HEART_ART[0]) for row in HEART_ART)
    assert (HEART_WIDTH, HEART_HEIGHT) == (34, 32)


def test_heart_and_tail_are_symmetric_except_shade_and_highlight():
    for row in HEART_ART:
        plain = "".join("R" if cell in "RDH" else cell for cell in row)
        assert plain == plain[::-1], row


def test_tail_tip_sits_at_the_horizontal_center():
    tip = HEART_ART[-1]
    assert tip.index("O") == len(HEART_ART[0]) // 2
    assert tip.count("O") == 1, "the tail ends in a single pixel"


def test_every_art_cell_has_a_colour_or_is_transparent():
    cells = set("".join(HEART_ART))
    assert cells == {"O", "W", "R", "D", "H", "."}
    assert all(cell in HEART_COLOURS for cell in cells - {"."})


# ----------------------------------------------------------- anchoring to the art


def image(opaque, side: int = 10) -> tuple[bytes, int, int, int]:
    """A side x side RGBA image (row 0 at the top) from per-pixel opaque flags."""
    pixels = bytearray(side * side * 4)
    for row in range(side):
        for column in range(side):
            if opaque(row, column):
                index = (row * side + column) * 4
                pixels[index] = 255
                pixels[index + 3] = 255
    return bytes(pixels), side, side, side * 4


def test_opaque_top_fraction_scans_the_first_visible_row():
    pixels, w, h, stride = image(lambda row, column: row == 3 and column == 4)
    assert abs(opaque_top_fraction(pixels, w, h, stride) - 0.3) < 1e-9


def test_side_columns_do_not_decide_the_anchor():
    # Raised arms at the sides from row 0; the head in the middle starts at row 5.
    pixels, w, h, stride = image(
        lambda row, column: column in (0, 9) or (4 <= column <= 5 and row >= 5)
    )
    assert abs(opaque_top_fraction(pixels, w, h, stride) - 0.5) < 1e-9


def test_empty_central_band_falls_back_to_the_full_width():
    pixels, w, h, stride = image(lambda row, column: column in (0, 9) and row >= 2)
    assert abs(opaque_top_fraction(pixels, w, h, stride) - 0.2) < 1e-9


def test_a_transparent_image_anchors_at_the_top():
    pixels, w, h, stride = image(lambda row, column: False)
    assert opaque_top_fraction(pixels, w, h, stride) == 0
    assert opaque_top_fraction(b"", 0, 0, 0) == 0


def test_anchor_is_the_median_frame():
    assert abs(anchor_fraction([0, 0, 0.3, 0.3, 0.3, 0.3, 0.3, 0.3, 0.3]) - 0.3) < 1e-9
    assert anchor_fraction([0.2]) == 0.2
    assert anchor_fraction([]) == 0


def test_bubble_tail_lands_just_inside_the_visible_top():
    top = bubble_top_offset(24, HEART_HEIGHT, TAIL_OVERLAP)
    assert top + HEART_HEIGHT == 24 + 3, "tail tip 3 px below the visible top"


def test_bubble_offset_snaps_to_whole_pixels():
    top = bubble_top_offset(3.84, 32, 3)
    assert isinstance(top, int)
    assert top == -25

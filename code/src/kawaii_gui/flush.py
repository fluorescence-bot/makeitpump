"""
Flush and clean volumes for the pump tubes. Shared by both GUIs: this file is
copied verbatim to ../kawaii_gui/flush.py (a test checks the copies match).

An empty tube has to fill up before any liquid comes out of its end, so before
a run each tube is flushed: it pumps the tube's own volume plus a fixed excess
that clears the line. Flush volumes are never counted in the mixing totals or
session logs.

    tube volume (ml) = pi * (inner diameter / 2)^2 * length / 1000   (mm -> ml)
    flush volume (ml) = tube volume + FLUSH_EXCESS_ML

Cleaning (water in all beakers, every outlet in its normal place) rinses in
CLEAN_ROUNDS rounds: channels 1-3 each pump their flush volume of water into the
mixing bowl, then channel 4 pumps all of that, plus its own flush volume, out
through the cuvette. So the bowl never holds more than one round of water.
"""

import math

TUBE_INNER_DIAMETER_MM = 2.79   # the tubes from the beakers and to the cuvette
FLUSH_EXCESS_ML = 3.0           # pumped on top of the tube's volume to clear it
CLEAN_ROUNDS = 3                # rinse rounds of a clean
MAX_TUBE_LENGTH_MM = 10000      # sanity limit for the settings field


def tube_volume_ml(length_mm: float) -> float:
    """What a tube of `length_mm` holds, in ml."""
    radius = TUBE_INNER_DIAMETER_MM / 2
    return math.pi * radius ** 2 * length_mm / 1000


def flush_ml(length_mm: float) -> float:
    """How much to pump to fill a tube of `length_mm` and clear it."""
    return tube_volume_ml(length_mm) + FLUSH_EXCESS_ML


def tube_length_error(length_mm) -> "str | None":
    """Why a tube length isn't valid, or None if it is fine."""
    try:
        value = float(length_mm)
    except (TypeError, ValueError):
        return "Tube length must be a number of mm"
    if not 0 < value <= MAX_TUBE_LENGTH_MM:
        return f"Tube length must be more than 0 and at most {MAX_TUBE_LENGTH_MM} mm"
    return None


def clean_plan(lengths_mm, ml_per_s: float) -> dict:
    """The pump steps of a clean, for the tube lengths of channels 1-4.

    Returns {"steps": [(ml ch1, ml ch2, ml ch3, ml ch4), ...] in order,
             "water_ml": [ml of water each beaker 1-3 needs],
             "seconds": estimated total time at `ml_per_s` per channel}.
    """
    f1, f2, f3, f4 = (flush_ml(length) for length in lengths_mm)
    rinse_in = (f1, f2, f3, 0.0)
    drain = (0.0, 0.0, 0.0, f1 + f2 + f3 + f4)
    steps = [rinse_in, drain] * CLEAN_ROUNDS
    seconds = sum(max(step) / ml_per_s for step in steps)
    return {"steps": steps,
            "water_ml": [CLEAN_ROUNDS * f for f in (f1, f2, f3)],
            "seconds": seconds}

"""Constants fixed by the problem statement, not by the instance.

Everything here is a rule of PS1 itself: the scoring bands, the ECLO yield and
the legal co-sharing mix. They are the same for every instance, public or
hidden, so they belong in code.

Anything that varies per instance -- line codes, bound names, station and hub
ids, buffer depths, natures of works, location id spelling -- must come from
the CSVs instead. See ``instance.py``.
"""

from __future__ import annotations

# Section 2.5: contract tier sets the band, activity_priority nudges within it.
CONTRACT_WEIGHT = {1: 100.0, 2: 10.0, 3: 1.0}
ACTIVITY_NUDGE = {1: 0.3, 2: 0.2, 3: 0.0}

# Section 2.4 rule 1: a standard night yields 1.0 access, an ECLO night 1.5.
STANDARD_YIELD = 1.0
ECLO_YIELD = 1.5

# Section 2.4 rule 5: one PM alone, or one PC + <=3 C, or <=4 C, per possession.
MAX_CO_SHARE = 4

# Section 2.3. ``exclusive`` takes the possession alone; ``hosts`` may open one
# that co-workers join; ``joins`` may co-share a possession opened by a host or
# by other co-workers.
ACCESS_ROLES = {
    "PM": {"exclusive": True, "hosts": False, "joins": False},
    "PC": {"exclusive": False, "hosts": True, "joins": False},
    "C": {"exclusive": False, "hosts": False, "joins": True},
}

CONTRACT_PRIORITIES = frozenset(CONTRACT_WEIGHT)
ACTIVITY_PRIORITIES = frozenset(ACTIVITY_NUDGE)
ACCESS_TYPES = frozenset(ACCESS_ROLES)


def night_yield(eclo: bool | int) -> float:
    """Access units earned by one scheduled night."""
    return ECLO_YIELD if eclo else STANDARD_YIELD

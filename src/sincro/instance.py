"""Instance model for PS1 — Railway Track Access Optimisation.

Parses the eight CSV instance files into a network model and provides the
location-expansion and closure-footprint logic that every later stage
(validator, constructor, solver) depends on.
"""

from __future__ import annotations

import csv
import datetime as dt
from dataclasses import dataclass, field
from pathlib import Path

BOUNDS = ("EB", "WB")
OPPOSITE = {"EB": "WB", "WB": "EB"}


@dataclass(frozen=True)
class Station:
    station_id: str
    line_code: str
    seq: int
    is_interchange: bool


@dataclass(frozen=True)
class Sector:
    sector_id: str
    line_code: str
    from_station_id: str
    to_station_id: str
    seq: int


@dataclass(frozen=True)
class Contract:
    contract_number: str
    description: str
    activity_type: str
    nature_of_activity: str
    contract_priority: int
    contract_completion_date: dt.date
    planned_completion_date: dt.date
    number_of_workfronts: int
    access_type: str
    max_access_per_week: int


@dataclass(frozen=True)
class Activity:
    activity_id: str
    contract_number: str
    activity_type: str
    start_location_id: str
    end_location_id: str
    total_accesses: int
    planned_start_date: dt.date
    predecessor_activity_id: str | None
    activity_priority: int


@dataclass
class Instance:
    horizon_start: dt.date
    horizon_weeks: int
    stations: list[Station]
    sectors: list[Sector]
    supply: dict[str, int]
    buffer_rules: dict[str, tuple[int, bool]]
    contracts: dict[str, Contract]
    activities: dict[str, Activity]

    # derived
    _sector_by_id: dict[str, Sector] = field(default_factory=dict, repr=False)
    _line_sectors: dict[str, list[Sector]] = field(default_factory=dict, repr=False)

    def __post_init__(self) -> None:
        self._sector_by_id = {s.sector_id: s for s in self.sectors}
        for s in self.sectors:
            self._line_sectors.setdefault(s.line_code, []).append(s)
        for line in self._line_sectors:
            self._line_sectors[line].sort(key=lambda s: s.seq)

    # ---------- calendar ----------

    def week_of(self, date: dt.date) -> int:
        """1-based week index of a date within the horizon."""
        return (date - self.horizon_start).days // 7 + 1

    def week_start(self, week: int) -> dt.date:
        return self.horizon_start + dt.timedelta(days=7 * (week - 1))

    def week_end(self, week: int) -> dt.date:
        return self.week_start(week) + dt.timedelta(days=6)

    # ---------- geography ----------

    @staticmethod
    def parse_location(loc: str) -> tuple[str, str, str, str]:
        """'SEC:ALP:S01_S02:EB' -> ('SEC', 'ALP', 'S01_S02', 'EB')."""
        kind, line, mid, bound = loc.split(":")
        return kind, line, mid, bound

    def sector_span(self, start_loc: str, end_loc: str) -> list[Sector]:
        """Ordered sectors covered from start to end (inclusive)."""
        k1, l1, m1, b1 = self.parse_location(start_loc)
        k2, l2, m2, b2 = self.parse_location(end_loc)
        if k1 != "SEC" or k2 != "SEC":
            raise ValueError(f"non-sector endpoint: {start_loc} -> {end_loc}")
        if l1 != l2 or b1 != b2:
            raise ValueError(f"endpoints differ in line/bound: {start_loc} -> {end_loc}")
        a = self._sector_by_id[f"SEC:{l1}:{m1}"]
        b = self._sector_by_id[f"SEC:{l2}:{m2}"]
        lo, hi = sorted((a.seq, b.seq))
        return [s for s in self._line_sectors[l1] if lo <= s.seq <= hi]

    def locations_for_sectors(self, sectors: list[Sector], bound: str) -> list[str]:
        """Sector ids + the platform sectors interior to the run."""
        if not sectors:
            return []
        line = sectors[0].line_code
        out = [f"{s.sector_id}:{bound}" for s in sectors]
        for s in sectors[:-1]:
            out.append(f"PLAT:{line}:{s.to_station_id}:{bound}")
        return out

    def span_locations(self, act: Activity) -> list[str]:
        """Every location the activity itself occupies (no buffer)."""
        _, _, _, bound = self.parse_location(act.start_location_id)
        return self.locations_for_sectors(
            self.sector_span(act.start_location_id, act.end_location_id), bound
        )

    def closure_footprint(self, act: Activity) -> set[str]:
        """Span plus exclusion buffer, opposite-bound mirroring and the
        Live-only cross-line reach at the interchange."""
        nature = self.contracts[act.contract_number].nature_of_activity
        buf_sectors, mirror = self.buffer_rules[nature]
        _, line, _, bound = self.parse_location(act.start_location_id)

        span = self.sector_span(act.start_location_id, act.end_location_id)
        line_seq = self._line_sectors[line]
        lo = min(s.seq for s in span)
        hi = max(s.seq for s in span)
        idx_lo = next(i for i, s in enumerate(line_seq) if s.seq == lo)
        idx_hi = next(i for i, s in enumerate(line_seq) if s.seq == hi)
        widened = line_seq[max(0, idx_lo - buf_sectors): idx_hi + 1 + buf_sectors]

        bounds = [bound] + ([OPPOSITE[bound]] if mirror else [])
        footprint: set[str] = set()
        for b in bounds:
            footprint.update(self.locations_for_sectors(widened, b))
            # the widened run's outer platforms are part of the closure too
            if widened:
                footprint.add(f"PLAT:{line}:{widened[0].from_station_id}:{b}")
                footprint.add(f"PLAT:{line}:{widened[-1].to_station_id}:{b}")

        # Live only: cutting traction power at the interchange reaches the
        # other line's H01_H02 tunnel and H01/H02 platforms.
        if mirror and any(s.sector_id.endswith("H01_H02") for s in widened):
            other = "BET" if line == "ALP" else "ALP"
            for b in BOUNDS:
                footprint.add(f"SEC:{other}:H01_H02:{b}")
                footprint.add(f"PLAT:{other}:H01:{b}")
                footprint.add(f"PLAT:{other}:H02:{b}")

        return {loc for loc in footprint if loc in self.supply}


def _date(s: str) -> dt.date:
    return dt.date.fromisoformat(s.strip())


def load_instance(data_dir: str | Path) -> Instance:
    d = Path(data_dir)

    params = {r["key"]: r["value"] for r in csv.DictReader(open(d / "06_PARAMETERS.csv"))}

    stations = [
        Station(r["station_id"], r["line_code"], int(r["seq"]), r["is_interchange"] == "1")
        for r in csv.DictReader(open(d / "02_STATIONS.csv"))
    ]
    sectors = [
        Sector(r["sector_id"], r["line_code"], r["from_station_id"], r["to_station_id"], int(r["seq"]))
        for r in csv.DictReader(open(d / "03_SECTORS.csv"))
    ]
    supply = {
        r["location_id"]: int(r["supply_capacity"])
        for r in csv.DictReader(open(d / "04_LOCATION_SUPPLY.csv"))
    }
    buffer_rules = {
        r["nature_of_works"]: (int(r["up_to_buffer_sectors"]), r["opposite_bound_required"] == "1")
        for r in csv.DictReader(open(d / "05_BUFFER_LOCATION.csv"))
    }
    contracts = {}
    for r in csv.DictReader(open(d / "07_PROJECT_DETAILS.csv")):
        contracts[r["contract_number"]] = Contract(
            r["contract_number"], r["contract_description"], r["activity_type"],
            r["nature_of_activity"], int(r["contract_priority"]),
            _date(r["contract_completion_date"]), _date(r["planned_completion_date"]),
            int(r["number_of_workfronts"]), r["access_type"],
            int(r["number_of_maximum_access_per_week"]),
        )
    activities = {}
    for r in csv.DictReader(open(d / "08_ACTIVITY_DETAILS.csv")):
        pred = (r["predecessor_activity_id"] or "").strip() or None
        activities[r["activity_id"]] = Activity(
            r["activity_id"], r["contract_number"], r["activity_type"],
            r["start_location_id"], r["end_location_id"], int(r["total_accesses"]),
            _date(r["planned_start_date"]), pred, int(r["activity_priority"]),
        )

    return Instance(
        horizon_start=_date(params["horizon_start"]),
        horizon_weeks=int(params["horizon_weeks"]),
        stations=stations, sectors=sectors, supply=supply,
        buffer_rules=buffer_rules, contracts=contracts, activities=activities,
    )

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
        self._line_sectors = {}
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
        route, lo, hi = self._span_positions(start_loc, end_loc)
        _, line, _, bound = self.parse_location(start_loc)
        occupied = set(route[lo:hi + 1])
        return [s for s in self._line_sectors[line] if f"{s.sector_id}:{bound}" in occupied]

    def _span_positions(self, start_loc: str, end_loc: str) -> tuple[list[str], int, int]:
        k1, line, _, bound = self.parse_location(start_loc)
        k2, other_line, _, other_bound = self.parse_location(end_loc)
        if line != other_line or bound != other_bound or bound not in BOUNDS:
            raise ValueError(f"endpoints differ in line/bound: {start_loc} -> {end_loc}")
        if k1 not in ("SEC", "PLAT") or k2 not in ("SEC", "PLAT") or line not in self._line_sectors:
            raise ValueError(f"invalid endpoints: {start_loc} -> {end_loc}")
        sectors = self._line_sectors[line]
        route = [f"PLAT:{line}:{sectors[0].from_station_id}:{bound}"]
        for sector in sectors:
            route.extend((f"{sector.sector_id}:{bound}", f"PLAT:{line}:{sector.to_station_id}:{bound}"))
        lo, hi = sorted((route.index(start_loc), route.index(end_loc)))
        # Sector endpoints include both bordering station platforms.
        return route, lo - (lo % 2), hi + (hi % 2)

    def locations_for_sectors(self, sectors: list[Sector], bound: str) -> list[str]:
        """Sector ids and every platform, including book-in and book-out."""
        if not sectors:
            return []
        line = sectors[0].line_code
        out = [f"{s.sector_id}:{bound}" for s in sectors]
        out.append(f"PLAT:{line}:{sectors[0].from_station_id}:{bound}")
        for s in sectors:
            out.append(f"PLAT:{line}:{s.to_station_id}:{bound}")
        return out

    def span_locations(self, act: Activity) -> list[str]:
        """Every location the activity itself occupies (no buffer)."""
        route, lo, hi = self._span_positions(act.start_location_id, act.end_location_id)
        return route[lo:hi + 1]

    def closure_footprint(self, act: Activity) -> set[str]:
        """Span plus exclusion buffer, opposite-bound mirroring and the
        Live-only cross-line reach at the interchange."""
        nature = self.contracts[act.contract_number].nature_of_activity
        buf_sectors, mirror = self.buffer_rules[nature]
        _, line, _, bound = self.parse_location(act.start_location_id)

        route, lo, hi = self._span_positions(act.start_location_id, act.end_location_id)
        widened = route[max(0, lo - 2 * buf_sectors):hi + 2 * buf_sectors + 1]

        bounds = [bound] + ([OPPOSITE[bound]] if mirror else [])
        footprint: set[str] = set()
        for b in bounds:
            footprint.update(f"{loc.rsplit(':', 1)[0]}:{b}" for loc in widened)

        # Live only: cutting traction power at the interchange reaches the
        # other line's H01_H02 tunnel and H01/H02 platforms.
        if mirror and (f"SEC:{line}:H01_H02:{bound}" in widened or
                       any(f"PLAT:{line}:{hub}:{bound}" in widened for hub in ("H01", "H02"))):
            other = "BET" if line == "ALP" else "ALP"
            for b in BOUNDS:
                footprint.add(f"SEC:{other}:H01_H02:{b}")
                footprint.add(f"PLAT:{other}:H01:{b}")
                footprint.add(f"PLAT:{other}:H02:{b}")

        return footprint

    def affected_lines(self, act: Activity) -> set[str]:
        return {self.parse_location(loc)[1] for loc in self.closure_footprint(act)}

    def has_exclusion(self, act: Activity) -> bool:
        sectors, mirror = self.buffer_rules[self.contracts[act.contract_number].nature_of_activity]
        return sectors > 0 or mirror

    def check(self) -> None:
        """Reject invalid instances before they can truncate work or loop forever."""
        if self.horizon_weeks < 1:
            raise ValueError("horizon_weeks must be positive")
        if not self.activities or not self.contracts:
            raise ValueError("instance must contain contracts and activities")
        if any(cap < 0 for cap in self.supply.values()):
            raise ValueError("supply capacities must be nonnegative")
        station_keys = {(s.line_code, s.station_id) for s in self.stations}
        if len(station_keys) != len(self.stations):
            raise ValueError("duplicate station on a line")
        for line, sectors in self._line_sectors.items():
            if len({s.seq for s in sectors}) != len(sectors):
                raise ValueError(f"{line}: duplicate sector sequence")
            for i, sector in enumerate(sectors):
                if any((line, sid) not in station_keys for sid in (sector.from_station_id, sector.to_station_id)):
                    raise ValueError(f"{sector.sector_id}: unknown station")
                if i and sectors[i - 1].to_station_id != sector.from_station_id:
                    raise ValueError(f"{line}: disconnected sector sequence")
        for cn, c in self.contracts.items():
            if c.contract_priority not in (1, 2, 3) or c.access_type not in ("PM", "PC", "C"):
                raise ValueError(f"{cn}: invalid contract priority or access type")
            if c.max_access_per_week < 1 or c.number_of_workfronts < 1:
                raise ValueError(f"{cn}: access allocation and workfronts must be positive")
            if c.nature_of_activity not in self.buffer_rules:
                raise ValueError(f"{cn}: unknown nature of activity")
            if not any(a.contract_number == cn for a in self.activities.values()):
                raise ValueError(f"{cn}: contract has no activities")
        for aid, a in self.activities.items():
            if a.contract_number not in self.contracts:
                raise ValueError(f"{aid}: unknown contract {a.contract_number}")
            if a.activity_type != self.contracts[a.contract_number].activity_type:
                raise ValueError(f"{aid}: activity_type differs from its contract")
            if a.total_accesses < 1 or a.activity_priority not in (1, 2, 3):
                raise ValueError(f"{aid}: workload must be positive and priority must be 1, 2 or 3")
            if a.predecessor_activity_id and a.predecessor_activity_id not in self.activities:
                raise ValueError(f"{aid}: unknown predecessor {a.predecessor_activity_id}")
            for loc in (a.start_location_id, a.end_location_id):
                if self.parse_location(loc)[3] not in BOUNDS:
                    raise ValueError(f"{aid}: invalid bound in {loc}")
            missing = (set(self.span_locations(a)) | self.closure_footprint(a)) - self.supply.keys()
            if missing:
                raise ValueError(f"{aid}: missing location supply: {sorted(missing)}")
        # Iterative walks also support long cross-contract dependency chains.
        done: set[str] = set()
        for aid in self.activities:
            path: set[str] = set()
            current = aid
            while current and current not in done:
                if current in path:
                    raise ValueError(f"predecessor cycle at {current}")
                path.add(current)
                current = self.activities[current].predecessor_activity_id
            done.update(path)


def _date(s: str) -> dt.date:
    return dt.date.fromisoformat(s.strip())


def load_instance(data_dir: str | Path) -> Instance:
    d = Path(data_dir)

    def rows(name: str, key: str | None = None) -> list[dict]:
        with (d / name).open(newline="", encoding="utf-8-sig") as f:
            reader = csv.DictReader(f)
            result = list(reader)
        if not reader.fieldnames or not result:
            raise ValueError(f"{name}: empty CSV")
        if any(None in r or None in r.values() for r in result):
            raise ValueError(f"{name}: malformed CSV row")
        result = [{k: v.strip() for k, v in r.items()} for r in result]
        if key and len({r[key] for r in result}) != len(result):
            raise ValueError(f"{name}: duplicate {key}")
        return result

    lines = {r["line_code"] for r in rows("01_LINES.csv", "line_code")}
    params = {r["key"]: r["value"] for r in rows("06_PARAMETERS.csv", "key")}

    stations = [
        Station(r["station_id"], r["line_code"], int(r["seq"]), r["is_interchange"] == "1")
        for r in rows("02_STATIONS.csv")
    ]
    sectors = [
        Sector(r["sector_id"], r["line_code"], r["from_station_id"], r["to_station_id"], int(r["seq"]))
        for r in rows("03_SECTORS.csv", "sector_id")
    ]
    supply = {
        r["location_id"]: int(r["supply_capacity"])
        for r in rows("04_LOCATION_SUPPLY.csv", "location_id")
    }
    buffer_rules = {
        r["nature_of_works"]: (int(r["up_to_buffer_sectors"]), r["opposite_bound_required"] == "1")
        for r in rows("05_BUFFER_LOCATION.csv", "nature_of_works")
    }
    expected_buffers = {"Live": (2, True), "Non-live (Consist)": (1, False),
                        "Non-live (Others)": (0, False)}
    if buffer_rules != expected_buffers:
        raise ValueError("buffer rules must match the three PS1 safety rules")
    if any(s.line_code not in lines for s in stations + sectors):
        raise ValueError("station or sector references an unknown line")
    contracts = {}
    for r in rows("07_PROJECT_DETAILS.csv", "contract_number"):
        contracts[r["contract_number"]] = Contract(
            r["contract_number"], r["contract_description"], r["activity_type"],
            r["nature_of_activity"], int(r["contract_priority"]),
            _date(r["contract_completion_date"]), _date(r["planned_completion_date"]),
            int(r["number_of_workfronts"]), r["access_type"],
            int(r["number_of_maximum_access_per_week"]),
        )
    activities = {}
    for r in rows("08_ACTIVITY_DETAILS.csv", "activity_id"):
        pred = (r["predecessor_activity_id"] or "").strip() or None
        activities[r["activity_id"]] = Activity(
            r["activity_id"], r["contract_number"], r["activity_type"],
            r["start_location_id"], r["end_location_id"], int(r["total_accesses"]),
            _date(r["planned_start_date"]), pred, int(r["activity_priority"]),
        )

    inst = Instance(
        horizon_start=_date(params["horizon_start"]),
        horizon_weeks=int(params["horizon_weeks"]),
        stations=stations, sectors=sectors, supply=supply,
        buffer_rules=buffer_rules, contracts=contracts, activities=activities,
    )
    inst.check()
    return inst

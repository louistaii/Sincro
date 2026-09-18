"""Instance model for PS1 - Railway Track Access Optimisation.

Parses the eight CSV instance files into a network model and provides the
location-expansion and closure-footprint logic that every later stage
(validator, constructor, solver) depends on.

Nothing about a particular instance is baked in. Line codes, bound names,
station and hub ids, buffer depths, natures of works and the spelling of
location ids are all read from the CSVs, so a hidden instance that renames
them -- or carries three lines instead of two -- behaves identically. The
rules the problem statement fixes for every instance live in ``rules.py``.

``04_LOCATION_SUPPLY.csv`` is the authority on geography: it states each
location's kind, line and bound as columns, and those columns are used rather
than re-derived by splitting the id. The id is only matched against the
sector and station ids the other files declare, so the tool never assumes a
``SEC:``/``PLAT:`` spelling.
"""

from __future__ import annotations

import csv
import datetime as dt
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path

from . import rules


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
    is_shared: bool = False


@dataclass(frozen=True)
class Location:
    """One bookable location, as declared by 04_LOCATION_SUPPLY.csv.

    Exactly one of ``sector_id`` / ``station_id`` is set: a location is either
    a stretch of track between stations or a platform at a station.
    """
    location_id: str
    kind_label: str
    line_code: str
    bound: str
    capacity: int
    sector_id: str | None = None
    station_id: str | None = None

    @property
    def is_sector(self) -> bool:
        return self.sector_id is not None


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
    locations: dict[str, Location]
    buffer_rules: dict[str, tuple[int, bool]]
    contracts: dict[str, Contract]
    activities: dict[str, Activity]

    # derived
    _sector_by_id: dict[str, Sector] = field(default_factory=dict, repr=False)
    _line_sectors: dict[str, list[Sector]] = field(default_factory=dict, repr=False)
    _loc_by_sector: dict[tuple[str, str], str] = field(default_factory=dict, repr=False)
    _loc_by_station: dict[tuple[str, str, str], str] = field(default_factory=dict, repr=False)
    _bounds_by_line: dict[str, list[str]] = field(default_factory=dict, repr=False)
    _routes: dict[tuple[str, str], list[str]] = field(default_factory=dict, repr=False)
    _hub_stations: frozenset[str] = field(default_factory=frozenset, repr=False)
    _hub_sectors: dict[str, frozenset[str]] = field(default_factory=dict, repr=False)

    def __post_init__(self) -> None:
        self._sector_by_id = {s.sector_id: s for s in self.sectors}
        self._line_sectors = defaultdict(list)
        for s in self.sectors:
            self._line_sectors[s.line_code].append(s)
        self._line_sectors = {k: sorted(v, key=lambda s: s.seq)
                              for k, v in self._line_sectors.items()}

        # Location lookups, so geography never needs the id's spelling.
        self._loc_by_sector = {}
        self._loc_by_station = {}
        bounds: dict[str, set[str]] = defaultdict(set)
        for loc in self.locations.values():
            bounds[loc.line_code].add(loc.bound)
            if loc.is_sector:
                self._loc_by_sector[(loc.sector_id, loc.bound)] = loc.location_id
            else:
                self._loc_by_station[(loc.line_code, loc.station_id, loc.bound)] = loc.location_id
        self._bounds_by_line = {line: sorted(b) for line, b in bounds.items()}

        # Interchange topology, derived: hubs are the stations flagged as
        # interchanges, and a hub sector is one running between two of them
        # (or explicitly flagged is_shared).
        self._hub_stations = frozenset(s.station_id for s in self.stations if s.is_interchange)
        self._hub_sectors = {
            s.sector_id: frozenset((s.from_station_id, s.to_station_id))
            for s in self.sectors
            if s.is_shared or {s.from_station_id, s.to_station_id} <= set(self._hub_stations)
        }

        self._routes = {}
        for line, sectors in self._line_sectors.items():
            for bound in self._bounds_by_line.get(line, ()):
                route = self._route_for(line, sectors, bound)
                if route:
                    self._routes[(line, bound)] = route

    def _route_for(self, line: str, sectors: list[Sector], bound: str) -> list[str]:
        """Platform, sector, platform, ... along a line on one bound."""
        first = self._loc_by_station.get((line, sectors[0].from_station_id, bound))
        if first is None:
            return []
        route = [first]
        for s in sectors:
            sec = self._loc_by_sector.get((s.sector_id, bound))
            plat = self._loc_by_station.get((line, s.to_station_id, bound))
            if sec is None or plat is None:
                return []
            route.extend((sec, plat))
        return route

    # ---------- calendar ----------

    def week_of(self, date: dt.date) -> int:
        """1-based week index of a date within the horizon."""
        return (date - self.horizon_start).days // 7 + 1

    def week_start(self, week: int) -> dt.date:
        return self.horizon_start + dt.timedelta(days=7 * (week - 1))

    def week_end(self, week: int) -> dt.date:
        return self.week_start(week) + dt.timedelta(days=6)

    # ---------- geography ----------

    @property
    def lines(self) -> list[str]:
        return sorted(self._bounds_by_line)

    def line_of(self, location_id: str) -> str:
        return self.locations[location_id].line_code

    def bound_of(self, location_id: str) -> str:
        return self.locations[location_id].bound

    def bounds_on(self, line: str) -> list[str]:
        return self._bounds_by_line.get(line, [])

    def opposite_bounds(self, line: str, bound: str) -> list[str]:
        """Every other bound on the line. With the usual two, the mirror."""
        return [b for b in self.bounds_on(line) if b != bound]

    def counterpart(self, location_id: str, bound: str) -> str | None:
        """The same sector or platform on another bound."""
        loc = self.locations[location_id]
        if loc.is_sector:
            return self._loc_by_sector.get((loc.sector_id, bound))
        return self._loc_by_station.get((loc.line_code, loc.station_id, bound))

    def is_hub_location(self, location_id: str) -> bool:
        """True where cutting traction power reaches the neighbouring line."""
        loc = self.locations[location_id]
        return loc.sector_id in self._hub_sectors if loc.is_sector \
            else loc.station_id in self._hub_stations

    def _span_positions(self, start_loc: str, end_loc: str) -> tuple[list[str], int, int]:
        for loc in (start_loc, end_loc):
            if loc not in self.locations:
                raise ValueError(f"unknown location: {loc}")
        a, b = self.locations[start_loc], self.locations[end_loc]
        if a.line_code != b.line_code or a.bound != b.bound:
            raise ValueError(f"endpoints differ in line/bound: {start_loc} -> {end_loc}")
        route = self._routes.get((a.line_code, a.bound))
        if not route or start_loc not in route or end_loc not in route:
            raise ValueError(f"invalid endpoints: {start_loc} -> {end_loc}")
        lo, hi = sorted((route.index(start_loc), route.index(end_loc)))
        # A sector endpoint includes both bordering station platforms.
        if self.locations[route[lo]].is_sector:
            lo -= 1
        if self.locations[route[hi]].is_sector:
            hi += 1
        return route, lo, hi

    def span_locations(self, act: Activity) -> list[str]:
        """Every location the activity itself occupies (no buffer)."""
        route, lo, hi = self._span_positions(act.start_location_id, act.end_location_id)
        return route[lo:hi + 1]

    def _widen(self, route: list[str], lo: int, hi: int, buf_sectors: int) -> list[str]:
        """Extend the span by ``buf_sectors`` sectors each way, plus the
        platform beyond them, without overrunning the end of the line."""
        def walk(i: int, step: int, limit: int) -> int:
            counted = 0
            while i != limit and counted < buf_sectors:
                i += step
                if self.locations[route[i]].is_sector:
                    counted += 1
            if i != limit and self.locations[route[i]].is_sector:
                i += step
            return i

        return route[walk(lo, -1, 0):walk(hi, 1, len(route) - 1) + 1]

    def closure_footprint(self, act: Activity) -> set[str]:
        """Span plus exclusion buffer, opposite-bound mirroring and the
        traction-power cross-line reach at the interchange."""
        nature = self.contracts[act.contract_number].nature_of_activity
        buf_sectors, mirror = self.buffer_rules[nature]
        line = self.line_of(act.start_location_id)
        bound = self.bound_of(act.start_location_id)

        route, lo, hi = self._span_positions(act.start_location_id, act.end_location_id)
        widened = self._widen(route, lo, hi, buf_sectors)

        bounds = [bound] + (self.opposite_bounds(line, bound) if mirror else [])
        footprint: set[str] = set()
        for loc in widened:
            for b in bounds:
                other = self.counterpart(loc, b)
                if other is not None:
                    footprint.add(other)

        # Cutting traction power at an interchange closes the neighbouring
        # line's hub tunnel and hub platforms too. Only natures that mirror
        # onto the opposite bound cut power, so only they cross lines.
        if mirror and any(self.is_hub_location(loc) for loc in widened):
            footprint |= self._cross_line_hub_closure(line, widened)

        return footprint

    def _cross_line_hub_closure(self, line: str, widened: list[str]) -> set[str]:
        """Hub locations on every other line, across all of their bounds."""
        touched = {self.locations[loc].sector_id for loc in widened
                   if self.locations[loc].is_sector and self.is_hub_location(loc)}
        keys = {self._hub_sectors[sid] for sid in touched if sid in self._hub_sectors}

        out: set[str] = set()
        for other in self.lines:
            if other == line:
                continue
            for b in self.bounds_on(other):
                for sid, key in self._hub_sectors.items():
                    if self._sector_by_id[sid].line_code == other and (not keys or key in keys):
                        loc = self._loc_by_sector.get((sid, b))
                        if loc is not None:
                            out.add(loc)
                for station_id in self._hub_stations:
                    loc = self._loc_by_station.get((other, station_id, b))
                    if loc is not None:
                        out.add(loc)
        return out

    def affected_lines(self, act: Activity) -> set[str]:
        return {self.line_of(loc) for loc in self.closure_footprint(act)}

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
        if not self.buffer_rules:
            raise ValueError("no buffer rules supplied")
        for nature, (depth, _) in self.buffer_rules.items():
            if depth < 0:
                raise ValueError(f"{nature}: buffer depth must be nonnegative")
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
            if not self._bounds_by_line.get(line):
                raise ValueError(f"{line}: no locations declared")
            for bound in self._bounds_by_line[line]:
                if (line, bound) not in self._routes:
                    raise ValueError(f"{line}/{bound}: incomplete location supply along the line")
        for cn, c in self.contracts.items():
            if c.contract_priority not in rules.CONTRACT_PRIORITIES:
                raise ValueError(f"{cn}: contract_priority must be one of "
                                 f"{sorted(rules.CONTRACT_PRIORITIES)}")
            if c.access_type not in rules.ACCESS_TYPES:
                raise ValueError(f"{cn}: access_type must be one of {sorted(rules.ACCESS_TYPES)}")
            if c.max_access_per_week < 1 or c.number_of_workfronts < 1:
                raise ValueError(f"{cn}: access allocation and workfronts must be positive")
            if c.nature_of_activity not in self.buffer_rules:
                raise ValueError(f"{cn}: unknown nature of activity {c.nature_of_activity!r}")
            if not any(a.contract_number == cn for a in self.activities.values()):
                raise ValueError(f"{cn}: contract has no activities")
        for aid, a in self.activities.items():
            if a.contract_number not in self.contracts:
                raise ValueError(f"{aid}: unknown contract {a.contract_number}")
            if a.activity_type != self.contracts[a.contract_number].activity_type:
                raise ValueError(f"{aid}: activity_type differs from its contract")
            if a.total_accesses < 1:
                raise ValueError(f"{aid}: workload must be positive")
            if a.activity_priority not in rules.ACTIVITY_PRIORITIES:
                raise ValueError(f"{aid}: activity_priority must be one of "
                                 f"{sorted(rules.ACTIVITY_PRIORITIES)}")
            if a.predecessor_activity_id and a.predecessor_activity_id not in self.activities:
                raise ValueError(f"{aid}: unknown predecessor {a.predecessor_activity_id}")
            for loc in (a.start_location_id, a.end_location_id):
                if loc not in self.locations:
                    raise ValueError(f"{aid}: unknown location {loc}")
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


def _flag(value: str) -> bool:
    return value.strip().lower() in ("1", "true", "yes", "y")


def _infer_delimiter(ids: list[str]) -> str | None:
    """The separator the instance spells its location ids with.

    Underscores are excluded: they appear inside sector ids such as
    ``S01_S02`` rather than between components.
    """
    counts: Counter[str] = Counter()
    for i in ids:
        for ch in set(i):
            if not ch.isalnum() and ch != "_":
                counts[ch] += 1
    return counts.most_common(1)[0][0] if counts else None


def _resolve_locations(rows: list[dict], stations: list[Station],
                       sectors: list[Sector]) -> dict[str, Location]:
    """Attach each supplied location to the sector or station it books.

    Matching is by the sector and station ids the instance itself declares,
    so no ``SEC:``/``PLAT:`` prefix or component count is assumed.
    """
    sector_ids = sorted({s.sector_id for s in sectors}, key=len, reverse=True)
    stations_by_line: dict[str, set[str]] = defaultdict(set)
    for st in stations:
        stations_by_line[st.line_code].add(st.station_id)

    ids = [r["location_id"] for r in rows]
    delim = _infer_delimiter(ids)

    def sector_match(loc_id: str) -> str | None:
        for sid in sector_ids:  # longest first, so the most specific id wins
            if loc_id == sid:
                return sid
            if loc_id.startswith(sid) and not loc_id[len(sid)].isalnum():
                return sid
        return None

    def station_match(loc_id: str, line: str, bound: str) -> str | None:
        known = stations_by_line.get(line, set())
        tokens = loc_id.split(delim) if delim else [loc_id]
        hits = [t for t in tokens if t in known]
        if len(hits) > 1:
            narrowed = [t for t in hits if t not in (line, bound)]
            hits = narrowed or hits
        if len(hits) != 1:
            return None
        return hits[0]

    out: dict[str, Location] = {}
    kind_class: dict[str, str] = {}
    for r in rows:
        loc_id, line, bound = r["location_id"], r["line_code"], r["bound"]
        kind = r.get("location_kind", "")
        sid = sector_match(loc_id)
        station_id = None if sid else station_match(loc_id, line, bound)
        if sid is None and station_id is None:
            raise ValueError(
                f"{loc_id}: matches no sector_id in 03_SECTORS.csv and no "
                f"station_id on line {line} in 02_STATIONS.csv")
        if sid is not None and sectors and self_line(sectors, sid) != line:
            raise ValueError(f"{loc_id}: declared on line {line} but "
                             f"{sid} belongs to {self_line(sectors, sid)}")
        this = "sector" if sid else "platform"
        if kind and kind_class.setdefault(kind, this) != this:
            raise ValueError(f"location_kind {kind!r} covers both sectors and platforms")
        out[loc_id] = Location(loc_id, kind, line, bound, int(r["supply_capacity"]), sid, station_id)
    return out


def self_line(sectors: list[Sector], sector_id: str) -> str:
    return next(s.line_code for s in sectors if s.sector_id == sector_id)


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
        Station(r["station_id"], r["line_code"], int(r["seq"]), _flag(r["is_interchange"]))
        for r in rows("02_STATIONS.csv")
    ]
    sectors = [
        Sector(r["sector_id"], r["line_code"], r["from_station_id"], r["to_station_id"],
               int(r["seq"]), _flag(r.get("is_shared", "0")))
        for r in rows("03_SECTORS.csv", "sector_id")
    ]
    supply_rows = rows("04_LOCATION_SUPPLY.csv", "location_id")
    supply = {r["location_id"]: int(r["supply_capacity"]) for r in supply_rows}
    buffer_rules = {
        r["nature_of_works"]: (int(r["up_to_buffer_sectors"]), _flag(r["opposite_bound_required"]))
        for r in rows("05_BUFFER_LOCATION.csv", "nature_of_works")
    }
    if any(s.line_code not in lines for s in stations + sectors):
        raise ValueError("station or sector references an unknown line")
    if any(r["line_code"] not in lines for r in supply_rows):
        raise ValueError("location supply references an unknown line")
    locations = _resolve_locations(supply_rows, stations, sectors)

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
        stations=stations, sectors=sectors, supply=supply, locations=locations,
        buffer_rules=buffer_rules, contracts=contracts, activities=activities,
    )
    inst.check()
    return inst

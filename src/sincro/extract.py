"""
Loads the 8 instance CSVs into plain Python objects.

Each class below is just a row from one CSV file, with the text values
converted to the right type (numbers become int, dates become dates, etc).
No merging between files, no calculations - just "read the file, get objects".
"""

import csv
import datetime as dt
from dataclasses import dataclass
from pathlib import Path


# --------------------------------------------------------------------------
# One class per CSV file. Field names match the CSV column names.
# --------------------------------------------------------------------------

@dataclass
class Line:                       # 01_LINES.csv
    line_code: str
    line_name: str


@dataclass
class Station:                    # 02_STATIONS.csv
    station_id: str
    line_code: str
    seq: int
    is_interchange: bool


@dataclass
class Sector:                     # 03_SECTORS.csv
    sector_id: str
    line_code: str
    from_station_id: str
    to_station_id: str
    seq: int
    is_shared: bool


@dataclass
class LocationSupply:             # 04_LOCATION_SUPPLY.csv
    location_id: str
    location_kind: str
    line_code: str
    bound: str
    supply_capacity: int


@dataclass
class BufferLocation:             # 05_BUFFER_LOCATION.csv
    nature_of_works: str
    up_to_buffer_sectors: int
    opposite_bound_required: bool


@dataclass
class Parameter:                  # 06_PARAMETERS.csv
    key: str
    value: str


@dataclass
class Contract:                   # 07_PROJECT_DETAILS.csv
    contract_number: str
    contract_description: str
    contract_award_date: dt.date
    activity_type: str
    nature_of_activity: str
    contract_priority: int
    contract_completion_date: dt.date
    planned_completion_date: dt.date
    number_of_workfronts: int
    access_type: str
    number_of_maximum_access_per_week: int


@dataclass
class Activity:                   # 08_ACTIVITY_DETAILS.csv
    activity_id: str
    contract_number: str
    activity_type: str
    start_location_id: str
    end_location_id: str
    total_accesses: int
    planned_start_date: dt.date
    predecessor_activity_id: str | None
    activity_priority: int


# --------------------------------------------------------------------------
# Reading helpers
# --------------------------------------------------------------------------

def _rows(path: Path) -> list[dict]:
    """Read a CSV file and return a list of {column_name: text_value} dicts."""
    with open(path, newline="") as f:
        return list(csv.DictReader(f))


def _date(text: str) -> dt.date:
    return dt.date.fromisoformat(text.strip())


def _bool(text: str) -> bool:
    return text.strip() == "1"


# --------------------------------------------------------------------------
# One loader function per file - each just builds a list of objects
# --------------------------------------------------------------------------

def load_lines(path: Path) -> list[Line]:
    return [Line(r["line_code"], r["line_name"]) for r in _rows(path)]


def load_stations(path: Path) -> list[Station]:
    return [
        Station(r["station_id"], r["line_code"], int(r["seq"]), _bool(r["is_interchange"]))
        for r in _rows(path)
    ]


def load_sectors(path: Path) -> list[Sector]:
    return [
        Sector(
            r["sector_id"], r["line_code"], r["from_station_id"], r["to_station_id"],
            int(r["seq"]), _bool(r["is_shared"]),
        )
        for r in _rows(path)
    ]


def load_location_supply(path: Path) -> list[LocationSupply]:
    return [
        LocationSupply(
            r["location_id"], r["location_kind"], r["line_code"], r["bound"],
            int(r["supply_capacity"]),
        )
        for r in _rows(path)
    ]


def load_buffer_locations(path: Path) -> list[BufferLocation]:
    return [
        BufferLocation(r["nature_of_works"], int(r["up_to_buffer_sectors"]), _bool(r["opposite_bound_required"]))
        for r in _rows(path)
    ]


def load_parameters(path: Path) -> list[Parameter]:
    return [Parameter(r["key"], r["value"]) for r in _rows(path)]


def load_contracts(path: Path) -> list[Contract]:
    return [
        Contract(
            r["contract_number"], r["contract_description"], _date(r["contract_award_date"]),
            r["activity_type"], r["nature_of_activity"], int(r["contract_priority"]),
            _date(r["contract_completion_date"]), _date(r["planned_completion_date"]),
            int(r["number_of_workfronts"]), r["access_type"],
            int(r["number_of_maximum_access_per_week"]),
        )
        for r in _rows(path)
    ]


def load_activities(path: Path) -> list[Activity]:
    return [
        Activity(
            r["activity_id"], r["contract_number"], r["activity_type"],
            r["start_location_id"], r["end_location_id"], int(r["total_accesses"]),
            _date(r["planned_start_date"]), (r["predecessor_activity_id"] or "").strip() or None,
            int(r["activity_priority"]),
        )
        for r in _rows(path)
    ]


# --------------------------------------------------------------------------
# Load everything at once
# --------------------------------------------------------------------------

def load_all(data_dir: str | Path) -> dict:
    """Loads all 8 files and returns a dict of lists, e.g. data['sectors']."""
    d = Path(data_dir)
    return {
        "lines": load_lines(d / "01_LINES.csv"),
        "stations": load_stations(d / "02_STATIONS.csv"),
        "sectors": load_sectors(d / "03_SECTORS.csv"),
        "location_supply": load_location_supply(d / "04_LOCATION_SUPPLY.csv"),
        "buffer_locations": load_buffer_locations(d / "05_BUFFER_LOCATION.csv"),
        "parameters": load_parameters(d / "06_PARAMETERS.csv"),
        "contracts": load_contracts(d / "07_PROJECT_DETAILS.csv"),
        "activities": load_activities(d / "08_ACTIVITY_DETAILS.csv"),
    }


    #data = load_all(FOLDER_PATH)
    #for name, rows in data.items():
    #   print(f"{name}: {len(rows)} rows, e.g. {rows[0]}")

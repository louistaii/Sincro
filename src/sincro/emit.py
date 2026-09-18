"""Write the three submission CSVs for a constructed schedule."""

from __future__ import annotations

import csv
import sys
from pathlib import Path

from .construct import construct
from .instance import load_instance


def emit(data_dir: str, out_dir: str, scenario: str, allow_eclo: bool = False) -> None:
    inst = load_instance(data_dir)
    res, _ = construct(inst, allow_eclo=allow_eclo)
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)

    with open(out / "SCHEDULE_ACCESS.csv", "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["activity_id", "access_seq", "week", "eclo", "access_night"])
        for aid, seq, week, eclo, night in res["placements"]:
            w.writerow([aid, seq, week, eclo, night])

    with open(out / "SCHEDULE_OCCUPANCY.csv", "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["activity_id", "week", "location_id", "co_share_group"])
        for aid, _, week, _, _ in res["placements"]:
            g = res["group_of"][(aid, week)]
            for loc in inst.span_locations(inst.activities[aid]):
                w.writerow([aid, week, loc, f"b{g + 1}"])

    with open(out / "RESULTS.csv", "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["scenario", "contract_number", "simulated_completion_date", "overrun_days"])
        fin: dict[str, int] = {}
        for aid, wk in res["finish_week"].items():
            cn = inst.activities[aid].contract_number
            fin[cn] = max(fin.get(cn, 0), wk)
        for cn, c in inst.contracts.items():
            date = inst.week_end(fin[cn])
            w.writerow([scenario, cn, date.isoformat(),
                        max(0, (date - c.planned_completion_date).days)])
    print(f"wrote {out}/SCHEDULE_ACCESS.csv, SCHEDULE_OCCUPANCY.csv, RESULTS.csv")


if __name__ == "__main__":
    emit(sys.argv[1], sys.argv[2], sys.argv[3] if len(sys.argv) > 3 else "A")

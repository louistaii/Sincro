"""Exact CP-SAT optimizers with priority-aware lexicographic tie-breaking."""
from __future__ import annotations

import datetime as dt
from collections import defaultdict
from ortools.sat.python import cp_model

from .instance import Instance
from .priority import calculate_priority
from .rules import ACTIVITY_NUDGE, CONTRACT_WEIGHT

GROUPS = range(4)  # statutory maximum activities in one possession


def _priority_weights(inst: Instance, scenario: str, as_of_date: dt.date) -> dict[str, int]:
    return {
        aid: max(
            1,
            round(
                1_000
                * calculate_priority(
                    inst, aid, scenario, as_of_date=as_of_date
                ).score
            ),
        )
        for aid in inst.activities
    }


def _solve_lexicographically(
    model: cp_model.CpModel,
    primary,
    secondary,
    scenario: str,
) -> tuple[cp_model.CpSolver, float, float, bool]:
    """Prove the official objective, lock it, then optimise the priority tie-break."""
    model.minimize(primary)
    primary_solver = cp_model.CpSolver()
    primary_solver.parameters.num_search_workers = 8
    status = primary_solver.solve(model)
    if status != cp_model.OPTIMAL:
        raise RuntimeError(
            f"Scenario {scenario} primary objective was not proven optimal: "
            f"{primary_solver.status_name(status)}"
        )
    primary_value = round(primary_solver.objective_value)
    model.add(primary == primary_value)
    # Seed the secondary solve with the proven primary solution. This keeps
    # the lexicographic pass practical despite possession and night symmetry.
    for index in range(len(model.proto.variables)):
        variable = model.get_int_var_from_proto_index(index)
        model.add_hint(variable, primary_solver.value(variable))
    model.minimize(secondary)
    solver = cp_model.CpSolver()
    solver.parameters.num_search_workers = 8
    solver.parameters.max_time_in_seconds = 30
    status = solver.solve(model)
    if status not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        raise RuntimeError(
            f"Scenario {scenario} priority tie-break found no solution: "
            f"{solver.status_name(status)}"
        )
    return solver, float(primary_value), solver.objective_value, status == cp_model.OPTIMAL


def solve_scenario_a(inst: Instance, as_of_date: dt.date | None = None) -> dict:
    """Return a proven-optimal schedule, or raise if proof is unavailable.

    No time limit is set: ``OPTIMAL`` is required, never merely ``FEASIBLE``.
    """
    m, H = cp_model.CpModel(), inst.horizon_weeks
    aids, weeks = list(inst.activities), range(1, H + 1)
    x = {(a, w, g): m.new_bool_var(f"x_{a}_{w}_{g}")
         for a in aids for w in weeks for g in GROUPS}
    active = {(a, w): sum(x[a, w, g] for g in GROUPS) for a in aids for w in weeks}
    for aid in aids:
        a = inst.activities[aid]
        for w in weeks:
            m.add(active[aid, w] <= 1)
            if w < max(1, inst.week_of(a.planned_start_date)): m.add(active[aid, w] == 0)
        m.add(sum(active[aid, w] for w in weeks) == a.total_accesses)
        if a.predecessor_activity_id:
            p, n = a.predecessor_activity_id, inst.activities[a.predecessor_activity_id].total_accesses
            for w in weeks: m.add(n * active[aid, w] <= sum(active[p, t] for t in range(1, w)))
    # Model local night labels directly so co-workers in one possession use
    # the same night and every contract respects its workfront count.
    night = {}
    for a in aids:
        c = inst.contracts[inst.activities[a].contract_number]
        for w in weeks:
            for n in range(1, c.max_access_per_week + 1):
                night[a, w, n] = m.new_bool_var(f"night_{a}_{w}_{n}")
            m.add(sum(night[a, w, n] for n in range(1, c.max_access_per_week + 1))
                  == active[a, w])
    for c in inst.contracts.values():
        members = [a.activity_id for a in inst.activities.values() if a.contract_number == c.contract_number]
        for w in weeks:
            for n in range(1, c.max_access_per_week + 1):
                m.add(sum(night[a, w, n] for a in members) <= c.number_of_workfronts)
    span = {a: set(inst.span_locations(inst.activities[a])) for a in aids}
    foot = {a: inst.closure_footprint(inst.activities[a]) for a in aids}
    for i, a in enumerate(aids):
        ca = inst.contracts[inst.activities[a].contract_number]
        for b in aids[i + 1:]:
            if (inst.activities[a].contract_number != inst.activities[b].contract_number
                    or inst.activities[a].activity_type != inst.activities[b].activity_type
                    or not span[a] & span[b]):
                continue
            for w in weeks:
                for g in GROUPS:
                    for n in range(1, ca.max_access_per_week + 1):
                        m.add(night[a,w,n] - night[b,w,n] <= 2 - x[a,w,g] - x[b,w,g])
                        m.add(night[b,w,n] - night[a,w,n] <= 2 - x[a,w,g] - x[b,w,g])
    # A group is a possession. Capacity counts distinct groups at each location.
    for loc, cap in inst.supply.items():
        here = [a for a in aids if loc in span[a]]
        for w in weeks:
            used = []
            for g in GROUPS:
                u = m.new_bool_var(f"u_{loc}_{w}_{g}"); used.append(u)
                members = [x[a, w, g] for a in here]
                if members: m.add_max_equality(u, members)
                else: m.add(u == 0)
                pm = [x[a,w,g] for a in here if inst.contracts[inst.activities[a].contract_number].access_type == "PM"]
                pc = [x[a,w,g] for a in here if inst.contracts[inst.activities[a].contract_number].access_type == "PC"]
                if pm: m.add(sum(pm) <= 1)
                if pc: m.add(sum(pc) <= 1)
                m.add(sum(x[a,w,g] for a in here) <= 4)
            m.add(sum(used) <= cap)
    # Buffered overlaps must be one real possession at a common span;
    # reusing a label at disjoint locations does not create co-sharing.
    for i, a in enumerate(aids):
        for b in aids[i+1:]:
            exclusion = inst.has_exclusion(inst.activities[a]) or inst.has_exclusion(inst.activities[b])
            if exclusion and foot[a] & foot[b]:
                for w in weeks:
                    if not span[a] & span[b]:
                        m.add(active[a, w] + active[b, w] <= 1)
                    else:
                        for g in GROUPS:
                            for h in GROUPS:
                                if g != h: m.add(x[a,w,g] + x[b,w,h] <= 1)
    penalties=[]
    finish_vars = {}
    for a in aids:
        act, c = inst.activities[a], inst.contracts[inst.activities[a].contract_number]
        fin=m.new_int_var(1,H,f"finish_{a}")
        finish_vars[a] = fin
        for w in weeks: m.add(fin >= w * active[a,w])
        values=[0]+[round(10*CONTRACT_WEIGHT[c.contract_priority]*(1+ACTIVITY_NUDGE[act.activity_priority])*max(0,(inst.week_end(w)-c.planned_completion_date).days)) for w in weeks]
        p=m.new_int_var(0,max(values),f"penalty_{a}"); m.add_element(fin,values,p); penalties.append(p)
    weights = _priority_weights(inst, "A", as_of_date or dt.date.today())
    primary = sum(penalties)
    secondary = sum(weights[a] * finish_vars[a] for a in aids)
    s, primary_value, priority_value, priority_proven = _solve_lexicographically(
        m, primary, secondary, "A"
    )
    placements=[]; group_of={}; seq=defaultdict(int); finish={}
    for w in weeks:
        for a in aids:
            if s.value(active[a,w]):
                g=next(g for g in GROUPS if s.value(x[a,w,g])); seq[a]+=1; group_of[a,w]=g
                c=inst.contracts[inst.activities[a].contract_number]
                n=next(n for n in range(1,c.max_access_per_week+1) if s.value(night[a,w,n]))
                placements.append((a,seq[a],w,0,n)); finish[a]=w
    return {"placements":placements,"group_of":group_of,"finish_week":finish,
            "objective":primary_value,"priority_objective":priority_value,
            "priority_proven":priority_proven}


def _solve_eclo_scenario(
    inst: Instance, scenario: str, as_of_date: dt.date | None = None
) -> dict:
    """Prove the minimum Scenario-B or Scenario-C penalty.

    Work is represented in half-access units: normal=2 and ECLO=3.
    """
    m, H = cp_model.CpModel(), inst.horizon_weeks
    aids, weeks = list(inst.activities), range(1, H + 1)
    normal = {(a,w,g): m.new_bool_var(f"n_{a}_{w}_{g}") for a in aids for w in weeks for g in GROUPS}
    eclo = {(a,w,g): m.new_bool_var(f"e_{a}_{w}_{g}") for a in aids for w in weeks for g in GROUPS}
    active = {(a,w): sum(normal[a,w,g]+eclo[a,w,g] for g in GROUPS) for a in aids for w in weeks}
    work = {(a,w): sum(2*normal[a,w,g]+3*eclo[a,w,g] for g in GROUPS) for a in aids for w in weeks}
    for aid in aids:
        a, c = inst.activities[aid], inst.contracts[inst.activities[aid].contract_number]
        for w in weeks:
            m.add(active[aid,w] <= 1)
            if w < max(1, inst.week_of(a.planned_start_date)) or (scenario == "B" and inst.week_end(w) > c.planned_completion_date): m.add(active[aid,w] == 0)
        m.add(sum(work[aid,w] for w in weeks) == 2*a.total_accesses)
        if a.predecessor_activity_id:
            p = a.predecessor_activity_id
            for w in weeks: m.add(2*inst.activities[p].total_accesses*active[aid,w] <= sum(work[p,t] for t in range(1,w)))
    night = {}
    for a in aids:
        c = inst.contracts[inst.activities[a].contract_number]
        for w in weeks:
            for n in range(1, c.max_access_per_week + 1):
                night[a, w, n] = m.new_bool_var(f"night_{a}_{w}_{n}")
            m.add(sum(night[a, w, n] for n in range(1, c.max_access_per_week + 1))
                  == active[a, w])
    for c in inst.contracts.values():
        members=[a.activity_id for a in inst.activities.values() if a.contract_number == c.contract_number]
        for w in weeks:
            for n in range(1, c.max_access_per_week + 1):
                m.add(sum(night[a,w,n] for a in members) <= c.number_of_workfronts)
    span={a:set(inst.span_locations(inst.activities[a])) for a in aids}; foot={a:inst.closure_footprint(inst.activities[a]) for a in aids}
    for i,a in enumerate(aids):
        ca = inst.contracts[inst.activities[a].contract_number]
        for b in aids[i+1:]:
            if (inst.activities[a].contract_number != inst.activities[b].contract_number
                    or inst.activities[a].activity_type != inst.activities[b].activity_type
                    or not span[a] & span[b]):
                continue
            for w in weeks:
                for g in GROUPS:
                    ag = normal[a,w,g] + eclo[a,w,g]
                    bg = normal[b,w,g] + eclo[b,w,g]
                    for n in range(1, ca.max_access_per_week + 1):
                        m.add(night[a,w,n] - night[b,w,n] <= 2 - ag - bg)
                        m.add(night[b,w,n] - night[a,w,n] <= 2 - ag - bg)
    if scenario == "C":
        window = {(line, start): m.new_bool_var(f"window_{line}_{start}")
                  for line in inst.lines for start in weeks}
        for line in inst.lines:
            m.add(sum(window[line, start] for start in weeks) <= 1)
        for a in aids:
            for line in inst.affected_lines(inst.activities[a]):
                for w in weeks:
                    covering = [window[line, start] for start in (w - 1, w)
                                if 1 <= start <= H]
                    for g in GROUPS:
                        m.add(eclo[a, w, g] <= sum(covering))
    excess=[]
    for loc,cap in inst.supply.items():
        here=[a for a in aids if loc in span[a]]
        for w in weeks:
            used=[]
            for g in GROUPS:
                u=m.new_bool_var(f"u_{loc}_{w}_{g}"); used.append(u)
                terms=[normal[a,w,g]+eclo[a,w,g] for a in here]
                if terms: m.add_max_equality(u,terms)
                else: m.add(u==0)
                pm=[normal[a,w,g]+eclo[a,w,g] for a in here if inst.contracts[inst.activities[a].contract_number].access_type=="PM"]
                pc=[normal[a,w,g]+eclo[a,w,g] for a in here if inst.contracts[inst.activities[a].contract_number].access_type=="PC"]
                if pm: m.add(sum(pm)<=1)
                if pc: m.add(sum(pc)<=1)
                m.add(sum(normal[a,w,g]+eclo[a,w,g] for a in here)<=4)
            over=m.new_int_var(0,len(GROUPS),f"over_{loc}_{w}"); m.add(over >= sum(used)-cap)
            if scenario == "C": m.add(over <= 1)
            excess.append(over)
    for i,a in enumerate(aids):
        for b in aids[i+1:]:
            exclusion = inst.has_exclusion(inst.activities[a]) or inst.has_exclusion(inst.activities[b])
            if exclusion and foot[a]&foot[b]:
                for w in weeks:
                    if not span[a] & span[b]:
                        m.add(active[a,w] + active[b,w] <= 1)
                    else:
                        for g in GROUPS:
                            for h in GROUPS:
                                if g!=h: m.add(normal[a,w,g]+eclo[a,w,g]+normal[b,w,h]+eclo[b,w,h] <= 1)
    penalties=[]
    finish_vars = {}
    for a in aids:
        act, c = inst.activities[a], inst.contracts[inst.activities[a].contract_number]
        fin=m.new_int_var(1,H,f"finish_{a}")
        finish_vars[a] = fin
        for w in weeks: m.add(fin >= w*active[a,w])
        if scenario == "C":
            values=[0]+[round(10*CONTRACT_WEIGHT[c.contract_priority]*(1+ACTIVITY_NUDGE[act.activity_priority])*max(0,(inst.week_end(w)-c.planned_completion_date).days)) for w in weeks]
            p=m.new_int_var(0,max(values),f"penalty_{a}"); m.add_element(fin,values,p); penalties.append(p)
    primary = 5*10*sum(eclo.values())+7*10*sum(excess)+sum(penalties)
    weights = _priority_weights(inst, scenario, as_of_date or dt.date.today())
    secondary = sum(weights[a] * finish_vars[a] for a in aids)
    s, primary_value, priority_value, priority_proven = _solve_lexicographically(
        m, primary, secondary, scenario
    )
    placements=[]; group_of={}; seq=defaultdict(int); finish={}
    for w in weeks:
        for a in aids:
            if s.value(active[a,w]):
                g=next(g for g in GROUPS if s.value(normal[a,w,g])+s.value(eclo[a,w,g])); is_eclo=s.value(eclo[a,w,g]); seq[a]+=1; group_of[a,w]=g
                c=inst.contracts[inst.activities[a].contract_number]
                n=next(n for n in range(1,c.max_access_per_week+1) if s.value(night[a,w,n]))
                placements.append((a,seq[a],w,is_eclo,n)); finish[a]=w
    return {"placements":placements,"group_of":group_of,"finish_week":finish,
            "objective":primary_value,"priority_objective":priority_value,
            "priority_proven":priority_proven}


def solve_scenario_b(inst: Instance, as_of_date: dt.date | None = None) -> dict:
    return _solve_eclo_scenario(inst, "B", as_of_date)


def solve_scenario_c(inst: Instance, as_of_date: dt.date | None = None) -> dict:
    return _solve_eclo_scenario(inst, "C", as_of_date)

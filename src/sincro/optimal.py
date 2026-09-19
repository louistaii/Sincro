"""Exact CP-SAT optimisers with priority-aware lexicographic tie-breaking.

One model, three policies. The physics of the railway -- spans, buffers,
capacity, legal mixes, workfronts, co-sharing, predecessors -- is identical in
every scenario, so it is written once in ``_build``. What differs is policy:
whether ECLO is legal, whether planned dates are hard, whether overrun is
scored, how much capacity excess is tolerated, and whether ECLO nights must sit
inside one window per line. That lives in ``ScenarioPolicy``.

Each solve is lexicographic: prove the activity-own-delay objective first,
lock it, then spend the remaining freedom minimising priority-weighted
completion so that, among equally-scoring schedules, urgent work finishes
earlier.

The solver never gives up on an instance. If the nominal horizon cannot hold
the workload it is extended; if optimality cannot be proved in the time
allowed, the best proven-feasible schedule is returned and flagged; and if
CP-SAT finds nothing at all, the caller falls back to the heuristic. Section 1
of the brief makes that non-negotiable: a congested case must still produce a
schedule.
"""
from __future__ import annotations

import datetime as dt
import gc
import os
from collections import defaultdict
from dataclasses import dataclass

from ortools.sat.python import cp_model

from . import rules
from .instance import Instance
from .priority import calculate_priority

# The brief's weights carry one decimal (the activity nudge); CP-SAT wants
# integers, so every objective term is scaled by this factor.
SCALE = 10

# Scenario B's capacity excess is unbounded in the brief. A model needs some
# bound, so allow this much headroom above the nominal supply per location-week;
# at 7 per excess night the optimum never approaches it.
UNBOUNDED_EXCESS_HEADROOM = 2

# Match the box rather than assuming eight cores: oversubscribing CP-SAT's
# workers on a small container costs more in contention than it buys.
def _search_workers() -> int:
    # Cloud containers can expose host CPUs rather than their CPU quota.
    # Each CP-SAT worker also consumes memory. Never infer eight workers from
    # a shared host when the service may have only one allocated vCPU.
    default = 1 if os.environ.get('K_SERVICE') else max(1, min(8, os.cpu_count() or 1))
    value = int(os.environ.get('SINCRO_SOLVER_WORKERS', default))
    if not 1 <= value <= 64:
        raise ValueError('SINCRO_SOLVER_WORKERS must be between 1 and 64')
    return value


SEARCH_WORKERS = _search_workers()

# How far past the declared horizon the model may reach when the workload does
# not fit, and in what steps.
HORIZON_GROWTH = 1.5
MAX_HORIZON_ATTEMPTS = 4


@dataclass(frozen=True)
class ScenarioPolicy:
    """What distinguishes one scenario from another."""
    name: str
    allow_eclo: bool                        # A forbids it outright
    hard_planned_date: bool                 # B cannot overrun at all
    score_overrun: bool                     # A and C price lateness
    max_excess_per_location_week: int | None  # None means unbounded (B)
    eclo_window_weeks: int | None           # C confines ECLO per line
    may_extend_horizon: bool                # B's dates bind, so growing cannot help


POLICIES = {
    "A": ScenarioPolicy("A", allow_eclo=False, hard_planned_date=False,
                        score_overrun=True, max_excess_per_location_week=0,
                        eclo_window_weeks=None, may_extend_horizon=True),
    "B": ScenarioPolicy("B", allow_eclo=True, hard_planned_date=True,
                        score_overrun=False, max_excess_per_location_week=None,
                        eclo_window_weeks=None, may_extend_horizon=False),
    "C": ScenarioPolicy("C", allow_eclo=True, hard_planned_date=False,
                        score_overrun=True,
                        max_excess_per_location_week=rules.C_EXCESS_ALLOWANCE,
                        eclo_window_weeks=rules.ECLO_WINDOW_WEEKS,
                        may_extend_horizon=True),
}


class ExactSolveFailed(RuntimeError):
    """CP-SAT produced no schedule; the caller should fall back."""

    def __init__(self, message: str, status: int | None = None):
        super().__init__(message)
        self.status = status


def _role(inst: Instance, aid: str) -> dict:
    access = inst.contracts[inst.activities[aid].contract_number].access_type
    return rules.ACCESS_ROLES[access]


def _possessions_per_location(inst: Instance, policy: ScenarioPolicy) -> range:
    """How many distinct possessions a location-week may hold.

    Nominal supply plus whatever excess the scenario tolerates -- modelling
    fewer would quietly deny a scenario the elasticity the brief grants it.
    """
    cap = max(inst.supply.values(), default=0)
    headroom = policy.max_excess_per_location_week
    if headroom is None:
        headroom = UNBOUNDED_EXCESS_HEADROOM
    return range(max(1, cap + headroom))


def _default_as_of(inst: Instance) -> dt.date:
    """Anchor priority on the instance, not on the wall clock.

    Using ``date.today()`` would make the tie-break -- and therefore the
    emitted schedule -- depend on which day the solver happened to run.
    """
    return inst.horizon_start


def _strict_improvement_horizon(inst: Instance, incumbent: int) -> int:
    """Return a finite horizon containing every schedule beating an incumbent."""
    target = incumbent - 1
    if target < 0:
        return inst.horizon_weeks
    latest = []
    for activity in inst.activities.values():
        contract = inst.contracts[activity.contract_number]
        weight = round(SCALE * rules.CONTRACT_WEIGHT[contract.contract_priority]
                       * (1 + rules.ACTIVITY_NUDGE[activity.activity_priority]))
        deadline_offset = (contract.planned_completion_date - inst.horizon_start).days
        latest.append((deadline_offset + 1 + target // weight) // 7)
    return max([inst.horizon_weeks, *latest])


def _build(inst: Instance, policy: ScenarioPolicy, horizon: int, as_of_date: dt.date,
           schedule_constraints: dict | None = None, *, compact: bool = False):
    """Assemble the shared model. Returns the pieces the caller needs."""
    m = cp_model.CpModel()
    aids, weeks = list(inst.activities), range(1, horizon + 1)
    # Under the existing weekly closure rules, activities occupying a common
    # location must co-share. Consequently a location-week can contain only one
    # possession. Relabelling every possession to zero preserves all constraints:
    # disjoint sites still need a real shared location to waive their buffers.
    # Removing these interchangeable labels cuts the search dramatically without
    # admitting any additional schedule. Keep the expanded representation for
    # differential checks and tests that explicitly constrain possession labels.
    groups = range(1) if compact else _possessions_per_location(inst, policy)

    # A night is either standard (worth 2 half-units) or ECLO (worth 3).
    normal = {(a, w, g): m.new_bool_var(f"n_{a}_{w}_{g}")
              for a in aids for w in weeks for g in groups}
    if policy.allow_eclo:
        eclo = {(a, w, g): m.new_bool_var(f"e_{a}_{w}_{g}")
                for a in aids for w in weeks for g in groups}
    else:
        eclo = {}

    def at(a, w, g):
        """Is activity ``a`` in possession ``g`` at week ``w``?"""
        return normal[a, w, g] + eclo[a, w, g] if policy.allow_eclo else normal[a, w, g]

    active = {(a, w): sum(at(a, w, g) for g in groups) for a in aids for w in weeks}
    work = {(a, w): sum(2 * normal[a, w, g] + 3 * eclo[a, w, g] for g in groups)
                    if policy.allow_eclo else sum(2 * normal[a, w, g] for g in groups)
            for a in aids for w in weeks}

    # ---- workload, release dates, predecessors (rules 1-3) ----
    for aid in aids:
        a = inst.activities[aid]
        c = inst.contracts[a.contract_number]
        release = max(1, inst.week_of(a.planned_start_date))
        for w in weeks:
            m.add(active[aid, w] <= 1)          # at most one access-night per week
            too_early = w < release
            too_late = policy.hard_planned_date and inst.week_end(w) > c.planned_completion_date
            if too_early or too_late:
                m.add(active[aid, w] == 0)
        m.add(sum(work[aid, w] for w in weeks) == 2 * a.total_accesses)
        if a.predecessor_activity_id:
            p = a.predecessor_activity_id
            done = 2 * inst.activities[p].total_accesses
            for w in weeks:
                m.add(done * active[aid, w] <= sum(work[p, t] for t in range(1, w)))

    # ---- local night labels, workfronts (rules 7-8) ----
    night = {}
    for a in aids:
        c = inst.contracts[inst.activities[a].contract_number]
        nights = range(1, c.max_access_per_week + 1)
        for w in weeks:
            for n in nights:
                night[a, w, n] = m.new_bool_var(f"night_{a}_{w}_{n}")
            m.add(sum(night[a, w, n] for n in nights) == active[a, w])
    for c in inst.contracts.values():
        members = [a.activity_id for a in inst.activities.values()
                   if a.contract_number == c.contract_number]
        for w in weeks:
            for n in range(1, c.max_access_per_week + 1):
                m.add(sum(night[a, w, n] for a in members) <= c.number_of_workfronts)

    # Change control can protect visible decisions inside a rolling freeze
    # window. Possession-group ids remain free because they are an internal
    # representation and may safely be reassigned without moving an access.
    for (a, w), decision in (schedule_constraints or {}).get('fixed', {}).items():
        if a not in inst.activities or w not in weeks:
            continue
        if decision is None:
            m.add(active[a, w] == 0)
            continue
        m.add(active[a, w] == 1)
        if policy.allow_eclo:
            m.add(sum(eclo[a, w, g] for g in groups) == int(decision['eclo']))
        elif decision['eclo']:
            raise ValueError(f'Scenario {policy.name} cannot preserve an ECLO access')
        selected_night = int(decision['access_night'])
        if (a, w, selected_night) not in night:
            raise ValueError(f'{a} has an invalid protected local night {selected_night}')
        m.add(night[a, w, selected_night] == 1)

    span = {a: set(inst.span_locations(inst.activities[a])) for a in aids}
    foot = {a: inst.closure_footprint(inst.activities[a]) for a in aids}

    # Rule 6: a (location, week, co_share_group) possession is one access-night
    # slot. That binds every pair sharing a location inside one possession,
    # whatever contract each belongs to. Restricting it to co-workers of a
    # single contract let one label span two nights, which is two possessions
    # wearing one name: it hides a closure conflict between them and lets the
    # capacity count below score two real possessions as one.
    for i, a in enumerate(aids):
        na = inst.contracts[inst.activities[a].contract_number].max_access_per_week
        for b in aids[i + 1:]:
            if not span[a] & span[b]:
                continue
            nb = inst.contracts[inst.activities[b].contract_number].max_access_per_week
            for w in weeks:
                for g in groups:
                    together = at(a, w, g) + at(b, w, g)
                    for n in range(1, max(na, nb) + 1):
                        va, vb = night.get((a, w, n)), night.get((b, w, n))
                        # A night one contract cannot reach is unavailable to
                        # the whole possession.
                        if va is None and vb is None:
                            continue
                        if vb is None:
                            m.add(va <= 2 - together)
                        elif va is None:
                            m.add(vb <= 2 - together)
                        else:
                            m.add(va - vb <= 2 - together)
                            m.add(vb - va <= 2 - together)

    # ---- capacity and legal mixes (rules 5-6) ----
    excess = []
    for loc, cap in inst.supply.items():
        here = [a for a in aids if loc in span[a]]
        for w in weeks:
            used = []
            for g in groups:
                u = m.new_bool_var(f"u_{loc}_{w}_{g}")
                used.append(u)
                members = [at(a, w, g) for a in here]
                if members:
                    m.add_max_equality(u, members)
                else:
                    m.add(u == 0)
                    continue
                total = sum(members)
                exclusive = [at(a, w, g) for a in here if _role(inst, a)["exclusive"]]
                hosts = [at(a, w, g) for a in here if _role(inst, a)["hosts"]]
                m.add(total <= rules.MAX_CO_SHARE)
                if hosts:
                    m.add(sum(hosts) <= 1)
                if exclusive:
                    # An exclusive possession is taken alone: at most one of
                    # them, and nothing else beside it.
                    m.add(sum(exclusive) <= 1)
                    m.add(total <= 1 + (rules.MAX_CO_SHARE - 1) * (1 - sum(exclusive)))
            over = m.new_int_var(0, len(groups), f"over_{loc}_{w}")
            m.add(over >= sum(used) - cap)
            allowance = policy.max_excess_per_location_week
            if allowance is not None:
                m.add(over <= allowance)
            excess.append(over)

    # ---- buffers and closures (rule 4) ----
    for i, a in enumerate(aids):
        for b in aids[i + 1:]:
            # Every occupied span is a closure, even for buffer-free work.
            # Coincident work must share a legal possession in A, B and C.
            if not foot[a] & foot[b]:
                continue
            for w in weeks:
                if not span[a] & span[b]:
                    # No shared location, so they can never be one possession.
                    m.add(active[a, w] + active[b, w] <= 1)
                else:
                    for g in groups:
                        for h in groups:
                            if g != h:
                                m.add(at(a, w, g) + at(b, w, h) <= 1)

    # ---- Scenario C's per-line ECLO window (rule 10) ----
    if policy.allow_eclo and policy.eclo_window_weeks:
        width = policy.eclo_window_weeks
        window = {(line, start): m.new_bool_var(f"window_{line}_{start}")
                  for line in inst.lines for start in weeks}
        for line in inst.lines:
            m.add(sum(window[line, start] for start in weeks) <= 1)
        for a in aids:
            for line in inst.affected_lines(inst.activities[a]):
                for w in weeks:
                    covering = [window[line, start]
                                for start in range(w - width + 1, w + 1)
                                if 1 <= start <= horizon]
                    for g in groups:
                        m.add(eclo[a, w, g] <= sum(covering))

    # ---- objective terms ----
    penalties, finish_vars = [], {}
    for a in aids:
        fin = m.new_int_var(1, horizon, f"finish_{a}")
        finish_vars[a] = fin
        # Exact completion dates keep even time-limited feasible incumbents'
        # objective values aligned with their exported access rows.
        m.add_max_equality(fin, [w * active[a, w] for w in weeks])

    contract_finish = {}
    if policy.score_overrun:
        for c in inst.contracts.values():
            members = [a for a in aids if inst.activities[a].contract_number == c.contract_number]
            fin = m.new_int_var(1, horizon, f"finish_contract_{c.contract_number}")
            contract_finish[c.contract_number] = fin
            m.add_max_equality(fin, [finish_vars[a] for a in members])
            # Each activity pays only for its own delay. Contract completion
            # remains useful reporting data but cannot penalise on-time siblings.
            for a in members:
                weight = round(SCALE * rules.CONTRACT_WEIGHT[c.contract_priority]
                               * (1 + rules.ACTIVITY_NUDGE[inst.activities[a].activity_priority]))
                values = [0] + [weight * max(
                    0, (inst.week_end(w) - c.planned_completion_date).days) for w in weeks]
                p = m.new_int_var(0, max(values), f"penalty_activity_{a}")
                m.add_element(finish_vars[a], values, p)
                penalties.append(p)

    primary = sum(penalties)
    if policy.allow_eclo:
        primary += SCALE * rules.ECLO_NIGHT_WEIGHT * sum(eclo.values())
    primary += SCALE * rules.EXCESS_ACCESS_NIGHT_WEIGHT * sum(excess)

    weights = {aid: max(1, round(1_000 * calculate_priority(
        inst, aid, policy.name, as_of_date=as_of_date).score)) for aid in inst.activities}
    secondary = sum(weights[a] * finish_vars[a] for a in aids)

    return m, primary, secondary, dict(
        aids=aids, weeks=weeks, groups=groups, active=active,
        normal=normal, eclo=eclo, night=night, allow_eclo=policy.allow_eclo,
        finish_vars=finish_vars, contract_finish=contract_finish)


def _solve_lexicographic(m, primary, secondary, policy, primary_seconds, secondary_seconds):
    """Prove the local objective, lock it, then optimise the tie-break."""
    m.minimize(primary)
    first = cp_model.CpSolver()
    first.parameters.num_search_workers = SEARCH_WORKERS
    if primary_seconds:
        first.parameters.max_time_in_seconds = primary_seconds
    status = first.solve(m)
    if status not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        raise ExactSolveFailed(
            f"Scenario {policy.name}: {first.status_name(status)}", status)
    proven = status == cp_model.OPTIMAL
    primary_value = round(first.objective_value)
    best_bound = first.best_objective_bound
    primary_wall_seconds = first.wall_time

    # Without a proof, locking the objective could exclude better schedules.
    # Report the tie-break value of the schedule actually being returned.
    if not proven:
        return (first, float(primary_value), float(first.value(secondary)), False,
                False, best_bound, primary_wall_seconds)

    m.add(primary == primary_value)
    # Seed the tie-break solve with the proven schedule; possession and night
    # symmetry makes the lexicographic pass impractical otherwise.
    for index in range(len(m.proto.variables)):
        variable = m.get_int_var_from_proto_index(index)
        m.add_hint(variable, first.value(variable))
    m.minimize(secondary)
    second = cp_model.CpSolver()
    second.parameters.num_search_workers = SEARCH_WORKERS
    second.parameters.max_time_in_seconds = secondary_seconds
    status = second.solve(m)
    if status not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        # The tie-break pass ran out of budget. The proven schedule still
        # stands; report its own tie-break value rather than a nominal zero.
        return (first, float(primary_value), float(first.value(secondary)), True,
                False, best_bound, primary_wall_seconds)
    return (second, float(primary_value), second.objective_value, True,
            status == cp_model.OPTIMAL, best_bound, primary_wall_seconds)


def _extract(inst: Instance, s, parts) -> dict:
    placements, group_of, seq, finish = [], {}, defaultdict(int), {}
    for w in parts["weeks"]:
        for a in parts["aids"]:
            if not s.value(parts["active"][a, w]):
                continue
            g = next(g for g in parts["groups"]
                     if s.value(parts["normal"][a, w, g])
                     or (parts["allow_eclo"] and s.value(parts["eclo"][a, w, g])))
            is_eclo = int(parts["allow_eclo"] and s.value(parts["eclo"][a, w, g]))
            c = inst.contracts[inst.activities[a].contract_number]
            n = next(n for n in range(1, c.max_access_per_week + 1)
                     if s.value(parts["night"][a, w, n]))
            seq[a] += 1
            group_of[a, w] = g
            placements.append((a, seq[a], w, is_eclo, n))
            finish[a] = w
    return {"placements": placements, "group_of": group_of, "finish_week": finish}


def solve_exact(inst: Instance, scenario: str, *, as_of_date: dt.date | None = None,
                primary_seconds: float | None = None,
                secondary_seconds: float = 30.0,
                schedule_constraints: dict | None = None) -> dict:
    """Solve one scenario exactly, growing the horizon if the workload needs it."""
    policy = POLICIES[scenario.upper()]
    as_of_date = as_of_date or _default_as_of(inst)
    protected_horizon = max((week for _, week in
                             (schedule_constraints or {}).get('fixed', {})), default=0)
    horizon = max(inst.horizon_weeks, protected_horizon)
    if policy.hard_planned_date:
        # A short declared horizon must not exclude on-time choices that fall
        # before a contract's actual deadline. No feasible B access can occur
        # after the latest such week, so this also bounds the global proof.
        horizon = max(horizon, max((
            (c.planned_completion_date - inst.horizon_start).days + 1) // 7
            for c in inst.contracts.values()))
    last: ExactSolveFailed | None = None

    for attempt in range(MAX_HORIZON_ATTEMPTS):
        m, primary, secondary, parts = _build(
            inst, policy, horizon, as_of_date, schedule_constraints, compact=True)
        try:
            s, value, tie, proven, tie_proven, best_bound, primary_wall_seconds = _solve_lexicographic(
                m, primary, secondary, policy, primary_seconds, secondary_seconds)
        except ExactSolveFailed as exc:
            last = exc
            if not policy.may_extend_horizon or attempt == MAX_HORIZON_ATTEMPTS - 1:
                raise
            # The workload may simply not fit in the weeks declared. The proof
            # applies to the returned modelling horizon: allowing more weeks can
            # in general let cheap work move later and free expensive work sooner.
            horizon = max(horizon + 1, int(horizon * HORIZON_GROWTH))
            continue
        result = _extract(inst, s, parts)
        result.update(objective=value, priority_objective=tie, proven=proven,
                      priority_proven=tie_proven, horizon_weeks=horizon,
                      extended=horizon != inst.horizon_weeks, best_bound=best_bound,
                      primary_wall_seconds=primary_wall_seconds,
                      global_proven=proven and not policy.score_overrun,
                      certificate_horizon_weeks=horizon)
        # Feasibility at the declared horizon is not a global certificate:
        # cheap work may move later and release scarce early capacity for an
        # expensive contract. Search the finite horizon that contains every
        # schedule capable of strictly beating this proven incumbent.
        if policy.score_overrun and proven:
            certificate_horizon = _strict_improvement_horizon(inst, round(value))
            result['certificate_horizon_weeks'] = certificate_horizon
            result['global_proven'] = certificate_horizon <= horizon
            if certificate_horizon > horizon:
                # The incumbent is already extracted. Do not retain the first
                # model/solver while allocating the larger certificate model.
                del s, m, primary, secondary, parts
                gc.collect()
                cm, cp, cs, cparts = _build(
                    inst, policy, certificate_horizon, as_of_date,
                    schedule_constraints, compact=True)
                cm.add(cp <= round(value) - 1)
                try:
                    certified = _solve_lexicographic(
                        cm, cp, cs, policy, None, secondary_seconds)
                except ExactSolveFailed as exc:
                    if exc.status != cp_model.INFEASIBLE:
                        raise
                    result['global_proven'] = True
                else:
                    (cert_solver, cert_value, cert_tie, cert_proven,
                     cert_tie_proven, cert_bound, cert_seconds) = certified
                    result = _extract(inst, cert_solver, cparts)
                    result.update(
                        objective=cert_value, priority_objective=cert_tie,
                        proven=cert_proven, global_proven=cert_proven,
                        priority_proven=cert_tie_proven,
                        horizon_weeks=certificate_horizon,
                        certificate_horizon_weeks=certificate_horizon,
                        extended=certificate_horizon != inst.horizon_weeks,
                        best_bound=cert_bound, primary_wall_seconds=cert_seconds,
                    )
        return result
    raise last or ExactSolveFailed(f"Scenario {scenario}: no schedule found")


def solve_scenario_a(inst: Instance, as_of_date: dt.date | None = None) -> dict:
    return solve_exact(inst, "A", as_of_date=as_of_date)


def solve_scenario_b(inst: Instance, as_of_date: dt.date | None = None) -> dict:
    return solve_exact(inst, "B", as_of_date=as_of_date)


def solve_scenario_c(inst: Instance, as_of_date: dt.date | None = None) -> dict:
    return solve_exact(inst, "C", as_of_date=as_of_date)

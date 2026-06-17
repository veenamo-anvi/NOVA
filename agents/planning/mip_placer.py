"""MIP-based optimal base-station placement (Almoghathawi et al., 2024).

Minimise total network cost  Σ_j Σ_t (c_jt·z_jt + r_jt·y_jt)
  z_jt = build site j in period t (one-time CAPEX)
  y_jt = site j active in period t (per-period OPEX)
  x_ijt = demand cluster i assigned to site j in period t

s.t. (2) single-build, (4) activation, (5) unique assignment, (6) implies-active,
     (7) capacity, (8) SINR QoS (enforced by precomputed feasibility from the
     COST-231-Walfisch-Ikegami link budget). Multi-period supports Case A
     (permanent/expanding) and Case B (temporary/shifting) demand.

Solver: CBC via pulp. Returns None on timeout/infeasibility so the caller can
fall back to heuristic placement.
"""
import logging

import pulp

from candidates import DEMAND_CLUSTERS, site_catalog
from propagation import sinr_at_distance_db, haversine_m

log = logging.getLogger("planning.mip")

# Representative 5G n78 sector used for the per-site SINR feasibility check.
_REP_CELL = {"freq_mhz": 3500, "tx_power_w": 200.0, "antenna_config": "64T64R", "band": "n78"}


def _feasible(site: dict, cluster: dict, sinr_min_db: float) -> bool:
    d_km = haversine_m(site["lat"], site["lon"], cluster["lat"], cluster["lon"]) / 1000.0
    return sinr_at_distance_db(_REP_CELL, d_km) >= sinr_min_db


def solve(demand_periods: list[dict], *, sinr_min_db: float = 10.0,
          budget: float = 0.0, time_limit_sec: int = 120,
          demand_mode: str = "permanent") -> dict | None:
    """Solve the placement MIP. demand_periods[t] = {cluster_id: demand}."""
    sites = site_catalog()
    clusters = {dc["id"]: dc for dc in DEMAND_CLUSTERS}
    T = range(len(demand_periods))
    J = list(sites)

    feas = {(j, i): _feasible(sites[j], clusters[i], sinr_min_db)
            for j in J for i in clusters}

    prob = pulp.LpProblem("bs_placement", pulp.LpMinimize)
    z = {(j, t): pulp.LpVariable(f"z_{j}_{t}", cat="Binary") for j in J for t in T}
    y = {(j, t): pulp.LpVariable(f"y_{j}_{t}", cat="Binary") for j in J for t in T}
    x = {}
    for t in T:
        for i in demand_periods[t]:
            if demand_periods[t][i] <= 0:
                continue
            for j in J:
                if feas[(j, i)]:
                    x[(i, j, t)] = pulp.LpVariable(f"x_{i}_{j}_{t}", cat="Binary")

    # Objective
    prob += pulp.lpSum(sites[j]["install_cost"] * z[(j, t)]
                       + sites[j]["op_cost"] * y[(j, t)] for j in J for t in T)

    # (2) single-build
    for j in J:
        prob += pulp.lpSum(z[(j, t)] for t in T) <= 1
    # (4) activation: active only after built (this or earlier period)
    for j in J:
        for t in T:
            prob += y[(j, t)] <= pulp.lpSum(z[(j, tp)] for tp in T if tp <= t)
    # (5) unique assignment for every demand cluster active in period t
    for t in T:
        for i, d in demand_periods[t].items():
            if d <= 0:
                continue
            serving = [x[(i, j, t)] for j in J if (i, j, t) in x]
            if not serving:
                log.warning("cluster %s has no feasible site in period %d", i, t)
                return None
            prob += pulp.lpSum(serving) == 1
    # (6) implies-active
    for (i, j, t) in x:
        prob += x[(i, j, t)] <= y[(j, t)]
    # (7) capacity
    for j in J:
        for t in T:
            terms = [demand_periods[t][i] * x[(i, j, t)]
                     for i in demand_periods[t] if (i, j, t) in x]
            if terms:
                prob += pulp.lpSum(terms) <= sites[j]["capacity"] * y[(j, t)]
    # optional budget envelope
    if budget and budget > 0:
        prob += pulp.lpSum(sites[j]["install_cost"] * z[(j, t)]
                           + sites[j]["op_cost"] * y[(j, t)]
                           for j in J for t in T) <= budget

    status = prob.solve(pulp.PULP_CBC_CMD(timeLimit=time_limit_sec, msg=0))
    if pulp.LpStatus[status] not in ("Optimal",):
        log.warning("MIP status=%s — falling back", pulp.LpStatus[status])
        return None

    selected_by_period = {}
    for t in T:
        selected_by_period[t] = sorted(j for j in J if y[(j, t)].value() and y[(j, t)].value() > 0.5)
    build_schedule = [{"period": t, "site": j, "install_cost": sites[j]["install_cost"]}
                      for j in J for t in T if z[(j, t)].value() and z[(j, t)].value() > 0.5]
    period_assignments = {}
    for t in T:
        period_assignments[t] = {i: j for (i, j, tt) in x
                                 if tt == t and x[(i, j, tt)].value() and x[(i, j, tt)].value() > 0.5}

    install_total = sum(s["install_cost"] for s in
                        ({"install_cost": sites[b["site"]]["install_cost"]} for b in build_schedule))
    op_total = sum(sites[j]["op_cost"] for t in T for j in selected_by_period[t])

    # final-period selected sites drive single-plan cell construction
    final_sites = selected_by_period[max(T)] if len(demand_periods) else []

    return {
        "selected_sites_by_period": selected_by_period,
        "final_sites": final_sites,
        "build_schedule": build_schedule,
        "period_assignments": period_assignments,
        "cost_estimate": {
            "total": round(pulp.value(prob.objective), 2),
            "install_total": install_total,
            "op_total": op_total,
        },
        "demand_mode": demand_mode,
        "status": "Optimal",
    }

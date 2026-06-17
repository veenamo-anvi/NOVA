"""Planning pipeline: candidates -> placement -> PCI -> DU/CU -> slices -> topology.

`generate_plan` runs the full 10-step pipeline and stores the result; multi-period
planning wraps the MIP placer over several demand periods. Plans are kept in
memory keyed by plan_id until applied.
"""
import logging
import math
import uuid

import mip_placer
import pci_planner
import slice_allocator
from candidates import build_candidate_cells, site_catalog, DEMAND_PERIODS, DEMAND_CLUSTERS
from placement import select_cells
from propagation import haversine_m

log = logging.getLogger("planning.planner")

FIBER_LIGHT_SPEED_MPS = 2.0e8          # ~0.67c in fibre
DEFAULT_TARGET_CAPACITY = 16500        # reproduces the 30-cell Malleswaram layout
MAX_CELLS_PER_DU = 12
MAX_DUS_PER_CU = 4

_plans: dict[str, dict] = {}


def _cells_by_site() -> dict[str, list[dict]]:
    out: dict[str, list[dict]] = {}
    for c in build_candidate_cells():
        out.setdefault(c["site"], []).append(c)
    return out


def _expand_sites(site_codes: list[str]) -> list[dict]:
    by_site = _cells_by_site()
    cells: list[dict] = []
    for code in site_codes:
        cells.extend([dict(c) for c in by_site.get(code, [])])
    return cells


def _centroid(items: list[dict]) -> tuple[float, float]:
    if not items:
        return (0.0, 0.0)
    return (sum(i["lat"] for i in items) / len(items),
            sum(i["lon"] for i in items) / len(items))


def assign_dus(cells: list[dict], max_cells: int = MAX_CELLS_PER_DU) -> dict:
    """Group cells into DUs by geographic proximity (site-coherent chunks)."""
    by_site: dict[str, list[dict]] = {}
    for c in cells:
        by_site.setdefault(c["site"], []).append(c)
    # order sites spatially so chunks stay geographically local
    sites = sorted(by_site, key=lambda s: (round(by_site[s][0]["lat"], 2), by_site[s][0]["lon"]))

    dus: dict[str, dict] = {}
    du_idx, cur, count = 1, [], 0
    for s in sites:
        sc = by_site[s]
        if count + len(sc) > max_cells and cur:
            du_id = f"DU-MLS-{du_idx}"
            dus[du_id] = {"du_id": du_id, "cells": [c["cell_id"] for c in cur]}
            du_idx += 1
            cur, count = [], 0
        cur.extend(sc)
        count += len(sc)
    if cur:
        du_id = f"DU-MLS-{du_idx}"
        dus[du_id] = {"du_id": du_id, "cells": [c["cell_id"] for c in cur]}

    # stamp du_id onto cells + compute centroids
    cid_to_du = {cid: du for du, d in dus.items() for cid in d["cells"]}
    for c in cells:
        c["du_id"] = cid_to_du[c["cell_id"]]
    for du, d in dus.items():
        members = [c for c in cells if c["cell_id"] in d["cells"]]
        d["lat"], d["lon"] = _centroid(members)
        d["max_cells"] = max_cells
        d["cell_count"] = len(members)
    return dus


def assign_cus(dus: dict, max_dus: int = MAX_DUS_PER_CU) -> dict:
    """Group DUs into CUs by proximity."""
    order = sorted(dus, key=lambda d: (round(dus[d]["lat"], 2), dus[d]["lon"]))
    cus: dict[str, dict] = {}
    cu_idx, cur = 1, []
    for d in order:
        if len(cur) >= max_dus:
            cu_id = "CU-MLS" if cu_idx == 1 else f"CU-MLS-{cu_idx}"
            cus[cu_id] = {"cu_id": cu_id, "dus": list(cur)}
            cu_idx += 1
            cur = []
        cur.append(d)
    if cur:
        cu_id = "CU-MLS" if cu_idx == 1 else f"CU-MLS-{cu_idx}"
        cus[cu_id] = {"cu_id": cu_id, "dus": list(cur)}

    du_to_cu = {d: cu for cu, c in cus.items() for d in c["dus"]}
    for d, meta in dus.items():
        meta["cu_id"] = du_to_cu[d]
    for cu, c in cus.items():
        members = [dus[d] for d in c["dus"]]
        c["lat"], c["lon"] = _centroid(members)
        c["du_count"] = len(members)
    return cus


def timing_sync(dus: dict, cus: dict) -> dict:
    """Per DU->CU fronthaul propagation delay estimate (microseconds)."""
    out = {}
    for du_id, du in dus.items():
        cu = cus[du["cu_id"]]
        d_m = haversine_m(du["lat"], du["lon"], cu["lat"], cu["lon"])
        prop_us = (d_m / FIBER_LIGHT_SPEED_MPS) * 1e6
        out[du_id] = {"cu_id": du["cu_id"], "distance_m": round(d_m, 1),
                      "propagation_us": round(prop_us, 3),
                      "within_budget": prop_us <= 100.0}
    return out


def fronthaul_routing(dus: dict, cus: dict) -> dict:
    """Distance-based midhaul latency estimate per DU->CU link."""
    out = {}
    for du_id, du in dus.items():
        cu = cus[du["cu_id"]]
        d_km = haversine_m(du["lat"], du["lon"], cu["lat"], cu["lon"]) / 1000.0
        # propagation + per-km transport/processing overhead
        latency_ms = round(d_km / (FIBER_LIGHT_SPEED_MPS / 1000.0) * 1000 + d_km * 0.05, 4)
        out[du_id] = {"cu_id": du["cu_id"], "distance_km": round(d_km, 3),
                      "midhaul_latency_ms": latency_ms}
    return out


def plan_to_topology(cells: list[dict], dus: dict, cus: dict) -> dict:
    """Convert the plan to topology.json format, preserving all hardware fields."""
    cu_meta = {cu_id: {"cu_id": cu_id, "area": "Malleswaram",
                       "lat": round(c["lat"], 6), "lon": round(c["lon"], 6)}
               for cu_id, c in cus.items()}
    du_meta = {du_id: {"du_id": du_id, "cu_id": d["cu_id"], "area": "planned",
                       "lat": round(d["lat"], 6), "lon": round(d["lon"], 6),
                       "max_cells": d["max_cells"]}
               for du_id, d in dus.items()}
    cell_meta = {}
    for c in cells:
        cu_id = dus[c["du_id"]]["cu_id"]
        cell_meta[c["cell_id"]] = {
            "cell_id": c["cell_id"], "du_id": c["du_id"], "cu_id": cu_id,
            "area": c["area"], "lat": c["lat"], "lon": c["lon"],
            "generation": c["generation"], "band": c["band"], "freq_mhz": c["freq_mhz"],
            "vendor": c["vendor"], "hardware_model": c["hardware_model"],
            "antenna_config": c["antenna_config"], "pci": c["pci"],
            "tx_power_w": c["tx_power_w"], "idle_power_w": c["idle_power_w"],
            "peak_dl_mbps": c["peak_dl_mbps"], "max_ues": c["max_ues"],
        }
    return {
        "metadata": {"deployment": "Malleswaram (planned)",
                     "cell_count": len(cell_meta),
                     "cell_level_max_ues": sum(c["max_ues"] for c in cells)},
        "cus": cu_meta, "dus": du_meta, "cells": cell_meta,
    }


def _cost_estimate(site_codes: list[str]) -> dict:
    cat = site_catalog()
    install = [{"site": s, "cost": cat[s]["install_cost"]} for s in site_codes if s in cat]
    op = [{"site": s, "cost": cat[s]["op_cost"]} for s in site_codes if s in cat]
    return {"total": sum(i["cost"] for i in install) + sum(o["cost"] for o in op),
            "install_costs": install, "op_costs": op}


def generate_plan(params: dict) -> dict:
    """Full pipeline. Returns the stored plan dict."""
    use_mip = bool(params.get("use_mip", False))
    sinr_min = float(params.get("sinr_min_db", 10.0))
    budget = float(params.get("deployment_budget", 0) or 0)
    mip_used = False
    mip_extra = {}

    if use_mip:
        demand = {dc["id"]: dc["demand"] for dc in DEMAND_CLUSTERS}
        res = mip_placer.solve([demand], sinr_min_db=sinr_min, budget=budget,
                               time_limit_sec=int(params.get("mip_time_limit_sec", 120)))
        if res:
            mip_used = True
            site_codes = res["final_sites"]
            mip_extra = {"build_schedule": res["build_schedule"],
                         "period_assignments": res["period_assignments"]}
            cells = _expand_sites(site_codes)
        else:
            log.warning("MIP failed; using heuristic placement")
            cells = select_cells(int(params.get("target_active_ues", DEFAULT_TARGET_CAPACITY)))
            site_codes = sorted({c["site"] for c in cells})
    else:
        cells = select_cells(int(params.get("target_active_ues", DEFAULT_TARGET_CAPACITY)))
        site_codes = sorted({c["site"] for c in cells})

    pci_planner.assign_pcis(cells)
    dus = assign_dus(cells)
    cus = assign_cus(dus)
    centroids = {"dus": {d: {"lat": m["lat"], "lon": m["lon"]} for d, m in dus.items()},
                 "cus": {c: {"lat": m["lat"], "lon": m["lon"]} for c, m in cus.items()}}
    timing = timing_sync(dus, cus)
    slices = slice_allocator.allocate_slices(cells, params.get("traffic_profile"))
    fronthaul = fronthaul_routing(dus, cus)
    topology = plan_to_topology(cells, dus, cus)
    pci_check = pci_planner.verify(cells)

    plan_id = str(uuid.uuid4())
    plan = {
        "plan_id": plan_id,
        "geographic_area": params.get("geographic_area", "Malleswaram"),
        "selected_cell_count": len(cells),
        "selected_sites": site_codes,
        "mip_used": mip_used,
        "cells": list(topology["cells"].values()),
        "dus": topology["dus"], "cus": topology["cus"],
        "centroids": centroids,
        "timing_sync": timing,
        "fronthaul_routing": fronthaul,
        "slice_allocations": slices,
        "pci_check": pci_check,
        "cost_estimate": _cost_estimate(site_codes),
        "_topology": topology,   # used by /plan/apply
        **mip_extra,
    }
    _plans[plan_id] = plan
    log.info("plan %s: %d cells over %d sites (mip=%s, pci ok=%s)",
             plan_id[:8], len(cells), len(site_codes), mip_used,
             pci_check["collisions"] == 0 and pci_check["confusions"] == 0)
    return plan


def generate_multi_period(params: dict) -> dict:
    """Multi-period MIP plan (Case A permanent / Case B temporary)."""
    mode = params.get("demand_mode", "permanent")
    periods = params.get("time_periods") or DEMAND_PERIODS.get(mode, DEMAND_PERIODS["permanent"])
    res = mip_placer.solve(periods, sinr_min_db=float(params.get("sinr_min_db", 10.0)),
                           budget=float(params.get("deployment_budget", 0) or 0),
                           time_limit_sec=int(params.get("mip_time_limit_sec", 120)),
                           demand_mode=mode)
    if not res:
        raise ValueError("multi-period MIP infeasible or timed out")

    site_codes = res["final_sites"]
    cells = _expand_sites(site_codes)
    pci_planner.assign_pcis(cells)
    dus = assign_dus(cells)
    cus = assign_cus(dus)
    slices = slice_allocator.allocate_slices(cells, params.get("traffic_profile"))
    topology = plan_to_topology(cells, dus, cus)

    plan_id = str(uuid.uuid4())
    plan = {
        "plan_id": plan_id, "demand_mode": mode,
        "n_periods": len(periods),
        "selected_cell_count": len(cells),
        "selected_sites": site_codes,
        "mip_used": True,
        "cells": list(topology["cells"].values()),
        "dus": topology["dus"], "cus": topology["cus"],
        "slice_allocations": slices,
        "build_schedule": res["build_schedule"],
        "period_assignments": res["period_assignments"],
        "selected_sites_by_period": res["selected_sites_by_period"],
        "cost_estimate": res["cost_estimate"],
        "_topology": topology,
    }
    _plans[plan_id] = plan
    log.info("multi-period plan %s (%s): %d periods, %d final cells",
             plan_id[:8], mode, len(periods), len(cells))
    return plan


def get_plan(plan_id: str) -> dict | None:
    return _plans.get(plan_id)

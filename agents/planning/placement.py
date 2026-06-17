"""Heuristic cell placement: density-weighted candidate scoring.

Greedily selects candidate *sites* (each = 3 sectors) to satisfy the target
capacity, scoring each site by the demand it can serve discounted by distance
(Haversine) — closer-to-unserved-demand sites win. Returns the selected
candidate cells.
"""
from candidates import DEMAND_CLUSTERS, build_candidate_cells, site_catalog
from propagation import haversine_m


def select_cells(target_capacity: int, prefer_core: bool = True) -> list[dict]:
    """Pick sites until cumulative capacity >= target_capacity.

    Score(site) = sum_i demand_i / (1 + dist_km(site, cluster_i))  over still-
    significantly-unserved clusters. Core sites get a small preference so the
    canonical Malleswaram layout is reproduced before expansion sites.
    """
    sites = site_catalog()
    cells_by_site: dict[str, list[dict]] = {}
    for c in build_candidate_cells():
        cells_by_site.setdefault(c["site"], []).append(c)

    remaining = {dc["id"]: float(dc["demand"]) for dc in DEMAND_CLUSTERS}
    clusters = {dc["id"]: dc for dc in DEMAND_CLUSTERS}

    selected: list[str] = []
    capacity = 0
    available = set(sites)

    while capacity < target_capacity and available:
        best_site, best_score = None, -1.0
        for code in available:
            s = sites[code]
            score = 0.0
            for cid, dc in clusters.items():
                if remaining[cid] <= 0:
                    continue
                d_km = haversine_m(s["lat"], s["lon"], dc["lat"], dc["lon"]) / 1000.0
                score += remaining[cid] / (1.0 + d_km)
            if prefer_core and s["core_site"]:
                score *= 1.15
            if score > best_score:
                best_site, best_score = code, score
        if best_site is None:
            break

        selected.append(best_site)
        available.discard(best_site)
        s = sites[best_site]
        capacity += s["capacity"]
        # Deduct this site's capacity from the demand of its nearest clusters.
        order = sorted(clusters.values(),
                       key=lambda dc: haversine_m(s["lat"], s["lon"], dc["lat"], dc["lon"]))
        budget = s["capacity"]
        for dc in order:
            if budget <= 0:
                break
            take = min(remaining[dc["id"]], budget)
            remaining[dc["id"]] -= take
            budget -= take

    out: list[dict] = []
    for code in selected:
        out.extend(cells_by_site[code])
    return out

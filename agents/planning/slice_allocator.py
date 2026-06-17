"""Network-slice PRB budget allocation per cell.

Splits each cell's PRB budget across eMBB / URLLC / mMTC according to the
traffic profile fractions. PRB budget per cell is taken from its band bandwidth
(approximate NR/LTE PRB counts).
"""
PRB_BY_BAND = {"n78": 273, "n41": 106, "B40": 100, "B3": 100}
DEFAULT_PROFILE = {"eMBB": 0.7, "URLLC": 0.2, "mMTC": 0.1}


def allocate_slices(cells: list[dict], traffic_profile: dict | None = None) -> dict:
    """Return {cell_id: {slice: prb}} plus a network-wide rollup."""
    profile = {**DEFAULT_PROFILE, **(traffic_profile or {})}
    total = sum(v for k, v in profile.items() if k in ("eMBB", "URLLC", "mMTC")) or 1.0
    frac = {s: profile.get(s, 0.0) / total for s in ("eMBB", "URLLC", "mMTC")}

    per_cell: dict[str, dict] = {}
    rollup = {"eMBB": 0, "URLLC": 0, "mMTC": 0}
    for c in cells:
        prb = PRB_BY_BAND.get(c["band"], 100)
        alloc = {s: int(round(prb * frac[s])) for s in frac}
        # give rounding remainder to eMBB
        alloc["eMBB"] += prb - sum(alloc.values())
        per_cell[c["cell_id"]] = alloc
        for s in rollup:
            rollup[s] += alloc[s]
    return {"per_cell": per_cell, "network_prb_by_slice": rollup, "fractions": frac}

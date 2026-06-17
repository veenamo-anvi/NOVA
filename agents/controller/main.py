"""Controller — the network's single control plane (FastAPI, :8080).

All topology mutations go through here; the Controller is the only writer of
topology.json. GET routes merge live KPIs from InfluxDB at query time.
"""
import logging
import math

from fastapi import FastAPI, HTTPException

import influx
import topology_store as store
from models import AddCell, MoveCell, MoveDU, TopologyReplace

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s %(levelname)s %(name)s %(message)s")
log = logging.getLogger("controller")

app = FastAPI(title="NOVA Controller", version="1.0.0")


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
def _haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance in metres."""
    r = 6371000.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlmb = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dlmb / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def _cells_with_kpis(topo: dict, predicate=None) -> list[dict]:
    """Merge live KPIs into each cell record, optionally filtered."""
    kpis = influx.latest_cell_kpis()
    out = []
    for cell_id, cell in topo.get("cells", {}).items():
        if predicate and not predicate(cell):
            continue
        merged = dict(cell)
        merged["kpi"] = kpis.get(cell_id, {})
        out.append(merged)
    return out


# --------------------------------------------------------------------------- #
# Read routes
# --------------------------------------------------------------------------- #
@app.get("/health")
def health():
    return {"status": "ok", "service": "controller"}


@app.get("/topology")
def get_topology():
    """Raw topology.json with no KPI merge."""
    return store.load()


@app.get("/network")
def get_network():
    """Full live state: all cells (with KPIs) + DUs + CUs."""
    topo = store.load()
    return {
        "metadata": topo.get("metadata", {}),
        "cus": topo.get("cus", {}),
        "dus": topo.get("dus", {}),
        "cells": _cells_with_kpis(topo),
    }


@app.get("/cells")
def list_cells(area: str | None = None, du_id: str | None = None,
               cu_id: str | None = None):
    """Filtered cell list with live KPIs."""
    topo = store.load()

    def keep(c: dict) -> bool:
        if area and c.get("area") != area:
            return False
        if du_id and c.get("du_id") != du_id:
            return False
        if cu_id and c.get("cu_id") != cu_id:
            return False
        return True

    cells = _cells_with_kpis(topo, keep)
    return {"cells": cells, "total": len(cells)}


@app.get("/cells/{cell_id}")
def get_cell(cell_id: str):
    """Single cell config + 30-minute KPI time series."""
    topo = store.load()
    cell = topo.get("cells", {}).get(cell_id)
    if not cell:
        raise HTTPException(status_code=404, detail=f"cell {cell_id} not found")
    return {**cell, "timeseries": influx.cell_timeseries(cell_id, minutes=30)}


@app.get("/dus")
def list_dus():
    """DU list with aggregated live KPIs (cell counts + summed UEs)."""
    topo = store.load()
    kpis = influx.latest_cell_kpis()
    dus = {}
    for du_id, du in topo.get("dus", {}).items():
        du_cells = [c for c in topo["cells"].values() if c.get("du_id") == du_id]
        dus[du_id] = {
            **du,
            "cell_count": len(du_cells),
            "connected_ues": sum(kpis.get(c["cell_id"], {}).get("connected_ues", 0)
                                 for c in du_cells),
        }
    return {"dus": dus, "total": len(dus)}


@app.get("/cus")
def list_cus():
    """CU list with DU counts."""
    topo = store.load()
    cus = {}
    for cu_id, cu in topo.get("cus", {}).items():
        du_count = sum(1 for d in topo.get("dus", {}).values() if d.get("cu_id") == cu_id)
        cus[cu_id] = {**cu, "du_count": du_count}
    return {"cus": cus, "total": len(cus)}


@app.get("/neighbors/{cell_id}")
def neighbors(cell_id: str, max_neighbors: int = 6):
    """Nearest geographic neighbours by Haversine distance."""
    topo = store.load()
    cell = topo.get("cells", {}).get(cell_id)
    if not cell:
        raise HTTPException(status_code=404, detail=f"cell {cell_id} not found")
    dists = []
    for other_id, other in topo["cells"].items():
        if other_id == cell_id:
            continue
        d = _haversine_m(cell["lat"], cell["lon"], other["lat"], other["lon"])
        dists.append({"cell_id": other_id, "distance_m": round(d, 1),
                      "du_id": other.get("du_id"), "pci": other.get("pci")})
    dists.sort(key=lambda x: x["distance_m"])
    return {"cell_id": cell_id, "neighbors": dists[:max_neighbors]}


# --------------------------------------------------------------------------- #
# Mutation routes (the only writers of topology.json)
# --------------------------------------------------------------------------- #
@app.post("/move/cell")
def move_cell(req: MoveCell):
    with store.lock():
        topo = store.load()
        cell = topo.get("cells", {}).get(req.cell_id)
        if not cell:
            raise HTTPException(404, f"cell {req.cell_id} not found")
        if req.to_du_id not in topo.get("dus", {}):
            raise HTTPException(404, f"DU {req.to_du_id} not found")
        from_du = cell.get("du_id")
        cell["du_id"] = req.to_du_id
        cell["cu_id"] = topo["dus"][req.to_du_id].get("cu_id", cell.get("cu_id"))
        store.save_atomic(topo)
    influx.write_topology_event("CELL_MOVE", cell_id=req.cell_id,
                                from_component=from_du, to_component=req.to_du_id)
    log.info("moved cell %s: %s -> %s", req.cell_id, from_du, req.to_du_id)
    return {"status": "ok", "cell_id": req.cell_id,
            "from_du": from_du, "to_du": req.to_du_id}


@app.post("/move/du")
def move_du(req: MoveDU):
    with store.lock():
        topo = store.load()
        du = topo.get("dus", {}).get(req.du_id)
        if not du:
            raise HTTPException(404, f"DU {req.du_id} not found")
        if req.to_cu_id not in topo.get("cus", {}):
            raise HTTPException(404, f"CU {req.to_cu_id} not found")
        from_cu = du.get("cu_id")
        du["cu_id"] = req.to_cu_id
        # cascade cu_id onto the DU's cells
        for c in topo["cells"].values():
            if c.get("du_id") == req.du_id:
                c["cu_id"] = req.to_cu_id
        store.save_atomic(topo)
    influx.write_topology_event("DU_MOVE", du_id=req.du_id,
                                from_component=from_cu, to_component=req.to_cu_id)
    log.info("moved DU %s: %s -> %s", req.du_id, from_cu, req.to_cu_id)
    return {"status": "ok", "du_id": req.du_id,
            "from_cu": from_cu, "to_cu": req.to_cu_id}


@app.post("/topology/replace")
def replace_topology(req: TopologyReplace):
    """Full topology swap (used by plan/apply). Validates structure first."""
    if not req.cells:
        raise HTTPException(400, "cells must not be empty")
    for cell_id, cell in req.cells.items():
        du_id = cell.get("du_id")
        if du_id and du_id not in req.dus:
            raise HTTPException(400, f"cell {cell_id} references unknown DU {du_id}")
    for du_id, du in req.dus.items():
        cu_id = du.get("cu_id")
        if cu_id and cu_id not in req.cus:
            raise HTTPException(400, f"DU {du_id} references unknown CU {cu_id}")
    new_topo = {"metadata": req.metadata or {}, "cus": req.cus,
                "dus": req.dus, "cells": req.cells}
    store.recompute_metadata(new_topo)
    with store.lock():
        store.save_atomic(new_topo)
    influx.write_topology_event("TOPOLOGY_REPLACE")
    log.info("topology replaced: %d cells", len(req.cells))
    return {"status": "ok", "cell_count": len(req.cells),
            "du_count": len(req.dus), "cu_count": len(req.cus)}


@app.post("/cells/add")
def add_cell(req: AddCell):
    with store.lock():
        topo = store.load()
        cells = topo.setdefault("cells", {})
        if req.cell_id in cells:
            raise HTTPException(409, f"cell {req.cell_id} already exists")
        if req.du_id not in topo.get("dus", {}):
            raise HTTPException(404, f"DU {req.du_id} not found")

        pci = req.pci
        if pci == 0:
            used = {c.get("pci") for c in cells.values()}
            pci = next(i for i in range(1, 1008) if i not in used)

        cu_id = req.cu_id or topo["dus"][req.du_id].get("cu_id", "")
        cells[req.cell_id] = {
            "cell_id": req.cell_id, "du_id": req.du_id, "cu_id": cu_id,
            "area": req.area, "lat": req.lat, "lon": req.lon,
            "generation": req.generation, "band": req.band, "freq_mhz": req.freq_mhz,
            "vendor": req.vendor, "hardware_model": req.hardware_model,
            "antenna_config": req.antenna_config, "pci": pci,
            "tx_power_w": req.tx_power_w, "idle_power_w": req.idle_power_w,
            "peak_dl_mbps": req.peak_dl_mbps, "max_ues": req.max_ues,
        }
        store.recompute_metadata(topo)
        store.save_atomic(topo)
    influx.write_topology_event("CELL_ADD", cell_id=req.cell_id, to_component=req.du_id)
    log.info("added cell %s on %s (pci=%d)", req.cell_id, req.du_id, pci)
    return {"status": "ok", "cell_id": req.cell_id, "du_id": req.du_id, "pci": pci}


@app.delete("/cells/{cell_id}")
def remove_cell(cell_id: str):
    with store.lock():
        topo = store.load()
        cells = topo.get("cells", {})
        cell = cells.pop(cell_id, None)
        if cell is None:
            raise HTTPException(404, f"cell {cell_id} not found")
        store.recompute_metadata(topo)
        store.save_atomic(topo)
    influx.write_topology_event("CELL_REMOVE", cell_id=cell_id,
                                from_component=cell.get("du_id", ""))
    log.info("removed cell %s", cell_id)
    return {"status": "ok", "cell_id": cell_id, "removed_from_du": cell.get("du_id")}

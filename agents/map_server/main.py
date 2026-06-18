"""Map Server (FastAPI, :8083).

Serves the Leaflet.js live cell map and proxies all chat/history/tools traffic
to the Orchestrator. /api/cells enriches the Controller's network snapshot with
a computed coverage radius per cell.
"""
import logging
import os

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse

from coverage import compute_coverage_radius_m

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s %(levelname)s %(name)s %(message)s")
log = logging.getLogger("map_server")

CONTROLLER_URL = os.environ.get("CONTROLLER_URL", "http://controller:8080")
ORCHESTRATOR_URL = os.environ.get("ORCHESTRATOR_URL", "http://orchestrator:8082")
STATIC_DIR = os.path.join(os.path.dirname(__file__), "static")

app = FastAPI(title="NOVA Map Server", version="1.0.0")


@app.get("/health")
def health():
    return {"status": "ok", "service": "map-server"}


@app.get("/")
def index():
    return FileResponse(os.path.join(STATIC_DIR, "index.html"))


@app.get("/api/cells")
def api_cells():
    try:
        net = httpx.get(f"{CONTROLLER_URL}/network", timeout=10.0).json()
    except Exception as e:  # noqa: BLE001
        return JSONResponse({"error": f"controller unreachable: {e}"}, status_code=503)
    cells = []
    for c in net.get("cells", []):
        radius = compute_coverage_radius_m(
            c.get("band", "B3"), c.get("tx_power_w", 80),
            c.get("generation", "4G"), c.get("antenna_config", "4T4R"),
            c.get("freq_mhz"))
        cells.append({
            "id": c["cell_id"], "area": c.get("area"), "lat": c["lat"], "lon": c["lon"],
            "vendor": c.get("vendor"), "hardware_model": c.get("hardware_model"),
            "generation": c.get("generation"), "band": c.get("band"),
            "pci": c.get("pci"), "du_id": c.get("du_id"), "cu_id": c.get("cu_id"),
            "coverage_radius_m": radius, "kpi": c.get("kpi", {}),
        })
    return {"cells": cells, "total": len(cells)}


# ---- Orchestrator proxy ---------------------------------------------------- #
@app.post("/api/chat")
async def api_chat(request: Request):
    body = await request.body()

    async def stream():
        try:
            async with httpx.AsyncClient(timeout=120.0) as client:
                async with client.stream("POST", f"{ORCHESTRATOR_URL}/chat",
                                         content=body,
                                         headers={"Content-Type": "application/json"}) as r:
                    async for chunk in r.aiter_bytes():
                        yield chunk
        except Exception as e:  # noqa: BLE001
            yield f"\n\n[Error] orchestrator unreachable: {e}".encode()

    return StreamingResponse(stream(), media_type="text/plain")


def _proxy_get(path: str, params=None):
    try:
        r = httpx.get(f"{ORCHESTRATOR_URL}{path}", params=params, timeout=30.0)
        return JSONResponse(r.json(), status_code=r.status_code)
    except Exception as e:  # noqa: BLE001
        return JSONResponse({"error": str(e)}, status_code=503)


@app.get("/api/history")
def api_history(session_id: str = "default"):
    return _proxy_get("/history", {"session_id": session_id})


@app.delete("/api/history")
def api_clear_history(session_id: str = "default"):
    try:
        r = httpx.delete(f"{ORCHESTRATOR_URL}/history",
                         params={"session_id": session_id}, timeout=30.0)
        return JSONResponse(r.json(), status_code=r.status_code)
    except Exception as e:  # noqa: BLE001
        return JSONResponse({"error": str(e)}, status_code=503)


@app.get("/api/tools")
def api_tools():
    return _proxy_get("/tools")


@app.get("/api/orch-health")
def api_orch_health():
    return _proxy_get("/health")

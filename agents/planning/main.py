"""Planning Engine API (FastAPI, :8081).

Generates complete network plans (heuristic or MIP-optimal), stores them in
memory, and applies an accepted plan to the Controller via /topology/replace.
"""
import logging
import os

import httpx
from fastapi import FastAPI, HTTPException

import planner
from candidates import DEMAND_CLUSTERS, DEMAND_PERIODS, build_candidate_cells
from models import ApplyRequest, MultiPeriodRequest, PlanRequest

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s %(levelname)s %(name)s %(message)s")
log = logging.getLogger("planning")

CONTROLLER_URL = os.environ.get("CONTROLLER_URL", "http://controller:8080")

app = FastAPI(title="NOVA Planning Engine", version="1.0.0")


def _public(plan: dict) -> dict:
    """Strip internal keys (prefixed with _) before returning a plan."""
    return {k: v for k, v in plan.items() if not k.startswith("_")}


@app.get("/health")
def health():
    return {"status": "ok", "service": "planning"}


@app.get("/candidates")
def candidates():
    cells = build_candidate_cells()
    return {"candidate_cells": cells, "total": len(cells)}


@app.get("/demand-clusters")
def demand_clusters():
    return {"clusters": DEMAND_CLUSTERS,
            "period_profiles": {k: v for k, v in DEMAND_PERIODS.items()}}


@app.post("/plan")
def create_plan(req: PlanRequest):
    try:
        plan = planner.generate_plan(req.model_dump())
    except Exception as e:  # noqa: BLE001
        log.exception("plan generation failed")
        raise HTTPException(500, f"plan generation failed: {e}")
    return _public(plan)


@app.post("/plan/multi-period")
def create_multi_period(req: MultiPeriodRequest):
    try:
        plan = planner.generate_multi_period(req.model_dump())
    except ValueError as e:
        raise HTTPException(422, str(e))
    except Exception as e:  # noqa: BLE001
        log.exception("multi-period plan failed")
        raise HTTPException(500, f"multi-period plan failed: {e}")
    return _public(plan)


@app.get("/plan/{plan_id}")
def get_plan(plan_id: str):
    plan = planner.get_plan(plan_id)
    if not plan:
        raise HTTPException(404, f"plan {plan_id} not found")
    return _public(plan)


@app.post("/plan/apply")
def apply_plan(req: ApplyRequest):
    plan = planner.get_plan(req.plan_id)
    if not plan:
        raise HTTPException(404, f"plan {req.plan_id} not found")
    topo = plan["_topology"]
    try:
        r = httpx.post(f"{CONTROLLER_URL}/topology/replace", json=topo, timeout=30.0)
        r.raise_for_status()
    except httpx.HTTPError as e:
        log.error("apply failed: %s", e)
        raise HTTPException(502, f"controller /topology/replace failed: {e}")
    log.info("applied plan %s to controller", req.plan_id[:8])
    return {"status": "ok", "plan_id": req.plan_id, "controller_response": r.json()}

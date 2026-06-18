"""The 13 orchestrator tools.

Schemas are authored once in **Anthropic native format** (`name`, `description`,
`input_schema`). The Claude CLI backend uses them as-is; the Gemini backend
translates them at startup. Each tool's Python implementation hits the
Controller / Planning API over HTTP or queries InfluxDB directly.
"""
import os

import httpx
from influxdb_client import InfluxDBClient

CONTROLLER_URL = os.environ.get("CONTROLLER_URL", "http://controller:8080")
PLANNING_URL = os.environ.get("PLANNING_URL", "http://planning-api:8081")
INFLUX_URL = os.environ.get("INFLUX_URL", "http://influxdb:8086")
INFLUX_TOKEN = os.environ.get("INFLUX_TOKEN", "")
INFLUX_ORG = os.environ.get("INFLUX_ORG", "telecom")
INFLUX_BUCKET = os.environ.get("INFLUX_BUCKET", "telecom_metrics")

_HTTP_TIMEOUT = 30.0
_influx: InfluxDBClient | None = None


def _client() -> InfluxDBClient:
    global _influx
    if _influx is None:
        _influx = InfluxDBClient(url=INFLUX_URL, token=INFLUX_TOKEN, org=INFLUX_ORG)
    return _influx


def _get(url: str, params: dict | None = None) -> dict:
    r = httpx.get(url, params=params, timeout=_HTTP_TIMEOUT)
    r.raise_for_status()
    return r.json()


def _post(url: str, body: dict) -> dict:
    r = httpx.post(url, json=body, timeout=_HTTP_TIMEOUT)
    r.raise_for_status()
    return r.json()


# --------------------------------------------------------------------------- #
# Tool implementations
# --------------------------------------------------------------------------- #
def query_network(_args: dict) -> dict:
    return _get(f"{CONTROLLER_URL}/network")


def list_cells(args: dict) -> dict:
    params = {k: args[k] for k in ("area", "du_id", "cu_id") if args.get(k)}
    return _get(f"{CONTROLLER_URL}/cells", params)


def query_cell(args: dict) -> dict:
    return _get(f"{CONTROLLER_URL}/cells/{args['cell_id']}")


def move_cell(args: dict) -> dict:
    return _post(f"{CONTROLLER_URL}/move/cell",
                 {"cell_id": args["cell_id"], "to_du_id": args["to_du_id"]})


def move_du(args: dict) -> dict:
    return _post(f"{CONTROLLER_URL}/move/du",
                 {"du_id": args["du_id"], "to_cu_id": args["to_cu_id"]})


def plan_network(args: dict) -> dict:
    return _post(f"{PLANNING_URL}/plan", args)


def plan_network_multi_period(args: dict) -> dict:
    return _post(f"{PLANNING_URL}/plan/multi-period", args)


def apply_plan(args: dict) -> dict:
    return _post(f"{PLANNING_URL}/plan/apply", {"plan_id": args["plan_id"]})


def add_cell(args: dict) -> dict:
    return _post(f"{CONTROLLER_URL}/cells/add", args)


def remove_cell(args: dict) -> dict:
    r = httpx.delete(f"{CONTROLLER_URL}/cells/{args['cell_id']}", timeout=_HTTP_TIMEOUT)
    r.raise_for_status()
    return r.json()


def get_alerts(args: dict) -> dict:
    minutes = int(args.get("minutes", 60))
    severity = args.get("severity", "")
    sev_filter = f' and r.severity == "{severity}"' if severity else ""
    flux = f'''
from(bucket: "{INFLUX_BUCKET}")
  |> range(start: -{minutes}m)
  |> filter(fn: (r) => r._measurement == "alerts" and r._field == "message"{sev_filter})
  |> sort(columns: ["_time"], desc: true)
  |> limit(n: 50)
'''
    alerts = []
    for table in _client().query_api().query(flux):
        for rec in table.records:
            alerts.append({"time": rec.get_time().isoformat(),
                           "severity": rec.values.get("severity"),
                           "cell_id": rec.values.get("cell_id"),
                           "du_id": rec.values.get("du_id"),
                           "alert_type": rec.values.get("alert_type"),
                           "message": rec.get_value()})
    return {"alerts": alerts, "count": len(alerts), "window_minutes": minutes}


def query_ue(args: dict) -> dict:
    minutes = int(args.get("minutes", 30))
    clauses = ['r._measurement == "ue_usage"']
    if args.get("ue_id"):
        clauses.append(f'r.ue_id == "{args["ue_id"]}"')
    if args.get("cell_id"):
        clauses.append(f'r.cell_id == "{args["cell_id"]}"')
    pred = " and ".join(clauses)
    flux = f'''
from(bucket: "{INFLUX_BUCKET}")
  |> range(start: -{minutes}m)
  |> filter(fn: (r) => {pred})
  |> filter(fn: (r) => r._field == "dl_bytes")
  |> sort(columns: ["_time"], desc: true)
  |> limit(n: 50)
'''
    rows = []
    for table in _client().query_api().query(flux):
        for rec in table.records:
            rows.append({"time": rec.get_time().isoformat(),
                         "ue_id": rec.values.get("ue_id"),
                         "cell_id": rec.values.get("cell_id"),
                         "slice_type": rec.values.get("slice_type"),
                         "dl_bytes": rec.get_value()})
    return {"ue_records": rows, "count": len(rows), "window_minutes": minutes}


def get_son_status(args: dict) -> dict:
    minutes = int(args.get("minutes", 60))
    base = f'''
from(bucket: "{INFLUX_BUCKET}")
  |> range(start: -{minutes}m)
  |> filter(fn: (r) => r._measurement == "son_actions" and r._field == "message")
'''
    counts: dict[str, int] = {}
    recent = []
    for table in _client().query_api().query(base + '|> sort(columns:["_time"], desc:true)'):
        for rec in table.records:
            at = rec.values.get("action_type", "UNKNOWN")
            counts[at] = counts.get(at, 0) + 1
            if len(recent) < 10:
                recent.append({"time": rec.get_time().isoformat(),
                               "action_type": at, "cell_id": rec.values.get("cell_id"),
                               "message": rec.get_value()})
    # active alert severity counts
    sev_flux = f'''
from(bucket: "{INFLUX_BUCKET}")
  |> range(start: -{minutes}m)
  |> filter(fn: (r) => r._measurement == "alerts" and r._field == "ai_confidence")
  |> group(columns: ["severity"])
  |> count()
'''
    severities = {}
    for table in _client().query_api().query(sev_flux):
        for rec in table.records:
            severities[rec.values.get("severity")] = rec.get_value()
    return {"action_counts": counts, "recent_actions": recent,
            "alert_severities": severities, "window_minutes": minutes}


TOOL_MAP = {
    "query_network": query_network,
    "list_cells": list_cells,
    "query_cell": query_cell,
    "move_cell": move_cell,
    "move_du": move_du,
    "plan_network": plan_network,
    "plan_network_multi_period": plan_network_multi_period,
    "apply_plan": apply_plan,
    "get_alerts": get_alerts,
    "query_ue": query_ue,
    "get_son_status": get_son_status,
    "add_cell": add_cell,
    "remove_cell": remove_cell,
}


def execute_tool(name: str, args: dict) -> dict:
    fn = TOOL_MAP.get(name)
    if not fn:
        return {"error": f"unknown tool {name}"}
    try:
        return fn(args or {})
    except httpx.HTTPStatusError as e:
        return {"error": f"{name} HTTP {e.response.status_code}: {e.response.text[:200]}"}
    except Exception as e:  # noqa: BLE001
        return {"error": f"{name} failed: {e}"}


TOOL_SCHEMAS = [
    {"name": "query_network",
     "description": "Get the full network topology and live KPIs for all 30 cells (DUs, CUs, per-cell UEs/PRB/SINR/power).",
     "input_schema": {"type": "object", "properties": {}}},
    {"name": "list_cells",
     "description": "List cells with live KPIs, optionally filtered by area, du_id, or cu_id.",
     "input_schema": {"type": "object", "properties": {
         "area": {"type": "string"}, "du_id": {"type": "string"}, "cu_id": {"type": "string"}}}},
    {"name": "query_cell",
     "description": "Get one cell's config plus its 30-minute KPI time series.",
     "input_schema": {"type": "object", "properties": {
         "cell_id": {"type": "string"}}, "required": ["cell_id"]}},
    {"name": "move_cell",
     "description": "Reassign a cell to a different DU for load balancing.",
     "input_schema": {"type": "object", "properties": {
         "cell_id": {"type": "string"}, "to_du_id": {"type": "string"}},
         "required": ["cell_id", "to_du_id"]}},
    {"name": "move_du",
     "description": "Reassign a DU to a different CU.",
     "input_schema": {"type": "object", "properties": {
         "du_id": {"type": "string"}, "to_cu_id": {"type": "string"}},
         "required": ["du_id", "to_cu_id"]}},
    {"name": "plan_network",
     "description": "Generate a network plan (heuristic or MIP-optimal) with placement, PCI, DU/CU grouping, and slice allocation.",
     "input_schema": {"type": "object", "properties": {
         "geographic_area": {"type": "string"}, "use_mip": {"type": "boolean"},
         "sinr_min_db": {"type": "number"}, "deployment_budget": {"type": "number"},
         "traffic_profile": {"type": "object"}}}},
    {"name": "plan_network_multi_period",
     "description": "Generate a multi-period MIP plan: Case A phased rollout (permanent) or Case B diurnal shift (temporary).",
     "input_schema": {"type": "object", "properties": {
         "demand_mode": {"type": "string", "enum": ["permanent", "temporary"]},
         "deployment_budget": {"type": "number"}, "sinr_min_db": {"type": "number"}}}},
    {"name": "apply_plan",
     "description": "Apply an accepted plan to the live network (pushes topology to the Controller).",
     "input_schema": {"type": "object", "properties": {
         "plan_id": {"type": "string"}}, "required": ["plan_id"]}},
    {"name": "get_alerts",
     "description": "Get recent KPI anomaly alerts, optionally filtered by severity (INFO/WARNING/CRITICAL).",
     "input_schema": {"type": "object", "properties": {
         "minutes": {"type": "integer"}, "severity": {"type": "string"}}}},
    {"name": "query_ue",
     "description": "Get UE-level usage records, filterable by ue_id or cell_id.",
     "input_schema": {"type": "object", "properties": {
         "ue_id": {"type": "string"}, "cell_id": {"type": "string"}, "minutes": {"type": "integer"}}}},
    {"name": "get_son_status",
     "description": "Summarise recent autonomous SON actions (counts by type, last 10 actions) and active alert severities.",
     "input_schema": {"type": "object", "properties": {"minutes": {"type": "integer"}}}},
    {"name": "add_cell",
     "description": "Deploy a new cell. PCI is auto-assigned if not provided (pci=0).",
     "input_schema": {"type": "object", "properties": {
         "cell_id": {"type": "string"}, "du_id": {"type": "string"}, "area": {"type": "string"},
         "lat": {"type": "number"}, "lon": {"type": "number"}, "generation": {"type": "string"},
         "band": {"type": "string"}, "vendor": {"type": "string"}, "freq_mhz": {"type": "integer"},
         "pci": {"type": "integer"}, "max_ues": {"type": "integer"}},
         "required": ["cell_id", "du_id", "area", "lat", "lon", "generation", "band", "vendor", "freq_mhz"]}},
    {"name": "remove_cell",
     "description": "Decommission a cell and remove it from its DU.",
     "input_schema": {"type": "object", "properties": {
         "cell_id": {"type": "string"}}, "required": ["cell_id"]}},
]

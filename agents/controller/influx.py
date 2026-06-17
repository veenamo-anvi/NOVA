"""InfluxDB access for the Controller.

The Controller reads `cell_kpi` to merge live KPIs into topology responses and
writes `topology_event` on every mutation. All reads are best-effort: if
InfluxDB is unreachable or has no data, callers get empty results rather than
errors (topology config is still served).
"""
import os
import logging

from influxdb_client import InfluxDBClient, Point
from influxdb_client.client.write_api import SYNCHRONOUS

log = logging.getLogger("controller.influx")

INFLUX_URL = os.environ.get("INFLUX_URL", "http://influxdb:8086")
INFLUX_TOKEN = os.environ.get("INFLUX_TOKEN", "")
INFLUX_ORG = os.environ.get("INFLUX_ORG", "telecom")
INFLUX_BUCKET = os.environ.get("INFLUX_BUCKET", "telecom_metrics")

# cell_kpi fields surfaced in the nested `kpi` dict on /network and /cells.
KPI_FIELDS = [
    "connected_ues", "dl_throughput_mbps", "ul_throughput_mbps",
    "rsrp_dbm", "rsrq_db", "sinr_db", "power_w",
    "prb_dl_pct", "prb_ul_pct", "packet_loss_pct",
    "cqi", "mcs", "bler_pct", "latency_ms", "jitter_ms", "interference_dbm",
]

_client: InfluxDBClient | None = None


def _get_client() -> InfluxDBClient:
    global _client
    if _client is None:
        _client = InfluxDBClient(url=INFLUX_URL, token=INFLUX_TOKEN, org=INFLUX_ORG)
    return _client


def latest_cell_kpis() -> dict[str, dict]:
    """Return {cell_id: {field: value}} from the last 3 minutes of cell_kpi.

    Best-effort: returns {} on any failure or when no data is present.
    """
    flux = f'''
from(bucket: "{INFLUX_BUCKET}")
  |> range(start: -3m)
  |> filter(fn: (r) => r._measurement == "cell_kpi")
  |> group(columns: ["cell_id", "_field"])
  |> last()
  |> pivot(rowKey: ["cell_id"], columnKey: ["_field"], valueColumn: "_value")
'''
    out: dict[str, dict] = {}
    try:
        tables = _get_client().query_api().query(flux)
        for table in tables:
            for rec in table.records:
                cell_id = rec.values.get("cell_id")
                if not cell_id:
                    continue
                kpi = {f: rec.values.get(f) for f in KPI_FIELDS if rec.values.get(f) is not None}
                out[cell_id] = kpi
    except Exception as e:  # noqa: BLE001 - best-effort merge
        log.warning("latest_cell_kpis failed: %s", e)
    return out


def cell_timeseries(cell_id: str, minutes: int = 30) -> list[dict]:
    """Return a time-ascending list of KPI records for one cell."""
    flux = f'''
from(bucket: "{INFLUX_BUCKET}")
  |> range(start: -{minutes}m)
  |> filter(fn: (r) => r._measurement == "cell_kpi" and r.cell_id == "{cell_id}")
  |> pivot(rowKey: ["_time"], columnKey: ["_field"], valueColumn: "_value")
  |> sort(columns: ["_time"])
'''
    out: list[dict] = []
    try:
        tables = _get_client().query_api().query(flux)
        for table in tables:
            for rec in table.records:
                row = {"time": rec.get_time().isoformat()}
                for f in KPI_FIELDS:
                    if rec.values.get(f) is not None:
                        row[f] = rec.values.get(f)
                out.append(row)
    except Exception as e:  # noqa: BLE001
        log.warning("cell_timeseries(%s) failed: %s", cell_id, e)
    return out


def write_topology_event(event_type: str, *, cell_id: str = "", du_id: str = "",
                         from_component: str = "", to_component: str = "") -> None:
    """Write a topology_event point. Best-effort; never raises to the caller."""
    try:
        p = Point("topology_event").tag("event_type", event_type)
        if cell_id:
            p = p.field("cell_id", cell_id)
        if du_id:
            p = p.field("du_id", du_id)
        if from_component:
            p = p.field("from_component", from_component)
        if to_component:
            p = p.field("to_component", to_component)
        _get_client().write_api(write_options=SYNCHRONOUS).write(
            bucket=INFLUX_BUCKET, record=p)
    except Exception as e:  # noqa: BLE001
        log.warning("write_topology_event(%s) failed: %s", event_type, e)

"""CU simulator — aggregates its DUs' live cell KPIs into cu_kpi.

Reads the latest cell_kpi from InfluxDB for cells under this CU and synthesises
RRC / PDCP / interface-latency metrics. Decoupled from the DU sims: it consumes
what they wrote rather than recomputing load.
"""
import json
import logging
import os
import random
import time
from datetime import datetime, timezone

from influxdb_client import InfluxDBClient, Point
from influxdb_client.client.write_api import SYNCHRONOUS

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s %(levelname)s cu_sim %(message)s")
log = logging.getLogger("cu_sim")

CU_ID = os.environ.get("CU_ID", "CU-MLS")
TOPOLOGY_FILE = os.environ.get("TOPOLOGY_FILE", "/config/topology.json")
EMIT_INTERVAL_SEC = int(os.environ.get("EMIT_INTERVAL_SEC", "10"))

INFLUX_URL = os.environ.get("INFLUX_URL", "http://influxdb:8086")
INFLUX_TOKEN = os.environ.get("INFLUX_TOKEN", "")
INFLUX_ORG = os.environ.get("INFLUX_ORG", "telecom")
INFLUX_BUCKET = os.environ.get("INFLUX_BUCKET", "telecom_metrics")


def connect_influx() -> InfluxDBClient:
    last = None
    for attempt in range(1, 20):
        try:
            c = InfluxDBClient(url=INFLUX_URL, token=INFLUX_TOKEN, org=INFLUX_ORG)
            c.ping()
            log.info("connected to InfluxDB at %s", INFLUX_URL)
            return c
        except Exception as e:  # noqa: BLE001
            last = e
            log.warning("InfluxDB not ready (attempt %d): %s", attempt, e)
            time.sleep(6)
    raise RuntimeError(f"could not connect to InfluxDB: {last}")


def latest_cell_kpis(client: InfluxDBClient) -> dict[str, dict]:
    flux = f'''
from(bucket: "{INFLUX_BUCKET}")
  |> range(start: -2m)
  |> filter(fn: (r) => r._measurement == "cell_kpi")
  |> group(columns: ["cell_id", "_field"])
  |> last()
  |> pivot(rowKey: ["cell_id"], columnKey: ["_field"], valueColumn: "_value")
'''
    out: dict[str, dict] = {}
    try:
        for table in client.query_api().query(flux):
            for rec in table.records:
                cid = rec.values.get("cell_id")
                if cid:
                    out[cid] = rec.values
    except Exception as e:  # noqa: BLE001
        log.warning("query cell_kpi failed: %s", e)
    return out


def topo_view():
    with open(TOPOLOGY_FILE, "r", encoding="utf-8") as f:
        topo = json.load(f)
    dus = [d for d in topo.get("dus", {}).values() if d.get("cu_id") == CU_ID]
    cells = [c for c in topo.get("cells", {}).values() if c.get("cu_id") == CU_ID]
    return dus, cells


def main():
    client = connect_influx()
    write_api = client.write_api(write_options=SYNCHRONOUS)
    log.info("CU %s simulator started", CU_ID)
    while True:
        try:
            dus, cells = topo_view()
        except Exception as e:  # noqa: BLE001
            log.warning("topology read failed: %s", e)
            time.sleep(EMIT_INTERVAL_SEC)
            continue

        kpis = latest_cell_kpis(client)
        connected = sum(int(kpis.get(c["cell_id"], {}).get("connected_ues", 0)) for c in cells)
        pdcp_dl = sum(float(kpis.get(c["cell_id"], {}).get("dl_throughput_mbps", 0)) for c in cells)
        pdcp_ul = sum(float(kpis.get(c["cell_id"], {}).get("ul_throughput_mbps", 0)) for c in cells)
        cap = max(sum(c.get("max_ues", 0) for c in cells), 1)
        util = connected / cap

        now = datetime.now(timezone.utc)
        p = (Point("cu_kpi").tag("cu_id", CU_ID)
             .field("du_count", len(dus))
             .field("rrc_connected", connected)
             .field("rrc_idle", int(connected * random.uniform(0.2, 0.4)))
             .field("rrc_setup_rate", round(connected * random.uniform(0.05, 0.15), 1))
             .field("inter_du_ho_rate", round(util * random.uniform(5, 15), 1))
             .field("pdcp_dl_gbps", round(pdcp_dl / 1000.0, 3))
             .field("pdcp_ul_gbps", round(pdcp_ul / 1000.0, 3))
             .field("f1_latency_ms", round(random.uniform(0.1, 0.5), 3))
             .field("n2_latency_ms", round(random.uniform(1, 4), 2))
             .field("n3_latency_ms", round(random.uniform(1, 5), 2))
             .field("e1_latency_ms", round(random.uniform(0.1, 0.4), 3))
             .field("cpu_pct", round(min(95, 20 + util * 60 + random.gauss(0, 3)), 1))
             .field("memory_pct", round(min(95, 30 + util * 45 + random.gauss(0, 3)), 1))
             .time(now))
        try:
            write_api.write(bucket=INFLUX_BUCKET, record=p)
            log.info("cu_kpi: dus=%d rrc_connected=%d pdcp_dl=%.2fGbps",
                     len(dus), connected, pdcp_dl / 1000.0)
        except Exception as e:  # noqa: BLE001
            log.error("write failed: %s", e)
        time.sleep(EMIT_INTERVAL_SEC)


if __name__ == "__main__":
    main()

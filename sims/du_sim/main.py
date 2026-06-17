"""DU simulator — generates live RAN telemetry for its assigned cells.

Reads topology.json every cycle (picking up Controller moves within one emit
interval), synthesises cell_kpi for each cell whose du_id matches this DU,
aggregates du_kpi, and emits a sample of ue_usage / ue_mobility records.
"""
import json
import logging
import os
import random
import time
from datetime import datetime, timezone

from influxdb_client import InfluxDBClient, Point
from influxdb_client.client.write_api import SYNCHRONOUS

import kpi_model as km

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s %(levelname)s du_sim %(message)s")
log = logging.getLogger("du_sim")

DU_ID = os.environ.get("DU_ID", "DU-MLS-1")
TOPOLOGY_FILE = os.environ.get("TOPOLOGY_FILE", "/config/topology.json")
EMIT_INTERVAL_SEC = int(os.environ.get("EMIT_INTERVAL_SEC", "10"))

INFLUX_URL = os.environ.get("INFLUX_URL", "http://influxdb:8086")
INFLUX_TOKEN = os.environ.get("INFLUX_TOKEN", "")
INFLUX_ORG = os.environ.get("INFLUX_ORG", "telecom")
INFLUX_BUCKET = os.environ.get("INFLUX_BUCKET", "telecom_metrics")

SLICES = (("eMBB", 0.7), ("URLLC", 0.2), ("mMTC", 0.1))


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


def my_cells() -> list[dict]:
    try:
        with open(TOPOLOGY_FILE, "r", encoding="utf-8") as f:
            topo = json.load(f)
    except Exception as e:  # noqa: BLE001
        log.warning("could not read topology: %s", e)
        return []
    return [c for c in topo.get("cells", {}).values() if c.get("du_id") == DU_ID]


def _pick_slice() -> str:
    r = random.random()
    cum = 0.0
    for name, frac in SLICES:
        cum += frac
        if r <= cum:
            return name
    return "eMBB"


def build_points(cells: list[dict], now: datetime) -> list[Point]:
    lf = km.load_factor(now)
    points: list[Point] = []
    total_ues = 0
    cu_id = cells[0].get("cu_id", "") if cells else ""

    for cell in cells:
        kpi = km.generate_cell_kpi(cell, lf)
        total_ues += kpi["connected_ues"]

        p = (Point("cell_kpi")
             .tag("cell_id", cell["cell_id"]).tag("area", cell.get("area", ""))
             .tag("band", cell.get("band", "")).tag("pci", str(cell.get("pci", "")))
             .tag("du_id", DU_ID).tag("cu_id", cell.get("cu_id", ""))
             .tag("vendor", cell.get("vendor", ""))
             .tag("generation", cell.get("generation", "")))
        for field, val in kpi.items():
            p = p.field(field, float(val) if not isinstance(val, int) else val)
        points.append(p.time(now))

        # Sample a couple of UEs per cell for usage/mobility feeds.
        for i in range(2):
            slice_type = _pick_slice()
            ue_id = f"UE-{km._site_of(cell['cell_id'])}-{random.randint(1000, 9999)}"
            scale = {"eMBB": 1.0, "URLLC": 0.2, "mMTC": 0.02}[slice_type]
            points.append(Point("ue_usage")
                          .tag("ue_id", ue_id).tag("cell_id", cell["cell_id"])
                          .tag("slice_type", slice_type)
                          .field("dl_bytes", int(random.uniform(1e5, 5e7) * scale))
                          .field("ul_bytes", int(random.uniform(1e4, 5e6) * scale))
                          .field("latency_ms", round(kpi["latency_ms"] + random.gauss(0, 1), 2))
                          .field("jitter_ms", round(kpi["jitter_ms"], 2))
                          .field("packet_loss", round(kpi["packet_loss_pct"], 3))
                          .time(now))

        # Occasional intra-DU handover event.
        if random.random() < 0.10 and len(cells) > 1:
            target = random.choice([c for c in cells if c["cell_id"] != cell["cell_id"]])
            points.append(Point("ue_mobility")
                          .tag("ue_id", f"UE-{random.randint(1000, 9999)}")
                          .tag("source_cell", cell["cell_id"])
                          .tag("target_cell", target["cell_id"])
                          .tag("event_type", "HANDOVER")
                          .field("rsrp_source", kpi["rsrp_dbm"])
                          .field("rsrp_target", round(kpi["rsrp_dbm"] + random.gauss(2, 2), 1))
                          .field("ho_duration_ms", round(random.uniform(20, 60), 1))
                          .field("velocity_kmh", round(random.uniform(0, 60), 1))
                          .time(now))

    # DU aggregate.
    cap = max(sum(c.get("max_ues", 0) for c in cells), 1)
    util = total_ues / cap
    points.append(Point("du_kpi").tag("du_id", DU_ID).tag("cu_id", cu_id)
                  .field("active_ues", total_ues)
                  .field("cell_count", len(cells))
                  .field("cpu_pct", round(min(95, 15 + util * 70 + random.gauss(0, 3)), 1))
                  .field("memory_pct", round(min(95, 25 + util * 50 + random.gauss(0, 3)), 1))
                  .field("fronthaul_latency_us", round(random.uniform(80, 120), 1))
                  .field("processing_delay_ms", round(0.5 + util * 2 + random.gauss(0, 0.2), 2))
                  .field("f1_msg_per_sec", round(total_ues * random.uniform(1.5, 3.0), 1))
                  .time(now))
    return points


def main():
    client = connect_influx()
    write_api = client.write_api(write_options=SYNCHRONOUS)
    log.info("DU %s simulator started (emit every %ds)", DU_ID, EMIT_INTERVAL_SEC)
    while True:
        cells = my_cells()
        now = datetime.now(timezone.utc)
        if cells:
            try:
                pts = build_points(cells, now)
                write_api.write(bucket=INFLUX_BUCKET, record=pts)
                log.info("emitted %d cells (%d points)", len(cells), len(pts))
            except Exception as e:  # noqa: BLE001
                log.error("write failed: %s", e)
        else:
            log.info("no cells assigned to %s", DU_ID)
        time.sleep(EMIT_INTERVAL_SEC)


if __name__ == "__main__":
    main()

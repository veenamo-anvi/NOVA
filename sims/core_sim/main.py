"""Core simulator — AMF / SMF / UPF aggregate metrics (core_kpi).

Aggregates the whole network's latest cell_kpi into a shared NSA core
(registered UEs, PDU sessions, user-plane throughput) and emits one core_kpi
point per component.
"""
import logging
import os
import random
import time
from datetime import datetime, timezone

from influxdb_client import InfluxDBClient, Point
from influxdb_client.client.write_api import SYNCHRONOUS

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s %(levelname)s core_sim %(message)s")
log = logging.getLogger("core_sim")

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


def network_totals(client: InfluxDBClient) -> tuple[int, float, float]:
    """Return (total_connected_ues, total_dl_mbps, total_ul_mbps)."""
    flux = f'''
from(bucket: "{INFLUX_BUCKET}")
  |> range(start: -2m)
  |> filter(fn: (r) => r._measurement == "cell_kpi" and
       (r._field == "connected_ues" or r._field == "dl_throughput_mbps" or
        r._field == "ul_throughput_mbps"))
  |> group(columns: ["cell_id", "_field"])
  |> last()
  |> group(columns: ["_field"])
  |> sum()
'''
    ues, dl, ul = 0, 0.0, 0.0
    try:
        for table in client.query_api().query(flux):
            for rec in table.records:
                f, v = rec.get_field(), rec.get_value()
                if f == "connected_ues":
                    ues = int(v or 0)
                elif f == "dl_throughput_mbps":
                    dl = float(v or 0)
                elif f == "ul_throughput_mbps":
                    ul = float(v or 0)
    except Exception as e:  # noqa: BLE001
        log.warning("totals query failed: %s", e)
    return ues, dl, ul


def main():
    client = connect_influx()
    write_api = client.write_api(write_options=SYNCHRONOUS)
    log.info("Core simulator started (AMF/SMF/UPF)")
    while True:
        ues, dl_mbps, ul_mbps = network_totals(client)
        now = datetime.now(timezone.utc)
        active_sessions = int(ues * random.uniform(0.85, 0.98))

        amf = (Point("core_kpi").tag("component", "AMF").tag("instance_id", "amf-1")
               .field("registered_ues", ues)
               .field("active_sessions", active_sessions)
               .field("nas_msg_per_sec", round(ues * random.uniform(0.3, 0.8), 1))
               .field("paging_per_sec", round(ues * random.uniform(0.05, 0.2), 1))
               .field("handover_per_sec", round(ues * random.uniform(0.01, 0.05), 1))
               .time(now))
        smf = (Point("core_kpi").tag("component", "SMF").tag("instance_id", "smf-1")
               .field("active_pdu_sessions", active_sessions)
               .field("session_setup_rate", round(ues * random.uniform(0.05, 0.15), 1))
               .field("ip_pool_utilization_pct", round(min(95, ues / 200.0 + random.gauss(0, 2)), 1))
               .time(now))
        upf = (Point("core_kpi").tag("component", "UPF").tag("instance_id", "upf-1")
               .field("dl_throughput_gbps", round(dl_mbps / 1000.0, 3))
               .field("ul_throughput_gbps", round(ul_mbps / 1000.0, 3))
               .field("active_tunnels", active_sessions)
               .field("packet_drop_rate", round(random.uniform(0, 0.5), 4))
               .time(now))
        try:
            write_api.write(bucket=INFLUX_BUCKET, record=[amf, smf, upf])
            log.info("core_kpi: registered_ues=%d dl=%.2fGbps", ues, dl_mbps / 1000.0)
        except Exception as e:  # noqa: BLE001
            log.error("write failed: %s", e)
        time.sleep(EMIT_INTERVAL_SEC)


if __name__ == "__main__":
    main()

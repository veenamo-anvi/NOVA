"""KPI Monitoring Agent — BiLSTM anomaly detection + autonomous SON.

Polls InfluxDB on a fixed cadence, keeps a per-cell sliding window, classifies
each cell (rule-based until the window fills, then BiLSTM with a confidence
gate), and dispatches SON actions without operator involvement.
"""
import logging
import os
import time
from collections import defaultdict, deque

import httpx
from influxdb_client import InfluxDBClient, Point
from influxdb_client.client.write_api import SYNCHRONOUS

from features import CLASS_NAMES, SEQ_LEN, extract_features
from model import infer, load_model
import train

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s %(levelname)s kpi_agent %(message)s")
log = logging.getLogger("kpi_agent")

INFLUX_URL = os.environ.get("INFLUX_URL", "http://influxdb:8086")
INFLUX_TOKEN = os.environ.get("INFLUX_TOKEN", "")
INFLUX_ORG = os.environ.get("INFLUX_ORG", "telecom")
INFLUX_BUCKET = os.environ.get("INFLUX_BUCKET", "telecom_metrics")
CONTROLLER_URL = os.environ.get("CONTROLLER_URL", "http://controller:8080")

POLL_SEC = int(os.environ.get("POLL_INTERVAL_SEC", "30"))
MODEL_PATH = os.environ.get("MODEL_PATH", "kpi_model.pt")
MIN_CONFIDENCE = float(os.environ.get("MIN_CONFIDENCE", "0.70"))

OVERLOAD_PRB = float(os.environ.get("OVERLOAD_PRB_PCT", "85"))
UNDERLOAD_PRB = float(os.environ.get("UNDERLOAD_PRB_PCT", "20"))
SINR_MIN = float(os.environ.get("SINR_MIN_DB", "5"))
POWER_WASTE_W = float(os.environ.get("POWER_WASTE_W", "500"))
POWER_WASTE_MIN_UES = int(os.environ.get("POWER_WASTE_MIN_UES", "15"))
MOVE_COOLDOWN_CYCLES = 3


def load_or_train():
    if os.path.exists(MODEL_PATH):
        log.info("loading model from %s", MODEL_PATH)
        return load_model(MODEL_PATH)
    log.info("no model at %s — training from scratch", MODEL_PATH)
    train.train(MODEL_PATH)
    return load_model(MODEL_PATH)


def connect_influx() -> InfluxDBClient:
    last = None
    for attempt in range(1, 20):
        try:
            c = InfluxDBClient(url=INFLUX_URL, token=INFLUX_TOKEN, org=INFLUX_ORG)
            c.ping()
            log.info("connected to InfluxDB")
            return c
        except Exception as e:  # noqa: BLE001
            last = e
            log.warning("InfluxDB not ready (%d): %s", attempt, e)
            time.sleep(6)
    raise RuntimeError(f"InfluxDB unreachable: {last}")


def query_latest(client: InfluxDBClient) -> dict[str, dict]:
    # No group() before pivot: that would strip tags (du_id/cu_id/...). last()
    # works per-series by default, so all tags survive into the pivoted row.
    flux = f'''
from(bucket: "{INFLUX_BUCKET}")
  |> range(start: -3m)
  |> filter(fn: (r) => r._measurement == "cell_kpi")
  |> last()
  |> pivot(rowKey: ["cell_id", "du_id", "cu_id"], columnKey: ["_field"], valueColumn: "_value")
'''
    out = {}
    try:
        for table in client.query_api().query(flux):
            for rec in table.records:
                cid = rec.values.get("cell_id")
                if cid:
                    out[cid] = rec.values
    except Exception as e:  # noqa: BLE001
        log.warning("query failed: %s", e)
    return out


def rule_classify(kpi: dict) -> str:
    prb = float(kpi.get("prb_dl_pct", 0) or 0)
    sinr = float(kpi.get("sinr_db", 99) or 99)
    power = float(kpi.get("power_w", 0) or 0)
    ues = float(kpi.get("connected_ues", 0) or 0)
    if prb >= OVERLOAD_PRB:
        return "OVERLOAD"
    if sinr <= SINR_MIN:
        return "SINR_LOW"
    if power >= POWER_WASTE_W and ues <= POWER_WASTE_MIN_UES:
        return "POWER_WASTE"
    if prb <= UNDERLOAD_PRB:
        return "UNDERLOAD"
    return "NORMAL"


class SON:
    def __init__(self, client: InfluxDBClient):
        self.write_api = client.write_api(write_options=SYNCHRONOUS)
        self.last_moved: dict[str, int] = {}

    def _alert(self, severity, cell_id, du_id, alert_type, message, conf,
               metric=0.0, threshold=0.0):
        p = (Point("alerts").tag("severity", severity).tag("cell_id", cell_id)
             .tag("du_id", du_id).tag("alert_type", alert_type)
             .field("message", message).field("metric_value", float(metric))
             .field("threshold", float(threshold)).field("ai_confidence", float(conf)))
        self.write_api.write(bucket=INFLUX_BUCKET, record=p)

    def _action(self, cell_id, du_id, action_type, message, conf):
        p = (Point("son_actions").tag("cell_id", cell_id).tag("du_id", du_id)
             .tag("action_type", action_type)
             .field("message", message).field("confidence", float(conf)))
        self.write_api.write(bucket=INFLUX_BUCKET, record=p)

    def dispatch(self, cls, cell_id, kpi, conf, du_avg, cycle):
        du_id = kpi.get("du_id", "")
        if cls == "OVERLOAD":
            self._alert("WARNING", cell_id, du_id, "OVERLOAD",
                        f"PRB {kpi.get('prb_dl_pct')}% >= {OVERLOAD_PRB}%", conf,
                        kpi.get("prb_dl_pct", 0), OVERLOAD_PRB)
            self._load_balance(cell_id, du_id, du_avg, conf, cycle)
        elif cls == "UNDERLOAD":
            self._alert("INFO", cell_id, du_id, "UNDERLOAD",
                        f"PRB {kpi.get('prb_dl_pct')}% <= {UNDERLOAD_PRB}%", conf)
            tgt = self._lightest_du(du_id, du_avg)
            self._action(cell_id, du_id, "TRAFFIC_STEER",
                         f"Steer UEs to {tgt} and enable sleep/DTX", conf)
        elif cls == "SINR_LOW":
            self._alert("CRITICAL", cell_id, du_id, "SINR_DEGRADATION",
                        f"SINR {kpi.get('sinr_db')}dB <= {SINR_MIN}dB", conf,
                        kpi.get("sinr_db", 0), SINR_MIN)
            self._action(cell_id, du_id, "PCI_REOPT_REQUEST",
                         "Request PCI re-optimisation for interference", conf)
            try:
                httpx.post(f"{CONTROLLER_URL}/son/pci-reopt",
                           json={"cell_id": cell_id}, timeout=5.0)
            except Exception:  # noqa: BLE001 - endpoint optional
                pass
        elif cls == "POWER_WASTE":
            self._alert("WARNING", cell_id, du_id, "POWER_WASTE",
                        f"{kpi.get('power_w')}W at {kpi.get('connected_ues')} UEs", conf,
                        kpi.get("power_w", 0), POWER_WASTE_W)
            self._action(cell_id, du_id, "DTX_RECOMMEND",
                         "Enable DTX/sleep (est. ~35% power saving)", conf)

    def _lightest_du(self, cur_du, du_avg):
        others = {d: v for d, v in du_avg.items() if d != cur_du}
        return min(others, key=others.get) if others else cur_du

    def _load_balance(self, cell_id, du_id, du_avg, conf, cycle):
        target = self._lightest_du(du_id, du_avg)
        on_cooldown = cycle - self.last_moved.get(cell_id, -99) < MOVE_COOLDOWN_CYCLES
        if target == du_id or on_cooldown:
            self._action(cell_id, du_id, "LOAD_BALANCE",
                         f"Recommend move to {target} (cooldown/no target)", conf)
            return
        try:
            r = httpx.post(f"{CONTROLLER_URL}/move/cell",
                           json={"cell_id": cell_id, "to_du_id": target}, timeout=10.0)
            r.raise_for_status()
            self.last_moved[cell_id] = cycle
            self._action(cell_id, du_id, "LOAD_BALANCE",
                         f"Moved {cell_id}: {du_id} -> {target} (lightest DU)", conf)
            self._alert("INFO", cell_id, du_id, "LOAD_BALANCE",
                        f"Auto-moved to {target}", conf)
            log.info("LOAD_BALANCE: moved %s %s -> %s", cell_id, du_id, target)
        except Exception as e:  # noqa: BLE001
            log.error("move failed for %s: %s", cell_id, e)
            self._action(cell_id, du_id, "LOAD_BALANCE",
                         f"Move to {target} failed: {e}", conf)


def analyse(model, cells, buffers, son, cycle):
    # DU average PRB load map
    du_cells = defaultdict(list)
    for cid, kpi in cells.items():
        du_cells[kpi.get("du_id", "")].append(float(kpi.get("prb_dl_pct", 0) or 0))
    du_avg = {d: (sum(v) / len(v) if v else 0.0) for d, v in du_cells.items()}

    acted = 0
    for cid, kpi in cells.items():
        buffers[cid].append(extract_features(kpi))
        if len(buffers[cid]) == SEQ_LEN:
            idx, conf = infer(model, list(buffers[cid]))
            cls, use_model = CLASS_NAMES[idx], True
        else:
            cls, conf, use_model = rule_classify(kpi), -1.0, False

        if cls != "NORMAL" and (not use_model or conf >= MIN_CONFIDENCE):
            son.dispatch(cls, cid, kpi, conf, du_avg, cycle)
            acted += 1
    return acted


def main():
    model = load_or_train()
    client = connect_influx()
    son = SON(client)
    buffers = defaultdict(lambda: deque(maxlen=SEQ_LEN))
    log.info("KPI agent started (poll=%ds, min_conf=%.2f)", POLL_SEC, MIN_CONFIDENCE)
    cycle = 0
    while True:
        cells = query_latest(client)
        if cells:
            acted = analyse(model, cells, buffers, son, cycle)
            log.info("cycle %d: %d cells analysed, %d SON actions", cycle, len(cells), acted)
        else:
            log.info("cycle %d: no cell data", cycle)
        cycle += 1
        time.sleep(POLL_SEC)


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""Generate the 5 provisioned Grafana dashboards (Flux / InfluxDB).

Writes JSON dashboard models into provisioning/dashboards/ where the file
provider picks them up. Run:  python grafana/generate_dashboards.py
"""
import json
import os

DS = {"type": "influxdb", "uid": "influxdb"}
BUCKET = "telecom_metrics"
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "provisioning", "dashboards")

_pid = 0


def pid():
    global _pid
    _pid += 1
    return _pid


def target(flux):
    return [{"datasource": DS, "query": flux, "refId": "A"}]


def stat(title, flux, x, y, w=6, h=4, unit="none"):
    return {"id": pid(), "type": "stat", "title": title, "datasource": DS,
            "gridPos": {"x": x, "y": y, "w": w, "h": h},
            "fieldConfig": {"defaults": {"unit": unit}, "overrides": []},
            "options": {"reduceOptions": {"calcs": ["lastNotNull"]}, "colorMode": "value"},
            "targets": target(flux)}


def ts(title, flux, x, y, w=12, h=8, unit="none"):
    return {"id": pid(), "type": "timeseries", "title": title, "datasource": DS,
            "gridPos": {"x": x, "y": y, "w": w, "h": h},
            "fieldConfig": {"defaults": {"unit": unit, "custom": {"drawStyle": "line",
                            "fillOpacity": 10}}, "overrides": []},
            "options": {"legend": {"displayMode": "list", "placement": "bottom"}},
            "targets": target(flux)}


def piechart(title, flux, x, y, w=8, h=8):
    return {"id": pid(), "type": "piechart", "title": title, "datasource": DS,
            "gridPos": {"x": x, "y": y, "w": w, "h": h},
            "options": {"reduceOptions": {"calcs": ["lastNotNull"]}, "legend": {"displayMode": "list"}},
            "targets": target(flux)}


def table(title, flux, x, y, w=24, h=8):
    return {"id": pid(), "type": "table", "title": title, "datasource": DS,
            "gridPos": {"x": x, "y": y, "w": w, "h": h},
            "targets": target(flux)}


def dashboard(uid, title, panels, var_generation=False):
    d = {"uid": uid, "title": title, "tags": ["nova"], "timezone": "browser",
         "schemaVersion": 39, "version": 1, "refresh": "30s", "editable": True,
         "time": {"from": "now-3h", "to": "now"}, "panels": panels,
         "templating": {"list": []}}
    if var_generation:
        d["templating"]["list"].append({
            "name": "generation", "type": "custom", "label": "Generation",
            "query": "5G,4G", "current": {"text": "5G", "value": "5G"},
            "options": [{"text": "5G", "value": "5G"}, {"text": "4G", "value": "4G"}]})
    return d


def f_sum_last(field):
    """Total across cells of the last value (for stat panels)."""
    return (f'from(bucket:"{BUCKET}") |> range(start:-5m) '
            f'|> filter(fn:(r)=>r._measurement=="cell_kpi" and r._field=="{field}") '
            f'|> group(columns:["cell_id"]) |> last() |> group() |> sum()')


def f_mean_last(field):
    return (f'from(bucket:"{BUCKET}") |> range(start:-5m) '
            f'|> filter(fn:(r)=>r._measurement=="cell_kpi" and r._field=="{field}") '
            f'|> group(columns:["cell_id"]) |> last() |> group() |> mean()')


def f_total_ts(field, fn="sum"):
    """Network-total timeseries: aggregate per cell then combine per window."""
    return (f'from(bucket:"{BUCKET}") |> range(start:v.timeRangeStart, stop:v.timeRangeStop) '
            f'|> filter(fn:(r)=>r._measurement=="cell_kpi" and r._field=="{field}") '
            f'|> aggregateWindow(every:v.windowPeriod, fn:last, createEmpty:false) '
            f'|> group(columns:["_time"]) |> {fn}() |> group()')


def f_percell_ts(field, gen_filter=False):
    g = ' and r.generation=="$generation"' if gen_filter else ''
    return (f'from(bucket:"{BUCKET}") |> range(start:v.timeRangeStart, stop:v.timeRangeStop) '
            f'|> filter(fn:(r)=>r._measurement=="cell_kpi" and r._field=="{field}"{g}) '
            f'|> aggregateWindow(every:v.windowPeriod, fn:mean, createEmpty:false)')


def build():
    os.makedirs(OUT, exist_ok=True)

    # 1) Network Overview
    p = [stat("Total Connected UEs", f_sum_last("connected_ues"), 0, 0),
         stat("Avg SINR (dB)", f_mean_last("sinr_db"), 6, 0),
         stat("Avg PRB DL (%)", f_mean_last("prb_dl_pct"), 12, 0, unit="percent"),
         stat("Total Power (W)", f_sum_last("power_w"), 18, 0, unit="watt"),
         ts("Total UEs", f_total_ts("connected_ues"), 0, 4),
         ts("Total DL Throughput (Mbps)", f_total_ts("dl_throughput_mbps"), 12, 4, unit="Mbits"),
         ts("Avg SINR per cell (dB)", f_percell_ts("sinr_db"), 0, 12),
         ts("PRB DL per cell (%)", f_percell_ts("prb_dl_pct"), 12, 12, unit="percent")]
    write("network_overview", dashboard("nova-net-overview", "Network Overview", p))

    # 2) Cell KPI (generation filter)
    p = [ts("PRB DL (%)", f_percell_ts("prb_dl_pct", True), 0, 0, unit="percent"),
         ts("SINR (dB)", f_percell_ts("sinr_db", True), 12, 0),
         ts("RSRP (dBm)", f_percell_ts("rsrp_dbm", True), 0, 8),
         ts("DL Throughput (Mbps)", f_percell_ts("dl_throughput_mbps", True), 12, 8, unit="Mbits"),
         ts("CQI", f_percell_ts("cqi", True), 0, 16),
         ts("BLER (%) & Latency (ms)", f_percell_ts("bler_pct", True), 12, 16)]
    write("cell_kpi", dashboard("nova-cell-kpi", "Cell KPI", p, var_generation=True))

    # 3) UE Analytics
    slice_dl = (f'from(bucket:"{BUCKET}") |> range(start:-30m) '
                f'|> filter(fn:(r)=>r._measurement=="ue_usage" and r._field=="dl_bytes") '
                f'|> group(columns:["slice_type"]) |> sum()')
    ho_rate = (f'from(bucket:"{BUCKET}") |> range(start:v.timeRangeStart, stop:v.timeRangeStop) '
               f'|> filter(fn:(r)=>r._measurement=="ue_mobility" and r._field=="ho_duration_ms") '
               f'|> aggregateWindow(every:v.windowPeriod, fn:count, createEmpty:false) |> group()')
    p = [piechart("DL bytes by slice", slice_dl, 0, 0, w=8, h=8),
         ts("Handover event rate", ho_rate, 8, 0, w=16, h=8),
         ts("UE DL bytes by slice", (f'from(bucket:"{BUCKET}") |> range(start:v.timeRangeStart, stop:v.timeRangeStop) '
            f'|> filter(fn:(r)=>r._measurement=="ue_usage" and r._field=="dl_bytes") '
            f'|> aggregateWindow(every:v.windowPeriod, fn:sum, createEmpty:false) '
            f'|> group(columns:["slice_type","_time"]) |> sum() |> group(columns:["slice_type"])'),
            0, 8, w=24, h=8)]
    write("ue_analytics", dashboard("nova-ue-analytics", "UE Analytics", p))

    # 4) SON & Alerts
    sev = lambda s: (f'from(bucket:"{BUCKET}") |> range(start:-60m) '
                     f'|> filter(fn:(r)=>r._measurement=="alerts" and r._field=="ai_confidence" '
                     f'and r.severity=="{s}") |> count() |> group() |> sum()')
    son_ts = (f'from(bucket:"{BUCKET}") |> range(start:v.timeRangeStart, stop:v.timeRangeStop) '
              f'|> filter(fn:(r)=>r._measurement=="son_actions" and r._field=="confidence") '
              f'|> aggregateWindow(every:v.windowPeriod, fn:count, createEmpty:false) '
              f'|> group(columns:["action_type"])')
    conf_ts = (f'from(bucket:"{BUCKET}") |> range(start:v.timeRangeStart, stop:v.timeRangeStop) '
               f'|> filter(fn:(r)=>r._measurement=="alerts" and r._field=="ai_confidence") '
               f'|> aggregateWindow(every:v.windowPeriod, fn:mean, createEmpty:false) |> group()')
    son_tbl = (f'from(bucket:"{BUCKET}") |> range(start:-60m) '
               f'|> filter(fn:(r)=>r._measurement=="son_actions" and r._field=="message") '
               f'|> keep(columns:["_time","action_type","cell_id","_value"]) '
               f'|> sort(columns:["_time"], desc:true) |> limit(n:20)')
    p = [stat("CRITICAL (60m)", sev("CRITICAL"), 0, 0, unit="none"),
         stat("WARNING (60m)", sev("WARNING"), 6, 0),
         stat("INFO (60m)", sev("INFO"), 12, 0),
         ts("SON actions by type", son_ts, 0, 4, w=12),
         ts("Mean AI confidence", conf_ts, 12, 4, w=12),
         table("Recent SON actions", son_tbl, 0, 12)]
    write("son_alerts", dashboard("nova-son-alerts", "SON & Alerts", p))

    # 5) DU/CU Performance
    du = lambda f, fn="mean": (f'from(bucket:"{BUCKET}") |> range(start:v.timeRangeStart, stop:v.timeRangeStop) '
                               f'|> filter(fn:(r)=>r._measurement=="du_kpi" and r._field=="{f}") '
                               f'|> aggregateWindow(every:v.windowPeriod, fn:{fn}, createEmpty:false)')
    cu = lambda f: (f'from(bucket:"{BUCKET}") |> range(start:v.timeRangeStart, stop:v.timeRangeStop) '
                    f'|> filter(fn:(r)=>r._measurement=="cu_kpi" and r._field=="{f}") '
                    f'|> aggregateWindow(every:v.windowPeriod, fn:mean, createEmpty:false)')
    core = lambda comp, f: (f'from(bucket:"{BUCKET}") |> range(start:v.timeRangeStart, stop:v.timeRangeStop) '
                            f'|> filter(fn:(r)=>r._measurement=="core_kpi" and r.component=="{comp}" and r._field=="{f}") '
                            f'|> aggregateWindow(every:v.windowPeriod, fn:mean, createEmpty:false)')
    p = [ts("DU CPU (%)", du("cpu_pct"), 0, 0, unit="percent"),
         ts("DU active UEs", du("active_ues"), 12, 0),
         ts("DU fronthaul latency (us)", du("fronthaul_latency_us"), 0, 8),
         ts("CU PDCP DL (Gbps)", cu("pdcp_dl_gbps"), 12, 8),
         ts("Core registered UEs (AMF)", core("AMF", "registered_ues"), 0, 16),
         ts("UPF DL throughput (Gbps)", core("UPF", "dl_throughput_gbps"), 12, 16)]
    write("du_cu_performance", dashboard("nova-du-cu-perf", "DU/CU Performance", p))


def write(name, model):
    path = os.path.join(OUT, name + ".json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(model, f, indent=2)
    print("wrote", path)


if __name__ == "__main__":
    build()

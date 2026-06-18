# NOVA — Operations Runbook

Operational guide for running, verifying, operating, and troubleshooting the
NOVA telecom network automation stack. For architecture and design, see
[`spec.md`](spec.md); for the build history, [`plan.md`](plan.md); for the data
contract, [`docs/schema.md`](docs/schema.md).

---

## 1. System overview

12 containers orchestrated by `docker-compose.yml`.

| Service | Port | Profile | Role |
|---|---|---|---|
| `influxdb` | 8086 | (always) | Time-series KPI store |
| `grafana` | 3000 | (always) | Dashboards (admin/admin) |
| `controller` | 8080 | agents/full | Topology control plane (source of truth) |
| `planning-api` | 8081 | agents/full | Placement / PCI / slice / MIP planning |
| `orchestrator` | 8082 | agents/full | LLM chat + 13-tool calling |
| `map-server` | 8083 | agents/full | Leaflet live map + chat proxy |
| `kpi-agent` | — | agents/full | BiLSTM anomaly detection + autonomous SON |
| `core-sim` | — | sims/full | AMF/SMF/UPF simulator |
| `cu-mls` | — | sims/full | CU simulator |
| `du-mls-1/2/3` | — | sims/full | DU simulators (12/9/9 cells) |

**Dependency order:** `influxdb` → `controller` → `planning-api`/`orchestrator` →
`map-server`; sims and `kpi-agent` depend on `influxdb` (+ `controller`).

**Data flow:** DU sims write `cell_kpi` → InfluxDB; Controller merges KPIs into
topology on read; KPI agent reads KPIs, classifies, and calls Controller
`/move/cell`; Planning pushes plans to Controller; Map/Orchestrator read it all.

---

## 2. Prerequisites

- **Docker Desktop** running (Windows/Mac) or Docker Engine (Linux). On Windows,
  closing the Docker Desktop *window* does **not** stop the engine — it keeps
  running in the tray. To stop it: tray icon → **Quit Docker Desktop**.
- **~4 GB free RAM**, **~6 GB free disk** (PyTorch image + InfluxDB data).
- **Python 3.12+** (only for `chat.py` / `demo.py` / tests — not the app itself).
- Ports free: **8080–8083, 8086, 3000**.
- Outbound network to `pypi.org` and `download.pytorch.org` for the first build.

Verify the engine is up:
```bash
docker info --format 'engine: {{.ServerVersion}} ({{.OSType}})'
```

---

## 3. First-time setup

```bash
git clone https://github.com/veenamo-anvi/NOVA.git
cd NOVA
cp .env.example .env          # then edit secrets (see §7)
```

`.env` is git-ignored — every machine creates its own. The defaults work for a
local demo; only the LLM backend needs a real key (§7).

---

## 4. Start / stop

### Start the full stack (first run builds images)
```bash
docker compose --profile full up -d --build
docker compose ps            # expect 12 services "Up"
```

### Staged start (useful while iterating)
```bash
docker compose up -d influxdb grafana          # infra only
docker compose --profile sims up -d --build    # + simulators
docker compose --profile agents up -d --build  # + controller/planning/kpi/orch/map
```

### Rebuild one service after a code change
```bash
docker compose build orchestrator && docker compose up -d orchestrator
```

### Stop / restart
```bash
docker compose stop                # stop all (keep containers + data)
docker compose restart kpi-agent   # restart one
docker compose down                # remove containers (keep named volumes/data)
docker compose down -v             # also delete influxdb/grafana/model volumes (DATA LOSS)
```

> **After a reboot:** containers do **not** auto-start (no restart policy set).
> Re-run `docker compose --profile full up -d`. Volume data persists.

---

## 5. Health verification

```bash
# Service health
curl -s localhost:8080/health      # controller
curl -s localhost:8081/health      # planning
curl -s localhost:8082/health      # orchestrator -> shows active LLM backend+model
curl -s localhost:8083/health      # map-server
curl -s localhost:3000/api/health  # grafana

# 30 cells served with live KPIs merged
curl -s localhost:8080/network | python -c "import sys,json; d=json.load(sys.stdin); print(len(d['cells']),'cells,', sum(1 for c in d['cells'] if c['kpi']),'with KPIs')"

# Telemetry flowing (measurements present)
docker exec influxdb influx query 'import "influxdata/influxdb/schema" schema.measurements(bucket:"telecom_metrics")' --raw | grep -oE 'cell_kpi|du_kpi|cu_kpi|core_kpi|alerts|son_actions' | sort -u
```

**Expected:** all `{"status":"ok"}`; 30 cells / 30 with KPIs; measurements
present. KPIs take ~10–20 s to appear after the sims start.

---

## 6. Operating the system

| Surface | URL / command |
|---|---|
| Live cell map | http://localhost:8083 |
| Grafana dashboards | http://localhost:3000 → folder **NOVA** (admin/admin) |
| InfluxDB UI | http://localhost:8086 |
| Operator CLI | `py chat.py` (shortcuts: `/status /alerts /cells /plan /son /ue`) |
| Scripted demo | `py demo.py` |

**Common chat commands** (CLI or map panel): "what is the network status",
"show recent SON actions", "generate an optimal plan using mip",
"move MLS_RWS_01 to DU-MLS-2".

---

## 7. LLM backend configuration

The orchestrator picks a backend at startup, in this order:

1. **Claude CLI** — if `CLAUDE_CLI_PATH` is set *and* the binary exists in the image
2. **Anthropic API** — if `ANTHROPIC_API_KEY` is set (model `ANTHROPIC_MODEL`, default `claude-opus-4-8`)
3. **Gemini** — if `GOOGLE_API_KEY` is set (model `GEMINI_MODEL`)
4. **Mock** — deterministic intent router (no credentials needed; demo-able offline)

Set in `.env`, then `docker compose up -d orchestrator`. Confirm with:
```bash
curl -s localhost:8082/health      # {"backend":"anthropic-api","model":"claude-opus-4-8"}
```

- **Lower cost:** set `ANTHROPIC_MODEL=claude-haiku-4-5` (or `claude-sonnet-4-6`).
- **Security:** never commit `.env`. Rotate any key that has been shared.

---

## 8. Routine operational tasks

### Regenerate the canonical topology (seed)
`topology.json` is both the committed seed **and** runtime state the SON agent
mutates (autonomous cell moves). To reset it to the clean 30-cell layout:
```bash
docker stop kpi-agent                          # freeze SON moves first
python dev-env/config/generate_topology.py     # rewrite topology.json
docker start kpi-agent
```

### Retrain the KPI model
The model trains on first boot and persists to the `kpi-models` volume. Force a
retrain:
```bash
docker volume rm deeplearning_project_kpi-models   # stack must be down for this
docker compose up -d kpi-agent                      # retrains on next boot (~30 s)
```

### Generate / apply a plan via API
```bash
PID=$(curl -s -X POST localhost:8081/plan -H 'Content-Type: application/json' -d '{"use_mip":true}' | python -c "import sys,json;print(json.load(sys.stdin)['plan_id'])")
curl -s -X POST localhost:8081/plan/apply -H 'Content-Type: application/json' -d "{\"plan_id\":\"$PID\"}"
```

### Regenerate Grafana dashboards
```bash
python grafana/generate_dashboards.py
docker compose restart grafana
```

### Run the test suite
```bash
python -m unittest discover -s tests -p "test_*.py"   # 28 tests; integration auto-skips if down
```

---

## 9. Logs & monitoring

```bash
docker compose logs -f orchestrator        # follow one service
docker compose logs --tail 50 kpi-agent    # last 50 lines
docker compose ps                          # status of all
docker stats                               # live CPU/mem per container
```

Per-service signals to watch:
- `kpi-agent`: `cycle N: M cells analysed, K SON actions` + `LOAD_BALANCE: moved ...`
- `du-mls-*`: `emitted 12 cells (N points)`
- `controller`: `moved cell ...`, `topology replaced ...`

---

## 10. Troubleshooting

### Image build fails (controller / kpi-agent / any)
Both Dockerfiles `pip install` over the network, so failures are usually
environmental. Capture the real error:
```bash
docker compose build controller 2>&1 | tail -25
```
| Symptom in log | Cause | Fix |
|---|---|---|
| `Could not fetch URL`, `Read timed out`, `SSLError`, proxy 403 | Network / corporate proxy blocking `pypi.org` (or `download.pytorch.org` for kpi-agent) | Configure Docker's proxy (Docker Desktop → Settings → Resources → Proxies) or a pip mirror; try off-VPN |
| `no space left on device`, `failed to write` | Out of disk | Free space; `docker system prune -af` (removes unused images/cache) |
| build killed / OOM during torch install | Low RAM | Raise Docker Desktop memory (Settings → Resources) to ≥4 GB |
| `no matching distribution for torch==2.5.1` | Unsupported CPU arch | Rare; on Apple Silicon ensure recent Docker; or pin a torch version with an arm64 cpu wheel |
| `no such service` | Service is behind a profile | Build by name: `docker compose build controller kpi-agent` |

### A service is "Restarting" / crash-looping
```bash
docker compose logs --tail 40 <service>
```
- Sims/agents crash-looping right after start usually = **InfluxDB not healthy
  yet**; they retry (up to ~19×). Give it ~1 min, then recheck.
- Persistent crash = read the traceback in the logs.

### Port already in use
```
Error: ports are not available ... 8086 ... address already in use
```
Another process (often a stray old container) holds the port. Find and stop it:
```bash
docker ps -a --filter "publish=8086"
docker rm -f <container>            # or change the host port in docker-compose.yml
```

### `/network` returns cells but `kpi` is empty
Simulators aren't writing, or InfluxDB auth mismatch.
```bash
docker compose logs --tail 20 du-mls-1     # should show "emitted ... cells"
docker compose ps influxdb                  # must be "healthy"
```
Ensure `.env` `INFLUX_TOKEN/ORG/BUCKET` match across all services (they default
consistently if you copied `.env.example`).

### Orchestrator `/chat` errors
- `{"detail":"error parsing the body"}` → malformed JSON in the request (often a
  non-ASCII char from a shell); send valid UTF-8 JSON.
- `[Error] Anthropic API: ...` → bad/expired `ANTHROPIC_API_KEY`, rate limit, or
  no network. Check the key; `curl localhost:8082/health` shows the active backend.
- Backend shows `mock` unexpectedly → no LLM credential was set in `.env` (§7).

### Map shows no cells
`map-server` proxies the Controller. Check `curl localhost:8083/api/cells` — a
503 means the Controller is down; otherwise it's a browser/tile issue.

### Containers vanished after reboot
Expected — no restart policy. `docker compose --profile full up -d`. Data in
named volumes (InfluxDB, Grafana, model) persists.

---

## 11. Data & backups

Persistent named volumes (survive `down`, deleted by `down -v`):
```bash
docker volume ls | grep deeplearning_project
#  deeplearning_project_influxdb-data   (KPI history)
#  deeplearning_project_grafana-data    (dashboard edits)
#  deeplearning_project_kpi-models      (trained kpi_model.pt)
```
Back up InfluxDB:
```bash
docker exec influxdb influx backup /tmp/backup && docker cp influxdb:/tmp/backup ./influx-backup
```
`topology.json` is plain text under `dev-env/config/` — copy it to snapshot the
live topology.

---

## 12. Clean teardown

```bash
docker compose down              # stop + remove containers, keep data
docker compose down -v           # + delete volumes (full reset, DATA LOSS)
docker system prune -af          # reclaim build cache / dangling images (optional)
```

---

## 13. Quick reference

```bash
# bring everything up
docker compose --profile full up -d --build
# health sweep
for p in 8080 8081 8082 8083; do curl -s localhost:$p/health; echo; done
# operate
py chat.py
open http://localhost:8083      # map
open http://localhost:3000      # grafana (admin/admin)
# reset topology
docker stop kpi-agent; python dev-env/config/generate_topology.py; docker start kpi-agent
# tear down
docker compose down
```

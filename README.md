# NOVA — Telecom Network Automation

Multi-agent Agentic AI system that plans, deploys, and continuously optimizes an
O-RAN-compliant 4G/5G (NSA) network for Malleswaram, North Bangalore. The live
deployment is simulated (3 DU + 1 CU + core containers); all UE telemetry is
synthetic but physically grounded.

See [`spec.md`](spec.md) for the full specification and [`plan.md`](plan.md) for
the build roadmap.

## Architecture (12 containers)

| Service | Port | Role |
|---|---|---|
| `influxdb` | 8086 | Time-series KPI store |
| `grafana` | 3000 | Dashboards |
| `controller` | 8080 | Topology control plane (source of truth) |
| `planning-api` | 8081 | Placement / PCI / slice / MIP planning |
| `orchestrator` | 8082 | LLM chat + tool-calling |
| `map-server` | 8083 | Leaflet.js live map + chat proxy |
| `kpi-agent` | — | BiLSTM anomaly detection + SON |
| `core-sim`, `cu-mls`, `du-mls-1..3` | — | Network simulators |

## Quick start

```bash
cp .env.example .env          # adjust secrets (GOOGLE_API_KEY etc.)

# Phase 0 — infra only (works today):
docker compose up -d influxdb grafana
#   InfluxDB UI  -> http://localhost:8086
#   Grafana      -> http://localhost:3000  (admin/admin)

# Later phases (as services are implemented):
docker compose --profile sims up -d      # simulators
docker compose --profile full up -d      # everything
```

## Repository layout

```
agents/        controller, planning, orchestrator, kpi_agent, map_server
sims/          du_sim, cu_sim, core_sim
ml/            dataset_generator.py, model.py, train.py
grafana/       provisioning (datasource + dashboards)
dev-env/config/ topology.json (+ generate_topology.py)
docs/          schema.md — the cross-agent data contract
chat.py        operator CLI (stdlib only)
```

## Data contracts

`topology.json` (Controller's domain) and the InfluxDB measurements are documented
in [`docs/schema.md`](docs/schema.md). Regenerate the topology with:

```bash
python dev-env/config/generate_topology.py
```

## Deployment runbook

### Prerequisites
- Docker + Docker Compose, Python 3.12+ (for `chat.py` / `demo.py` / tests).
- Ports free: 8080–8083, 8086, 3000.

### Bring up the full stack
```bash
cp .env.example .env
docker compose up -d influxdb grafana       # infra
docker compose --profile sims up -d --build  # simulators (digital twin)
docker compose --profile agents up -d --build # controller, planning, kpi, orch, map
# or everything at once:
docker compose --profile full up -d --build
docker compose ps                            # expect 12 services up
```

### Verify
```bash
curl -s localhost:8080/network | python -c "import sys,json;print(len(json.load(sys.stdin)['cells']),'cells')"
curl -s localhost:8082/health                # orchestrator backend/model
open http://localhost:8083                   # live map
open http://localhost:3000                   # Grafana (admin/admin) -> NOVA folder
```

### Operate
```bash
py chat.py                # interactive operator CLI
py demo.py                # scripted end-to-end walkthrough
```

### Tests
```bash
python -m unittest discover -s tests -p "test_*.py"
# unit tests run anywhere; the integration test auto-skips unless the stack is up.
```

### Enable a real LLM backend
The orchestrator uses a deterministic **mock** backend unless credentials are set:
- **Gemini**: put `GOOGLE_API_KEY=...` in `.env`, then `docker compose up -d orchestrator`.
- **Claude CLI**: install the CLI in the orchestrator image (see its `Dockerfile`)
  and set `CLAUDE_CLI_PATH=/usr/bin/claude`.

### Teardown
```bash
docker compose down            # stop (keep volumes)
docker compose down -v         # also remove influx/grafana/model volumes
```

### Note on `topology.json`
It is both the committed **seed** and the **runtime state** the KPI agent mutates
(autonomous SON moves). Regenerate the canonical seed any time with
`python dev-env/config/generate_topology.py`.

## Build status

All phases (0–7) complete — see [`plan.md`](plan.md). The 12-container stack is
fully implemented: digital twin, control plane, planning (heuristic + MIP),
BiLSTM SON agent, LLM orchestrator, live map, and Grafana dashboards.

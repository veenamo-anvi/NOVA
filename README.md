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

## Build status

Phase 0 (scaffolding + contracts + infra) is in place. Phases 1–7 are tracked in
[`plan.md`](plan.md).

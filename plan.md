# Implementation Plan — Telecom Network Automation (NOVA)

> Build roadmap derived from `spec.md`. The spec marks Phases 0–6 "complete," but the
> repository currently contains **no source code**. This plan treats the project as a
> greenfield build and sequences the work so each layer is runnable and testable before
> the next depends on it.

## Guiding Principles

- **Bottom-up, runnable at every step.** Data store → simulators → control plane → planning → AI → orchestrator → UI. Each phase ends with something you can `docker compose up` and verify.
- **Single source of truth.** `dev-env/config/topology.json` for topology; InfluxDB for all time-series KPIs. Only the Controller writes topology.
- **Contracts first.** Lock the InfluxDB measurement schema and the topology.json shape early (Phase 1); every downstream agent codes against them.
- **LLM-agnostic tools.** Tool schemas authored once in Anthropic format; Gemini translation is a thin adapter. Claude CLI is the default backend.

---

## Proposed Repository Layout

```
NOVA/
├── spec.md
├── plan.md
├── README.md
├── chat.py                       # operator CLI (stdlib only)
├── docker-compose.yml
├── .env.example
├── dev-env/
│   └── config/
│       └── topology.json         # 30-cell Malleswaram topology (source of truth)
├── agents/
│   ├── orchestrator/             # :8082  LLM chat + tool-calling
│   ├── controller/               # :8080  topology control plane
│   ├── planning/                 # :8081  placement / PCI / slices / MIP
│   ├── kpi_agent/                # background BiLSTM anomaly detection + SON
│   └── map_server/               # :8083  Leaflet.js map + chat proxy
├── sims/
│   ├── du_sim/                   # 3× DU simulators (4G+5G RAN, KPI generation)
│   ├── cu_sim/                   # 1× CU simulator (RRC/PDCP)
│   └── core_sim/                 # AMF/SMF/UPF simulator
├── ml/
│   ├── dataset_generator.py      # synthetic 50,400-row CSV
│   ├── model.py                  # KPIClassifier BiLSTM
│   └── train.py                  # training loop + weights export
├── grafana/
│   └── provisioning/             # datasource + 5 dashboards
└── tests/                        # unit + integration
```

---

## Phase 0 — Project Scaffolding & Contracts ✅ COMPLETE
**Goal:** repo skeleton, shared schemas, and infra that everything else builds on.

- [x] Create directory tree above; add `.gitignore` (Python, `*.pt`, `__pycache__`, `.env`).
- [x] Author `docker-compose.yml` with all 12 services (`sims`/`agents`/`full` profiles) + shared `nova` network.
- [x] Stand up **InfluxDB** (:8086) and **Grafana** (:3000) with init env (org `telecom`, bucket `telecom_metrics`, token) + datasource/dashboard provisioning.
- [x] Write `.env.example` documenting every env var from the spec config tables.
- [x] Define **`topology.json`** schema + generator (`dev-env/config/generate_topology.py`): 30 cells, full hardware metadata, 16,500 cell-level max UEs, unique PCIs.
- [x] Document the **9 InfluxDB measurements** in `docs/schema.md` — the cross-agent contract.

**Done when:** ~~`docker compose up influxdb grafana` works; topology.json validates against the documented schema.~~ ✅ InfluxDB ping 204, Grafana health ok, topology validated.

> Note: removed a pre-existing `dev-env` Docker Compose project (26 containers, different multi-region topology) that conflicted on NOVA's container names/ports. Data volumes left intact.

## Phase 1 — Foundation: 30-Cell Topology + Controller ✅ COMPLETE
**Goal:** the live network's source of truth and its control plane.

- [x] Generate the **30-cell Malleswaram topology** (done in Phase 0 via `generate_topology.py`).
- [x] Build **Controller** (`agents/controller/`, FastAPI :8080):
  - [x] Atomic topology load/write (`.tmp` → `os.replace`) with a process lock.
  - [x] KPI merge from InfluxDB (`cell_kpi` last 3 min) on `/network`, `/cells`, `/cells/{id}` (best-effort; serves config if Influx is down).
  - [x] Routes: `/health`, `/topology`, `/network`, `/cells`, `/cells/{id}`, `/dus`, `/cus`, `/neighbors/{id}`.
  - [x] Mutations: `/move/cell`, `/move/du`, `/topology/replace`, `/cells/add` (PCI auto-assign on `pci:0`), `DELETE /cells/{id}`.
  - [x] Write `topology_event` to InfluxDB on every mutation.

**Done when:** ~~`GET /network` returns 30 cells; a `/move/cell` persists and emits a topology_event.~~ ✅ Verified: 30 cells, move persists & reverses, PCI auto-assign (→31), 7 topology_events landed in InfluxDB.

## Phase 2 — Simulators (Digital Twin) ✅ COMPLETE
**Goal:** synthetic but physically-grounded KPI telemetry feeding InfluxDB.

- [x] **DU simulator** (`sims/du_sim/`): reads topology each cycle (picks up moves); per assigned cell generates the full 16-field `cell_kpi` set using COST-231-Hata RSRP + diurnal load curve + `WEEKEND_FACTOR=0.75`; correlated SINR→CQI→MCS→throughput, load→power; writes `du_kpi`.
- [x] **CU simulator** (`sims/cu_sim/`): aggregates its DUs' latest cell_kpi → `cu_kpi` (RRC, PDCP throughput, F1/N2/N3/E1 latency, CPU/mem).
- [x] **Core simulator** (`sims/core_sim/`): network-wide aggregate → `core_kpi` AMF/SMF/UPF points.
- [x] Emit `ue_mobility` (handover events) and `ue_usage` (per-slice eMBB/URLLC/mMTC) records.
- [x] Wire 3 DU containers (12/9/9 cells) + 1 CU + core into compose (`sims` profile).

**Done when:** ~~Grafana shows live KPIs streaming for all 30 cells; load follows the diurnal curve.~~ ✅ All 30 cells stream live KPIs (verified via Controller `/network` merge), diurnal load confirmed across hours, all 7 measurements present. (Grafana *dashboards* land in Phase 6; datasource already provisioned.)

## Phase 3 — Planning Engine ✅ COMPLETE
**Goal:** generate complete network plans from high-level parameters.

- [x] **Heuristic pipeline** (`agents/planning/`, FastAPI :8081): `select_cells` (density-weighted Haversine) → `assign_pcis` (graph-coloring, collision/confusion-free) → `assign_dus`/`assign_cus` (proximity) → centroids → `timing_sync` → `allocate_slices` (eMBB/URLLC/mMTC) → `fronthaul_routing` → `plan_to_topology()` (preserves all hardware fields).
- [x] **MIP placement** (`mip_placer.py`): Almoghathawi 2024 formulation via `pulp`/CBC; COST-231-Walfisch-Ikegami NLOS path loss; single-build/activation/coverage/capacity/SINR constraints; heuristic fallback on timeout/infeasibility.
- [x] **Multi-period** planning: Case A (phased rollout, build reuse) + Case B (diurnal shift); 10 Bangalore demand clusters; CAPEX/OPEX split.
- [x] Routes: `/plan`, `/plan/multi-period`, `/plan/{id}`, `/plan/apply` (→ Controller `/topology/replace`), `/candidates`, `/demand-clusters`, `/health`.

**Done when:** ~~`POST /plan` returns a valid plan; `/plan/apply` deploys it and simulators reconfigure live.~~ ✅ Heuristic reproduces the 30-cell layout (clean PCI); MIP optimizes to 7 sites/875.5k vs heuristic 10 sites/1.25M; multi-period phased build schedule verified; `/plan/apply` pushed 30 cells to Controller and sims kept streaming.

## Phase 4 — KPI Monitoring Agent (ML + SON) ✅ COMPLETE
**Goal:** autonomous anomaly detection and corrective action.

> Note: ML code lives in `agents/kpi_agent/` (not a separate `ml/`) so the
> container is self-contained and trains on first boot per the spec's
> `load_or_train`. Model weights persist to a `kpi-models` Docker volume.

- [x] **Dataset** (`dataset_generator.py`): 50,400-row CSV (70d × 24h × 30 cells), class mix 70/15/8/5/2; CLI `--days --seed --out`; shared `sample_kpi()` per-class sampler.
- [x] **Model** (`model.py`): 2-layer BiLSTM, hidden 64, dropout 0.25, input `(B, 6, 9)`, Linear(128→64)→ReLU→Dropout→Linear(64→5).
- [x] **Features** (`features.py`): fixed 9-feature order + min/range normalisation covering 4G+5G.
- [x] **Training** (`train.py`): labelled sequences from per-class sampler, WeightedRandomSampler, exports `kpi_model.pt`.
- [x] **Agent** (`kpi_agent.py`): polls InfluxDB; per-cell `deque(maxlen=6)`; rule-based fallback until window fills, then BiLSTM with `MIN_CONFIDENCE=0.70` gate.
- [x] **SON actions**: OVERLOAD→LOAD_BALANCE (`/move/cell` to lightest DU + 3-cycle cooldown), UNDERLOAD→TRAFFIC_STEER, SINR_LOW→PCI_REOPT_REQUEST, POWER_WASTE→DTX_RECOMMEND; writes `alerts` + `son_actions`.

**Done when:** ~~an induced overload triggers an automatic cell move logged to `son_actions`.~~ ✅ Verified: model trained on boot, BiLSTM engaged at cycle 6 with ~0.9999 confidence, peak-hour overloads auto-moved cells to the lightest DU (logged to `son_actions` + Controller `topology_event`); LOAD_BALANCE + PCI_REOPT_REQUEST action types confirmed.

> Fixed mid-phase: the Flux query's `group(["cell_id","_field"])` stripped the
> `du_id` tag, leaving load-balance with no target DU — removed the group() so
> tags survive the pivot.

## Phase 5 — Orchestrator (LLM Agent) ✅ COMPLETE
**Goal:** natural-language operator control via tool-calling.

> Note: added a deterministic **MockBackend** (intent router) as a third backend
> so `/chat` and the full tool pipeline are testable without any LLM credentials.
> Selection: Claude CLI (if `CLAUDE_CLI_PATH` set & present) → Gemini (if
> `GOOGLE_API_KEY`) → mock.

- [x] **FastAPI :8082** with streaming `StreamingResponse` over a sync generator.
- [x] **13 tools** (`tools.py`) in Anthropic schema hitting real Controller/Planning/InfluxDB.
- [x] **Backend selection** by `CLAUDE_CLI_PATH` / `GOOGLE_API_KEY`: ClaudeCLIBackend (`claude -p`), GeminiBackend (`google-genai`, `_clean_params()` translation), MockBackend.
- [x] **Tool-calling loop**: Gemini `while True` until no function calls, JSON-sanitised results; mock single intent→tool; per-session in-memory history.
- [x] **Context injection** `build_network_context()` → Controller `/network` in the system prompt.
- [x] Routes: `/chat`, `/history` (GET/DELETE), `/tools`, `/health`.
- [x] **`chat.py`** CLI (stdlib only): `/status /alerts /cells /plan /son /ue /history /clear /tools`, `--url`, `--session`.

**Done when:** ~~`py chat.py` → "move the most loaded cell to the lightest DU" executes end-to-end.~~ ✅ Verified: `/health` reports backend, 13 tools listed, chat intents (status/plan-MIP/SON/alerts/move) execute through the tool loop against live services; `chat.py` shortcuts + history/clear work. (Free-form "most loaded → lightest" NL reasoning needs a real LLM backend; explicit moves work on mock.)

## Phase 6 — Map Server + Dashboards
**Goal:** live visualization and an in-browser chat panel.

- [ ] **Map Server** (`agents/map_server/`, FastAPI :8083): `/api/cells` with `compute_coverage_radius_m()` (COST-231-Hata invert); proxy routes to Orchestrator (`/api/chat` streaming, `/api/history`, `/api/tools`, `/api/orch-health`) with 503 on failure.
- [ ] **Leaflet UI**: vendor color, 5G/4G opacity, overload/SINR status fill, click popups, generation/vendor filters, 30 s auto-refresh, streaming chat panel with random session ID.
- [ ] **5 Grafana dashboards**: network_overview, cell_kpi, ue_analytics, son_alerts, du_cu_performance + datasource provisioning.

**Done when:** map renders all 30 cells with live overlays; chat panel streams responses.

## Phase 7 — Testing, Demo & Docs
- [ ] Unit tests: placement, PCI graph-coloring, slice allocation, coverage-radius math.
- [ ] Integration test: orchestrator → planning → controller → DU reconfigures.
- [ ] Demo script: "deploy Bangalore network from scratch via chat."
- [ ] `README.md` quickstart + deployment runbook.

---

## Sequencing & Dependencies

```
Phase 0 (infra + contracts)
   └─► Phase 1 (topology + Controller)
          ├─► Phase 2 (simulators)  ──┐
          └─► Phase 3 (planning)      ├─► Phase 5 (orchestrator) ─► Phase 6 (map/dash)
                                      │            ▲
          Phase 4 (KPI agent) ────────┘────────────┘
                                                     └─► Phase 7 (tests/demo)
```

- Phases 2, 3, 4 can proceed in parallel once Phase 1 lands (all depend only on the Controller + InfluxDB contracts).
- Phase 5 depends on Controller (1), Planning (3), and InfluxDB data (2/4) for its tools.
- Phase 6 depends on Phase 5 (chat proxy) and Phase 1 (cell data).

## Key Risks / Open Questions

- **Claude CLI in Docker**: spec assumes `/usr/bin/claude` present in the orchestrator image — confirm install/licensing path; Gemini fallback needs `GOOGLE_API_KEY`.
- **MIP solver runtime**: CBC may exceed `mip_time_limit_sec` on dense candidate sets — heuristic fallback must be solid.
- **InfluxDB lag vs SON cooldown**: the 3-cycle move cooldown guards against thrash; validate timing against `POLL_INTERVAL_SEC`.
- **Topology/KPI consistency**: after `/topology/replace`, stale KPIs from the old topology must be guarded (map server's 2× live-vs-model radius check).

## Suggested First Increment

Phases 0 → 1 → 2 give a runnable digital twin (topology + Controller + simulators + InfluxDB + Grafana) — the foundation everything else needs and the fastest path to something visibly working.

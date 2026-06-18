"""LLM backends + shared prompt/context for the orchestrator.

Backend selection (main.py):
  CLAUDE_CLI_PATH set & exists -> ClaudeCLIBackend
  else GOOGLE_API_KEY set       -> GeminiBackend
  else                          -> MockBackend (deterministic intent router)

Every backend exposes `chat(history, user_msg)` — a generator of text chunks.
It appends the user turn and the final assistant text to `history` (a list of
{"role","content"} dicts) so GET /history works regardless of backend.
"""
import copy
import json
import logging
import os
import re
import shutil
import subprocess

import httpx

import tools as T

log = logging.getLogger("orchestrator.backends")

CONTROLLER_URL = os.environ.get("CONTROLLER_URL", "http://controller:8080")

SYSTEM_PROMPT = """You are the operator assistant for an O-RAN 4G/5G (NSA) network in \
Malleswaram, North Bangalore: 30 cells across 10 macro sites (3 sectors each), \
grouped under 3 DUs (DU-MLS-1/2/3) and 1 CU (CU-MLS). Cell IDs follow \
MLS_<SITE>_<SECTOR> (e.g. MLS_RWS_01). Vendors: Nokia/Ericsson/Samsung/ZTE. \
Bands and per-cell UE limits: n78 3500MHz (900), n41 2500MHz (700), B40 2300MHz \
(300), B3 1800MHz (250).

You have tools to inspect and modify the live network, generate/apply plans, and \
read KPI alerts and autonomous SON actions. Guidelines:
- Confirm before destructive actions (moving/removing cells, applying plans).
- Flag overloaded cells (PRB > 85%) and degraded SINR (< 5 dB).
- Summarise results concisely, using bullet points or compact tables."""


def build_network_context() -> str:
    """Live one-line-per-cell snapshot appended to the system prompt."""
    try:
        net = httpx.get(f"{CONTROLLER_URL}/network", timeout=10.0).json()
    except Exception as e:  # noqa: BLE001
        return f"\n\n[WARNING] Controller unreachable, live state unavailable: {e}"
    lines = ["\n\nLIVE NETWORK STATE:"]
    for c in net.get("cells", []):
        k = c.get("kpi", {})
        lines.append(
            f"- {c['cell_id']} ({c.get('area','')}) -> DU={c.get('du_id')} | "
            f"UEs={k.get('connected_ues','?')} | PRB={k.get('prb_dl_pct','?')}% | "
            f"SINR={k.get('sinr_db','?')}dB | Power={k.get('power_w','?')}W")
    return "\n".join(lines)


def system_prompt_with_context() -> str:
    return SYSTEM_PROMPT + build_network_context()


# --------------------------------------------------------------------------- #
# Mock backend — deterministic intent router (no external LLM needed)
# --------------------------------------------------------------------------- #
class MockBackend:
    name = "mock-intent-router"
    backend = "mock"

    def _summarise_network(self, net: dict) -> str:
        cells = net.get("cells", [])
        total_ues = sum(c.get("kpi", {}).get("connected_ues", 0) for c in cells)
        overloaded = [c["cell_id"] for c in cells
                      if (c.get("kpi", {}).get("prb_dl_pct", 0) or 0) > 85]
        by_du: dict[str, int] = {}
        for c in cells:
            by_du[c.get("du_id", "?")] = by_du.get(c.get("du_id", "?"), 0) + 1
        out = [f"**Network status** — {len(cells)} cells, {total_ues} connected UEs.",
               "Cells per DU: " + ", ".join(f"{d}={n}" for d, n in sorted(by_du.items()))]
        out.append(f"Overloaded (PRB>85%): {len(overloaded)}"
                   + (f" — {', '.join(overloaded[:8])}" if overloaded else ""))
        return "\n".join(out)

    def chat(self, history: list[dict], user_msg: str):
        history.append({"role": "user", "content": user_msg})
        msg = user_msg.lower()
        chunks: list[str] = []

        def emit(s):
            chunks.append(s)
            return s

        # move cell <id> to <du>
        m = re.search(r"move\s+(?:cell\s+)?(\w+)\s+to\s+([\w-]+)", msg)
        if m and "plan" not in msg:
            cell_id = m.group(1).upper()
            to_du = m.group(2).upper()
            yield emit(f"\n\n*[calling tool: move_cell]*\n")
            res = T.execute_tool("move_cell", {"cell_id": cell_id, "to_du_id": to_du})
            yield emit(f"Move result: {json.dumps(res)}")
        elif any(w in msg for w in ("alert",)):
            yield emit("\n\n*[calling tool: get_alerts]*\n")
            res = T.execute_tool("get_alerts", {"minutes": 60})
            yield emit(f"**{res.get('count',0)} alerts in last 60 min.**\n"
                       + "\n".join(f"- [{a['severity']}] {a['cell_id']} {a['alert_type']}: {a['message']}"
                                   for a in res.get("alerts", [])[:8]))
        elif "son" in msg or "action" in msg:
            yield emit("\n\n*[calling tool: get_son_status]*\n")
            res = T.execute_tool("get_son_status", {"minutes": 60})
            yield emit(f"**SON actions (60 min):** {json.dumps(res.get('action_counts',{}))}\n"
                       + f"Alert severities: {json.dumps(res.get('alert_severities',{}))}")
        elif "ue" in msg or "usage" in msg or "mobility" in msg:
            yield emit("\n\n*[calling tool: query_ue]*\n")
            res = T.execute_tool("query_ue", {"minutes": 30})
            yield emit(f"**{res.get('count',0)} UE records (30 min).** Sample:\n"
                       + "\n".join(f"- {r['ue_id']} on {r['cell_id']} ({r['slice_type']}): {r['dl_bytes']} dl_bytes"
                                   for r in res.get("ue_records", [])[:6]))
        elif "multi-period" in msg or "multi period" in msg:
            mode = "temporary" if "temporary" in msg or "diurnal" in msg else "permanent"
            yield emit(f"\n\n*[calling tool: plan_network_multi_period]*\n")
            res = T.execute_tool("plan_network_multi_period", {"demand_mode": mode})
            yield emit(f"Multi-period ({mode}) plan {res.get('plan_id','?')[:8]}: "
                       f"{res.get('selected_cell_count')} cells, cost {res.get('cost_estimate',{})}")
        elif "plan" in msg:
            use_mip = "mip" in msg or "optimal" in msg
            yield emit(f"\n\n*[calling tool: plan_network]*\n")
            res = T.execute_tool("plan_network", {"use_mip": use_mip})
            yield emit(f"Plan {res.get('plan_id','?')[:8]}: {res.get('selected_cell_count')} cells "
                       f"over sites {res.get('selected_sites')}, mip_used={res.get('mip_used')}, "
                       f"cost={res.get('cost_estimate',{}).get('total')}. "
                       f"Say 'apply plan {res.get('plan_id','')}' to deploy.")
        elif m is None and re.search(r"\bcell\s+mls_\w+", msg):
            cid = re.search(r"(mls_\w+)", msg).group(1).upper()
            yield emit(f"\n\n*[calling tool: query_cell]*\n")
            res = T.execute_tool("query_cell", {"cell_id": cid})
            yield emit(f"{cid}: vendor={res.get('vendor')} band={res.get('band')} "
                       f"DU={res.get('du_id')} pci={res.get('pci')}")
        elif "list" in msg and "cell" in msg:
            yield emit("\n\n*[calling tool: list_cells]*\n")
            res = T.execute_tool("list_cells", {})
            yield emit(f"{res.get('total',0)} cells. " + ", ".join(
                c["cell_id"] for c in res.get("cells", [])[:12]) + " ...")
        else:
            yield emit("\n\n*[calling tool: query_network]*\n")
            net = T.execute_tool("query_network", {})
            yield emit(self._summarise_network(net)
                       + "\n\n_(Mock backend: set GOOGLE_API_KEY or CLAUDE_CLI_PATH for full LLM reasoning.)_")

        history.append({"role": "assistant", "content": "".join(chunks)})


# --------------------------------------------------------------------------- #
# Gemini backend — real tool-calling loop (needs GOOGLE_API_KEY)
# --------------------------------------------------------------------------- #
def _clean_params(schema: dict) -> dict:
    """Anthropic input_schema -> Gemini-compatible: strip defaults/empty enums."""
    s = copy.deepcopy(schema)

    def walk(node):
        if isinstance(node, dict):
            node.pop("default", None)
            if "enum" in node and not node["enum"]:
                node.pop("enum")
            for v in node.values():
                walk(v)
        elif isinstance(node, list):
            for v in node:
                walk(v)
    walk(s)
    return s


class GeminiBackend:
    backend = "gemini"

    def __init__(self):
        from google import genai
        self.genai = genai
        self.client = genai.Client(api_key=os.environ["GOOGLE_API_KEY"])
        self.name = os.environ.get("GEMINI_MODEL", "gemini-2.0-flash")
        self.gemini_tools = [{"function_declarations": [
            {"name": s["name"], "description": s["description"],
             "parameters": _clean_params(s["input_schema"])} for s in T.TOOL_SCHEMAS]}]

    def chat(self, history: list[dict], user_msg: str):
        from google.genai import types
        history.append({"role": "user", "content": user_msg})
        contents = [types.Content(role=("model" if h["role"] == "assistant" else "user"),
                                  parts=[types.Part(text=h["content"])])
                    for h in history if isinstance(h.get("content"), str)]
        cfg = types.GenerateContentConfig(
            system_instruction=system_prompt_with_context(), tools=self.gemini_tools)
        final_text = []
        while True:
            resp = self.client.models.generate_content(
                model=self.name, contents=contents, config=cfg)
            cand = resp.candidates[0]
            contents.append(cand.content)
            calls = []
            for part in cand.content.parts:
                if getattr(part, "text", None):
                    final_text.append(part.text)
                    yield part.text
                if getattr(part, "function_call", None):
                    calls.append(part.function_call)
            if not calls:
                break
            tool_parts = []
            for fc in calls:
                yield f"\n\n*[calling tool: {fc.name}...]*\n"
                result = T.execute_tool(fc.name, dict(fc.args or {}))
                tool_parts.append(types.Part.from_function_response(
                    name=fc.name, response={"result": json.loads(json.dumps(result, default=str))}))
            contents.append(types.Content(role="user", parts=tool_parts))
        history.append({"role": "assistant", "content": "".join(final_text)})


# --------------------------------------------------------------------------- #
# Claude CLI backend — spawns `claude -p` (text generation; tool use via CLI)
# --------------------------------------------------------------------------- #
class ClaudeCLIBackend:
    backend = "claude-cli"

    def __init__(self, cli_path: str):
        self.cli_path = cli_path
        self.name = os.environ.get("ANTHROPIC_MODEL_NAME", "sonnet")

    def chat(self, history: list[dict], user_msg: str):
        history.append({"role": "user", "content": user_msg})
        prompt = system_prompt_with_context() + "\n\nOperator: " + user_msg
        try:
            proc = subprocess.run(
                [self.cli_path, "-p", prompt, "--model", self.name],
                capture_output=True, text=True, timeout=120)
            out = proc.stdout.strip() or proc.stderr.strip() or "(no output)"
        except Exception as e:  # noqa: BLE001
            out = f"\n\n[Error] Claude CLI failed: {e}"
        yield out
        history.append({"role": "assistant", "content": out})


def get_backend():
    cli = os.environ.get("CLAUDE_CLI_PATH", "").strip()
    if cli and (shutil.which(cli) or os.path.exists(cli)):
        log.info("backend: Claude CLI (%s)", cli)
        return ClaudeCLIBackend(cli)
    if os.environ.get("GOOGLE_API_KEY", "").strip():
        try:
            b = GeminiBackend()
            log.info("backend: Gemini (%s)", b.name)
            return b
        except Exception as e:  # noqa: BLE001
            log.warning("Gemini init failed (%s); falling back to mock", e)
    log.info("backend: mock intent router (no LLM credentials configured)")
    return MockBackend()

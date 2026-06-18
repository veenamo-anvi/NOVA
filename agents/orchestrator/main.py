"""Orchestrator API (FastAPI, :8082).

Accepts natural-language operator commands, drives a tool-calling loop against
the selected LLM backend, and streams the response back as text/plain. Sessions
are kept in memory per session_id.
"""
import logging

from fastapi import FastAPI
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

import backends
import tools as T

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s %(levelname)s %(name)s %(message)s")
log = logging.getLogger("orchestrator")

app = FastAPI(title="NOVA Orchestrator", version="1.0.0")

_backend = backends.get_backend()
_sessions: dict[str, list[dict]] = {}


class ChatRequest(BaseModel):
    message: str
    session_id: str = "default"


@app.get("/health")
def health():
    return {"status": "ok", "model": _backend.name, "backend": _backend.backend}


@app.get("/tools")
def list_tools():
    return [{"name": s["name"], "description": s["description"]} for s in T.TOOL_SCHEMAS]


@app.get("/history")
def get_history(session_id: str = "default"):
    return {"session_id": session_id, "history": _sessions.get(session_id, [])}


@app.delete("/history")
def clear_history(session_id: str = "default"):
    _sessions.pop(session_id, None)
    return {"status": "ok", "cleared": session_id}


@app.post("/chat")
def chat(req: ChatRequest):
    history = _sessions.setdefault(req.session_id, [])

    def generate():
        try:
            for chunk in _backend.chat(history, req.message):
                yield chunk
        except Exception as e:  # noqa: BLE001
            log.exception("chat failed")
            yield f"\n\n[Error] {e}"

    return StreamingResponse(generate(), media_type="text/plain")

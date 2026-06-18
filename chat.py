#!/usr/bin/env python3
"""NOVA operator CLI — a terminal REPL over the orchestrator REST API.

Pure stdlib (urllib). No LLM logic here; it formats requests and prints
responses. The /chat call is synchronous (full response at once).

Usage:
  py chat.py                                # localhost:8082, session "default"
  py chat.py --url http://host:8082         # remote orchestrator
  py chat.py --session ops-team             # named, isolated session
"""
import argparse
import json
import sys
import urllib.error
import urllib.parse
import urllib.request

SHORTCUTS = {
    "/status": "What is the current status of all cells, DUs, and CUs? Summarise in a table.",
    "/alerts": "Show me all recent KPI alerts from the last 60 minutes.",
    "/cells": "List all cells with their current connected UEs, PRB utilisation, and DU assignment.",
    "/plan": "Generate a network plan for Malleswaram with default parameters and show me a summary.",
    "/son": "Show me the recent SON autonomous actions and their outcomes.",
    "/ue": "Show me UE usage and mobility events from the last 30 minutes.",
}


def _get(url):
    with urllib.request.urlopen(url, timeout=30) as r:
        return json.loads(r.read().decode())


def _delete(url):
    req = urllib.request.Request(url, method="DELETE")
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read().decode())


def _chat(url, message, session_id):
    body = json.dumps({"message": message, "session_id": session_id}).encode()
    req = urllib.request.Request(url + "/chat", data=body,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=180) as r:
        return r.read().decode()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default="http://localhost:8082")
    ap.add_argument("--session", default="default")
    args = ap.parse_args()
    url = args.url.rstrip("/")

    try:
        h = _get(url + "/health")
        print(f"NOVA operator CLI - backend={h.get('backend')} model={h.get('model')} @ {url}")
    except Exception as e:  # noqa: BLE001
        print(f"[warning] orchestrator not reachable at {url}: {e}")
    print("Type a command, a /shortcut, or 'quit'. Shortcuts: " + " ".join(SHORTCUTS) +
          " /history /clear /tools")

    while True:
        try:
            line = input(f"\n[{args.session}] > ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break
        if not line:
            continue
        if line in ("quit", "exit", "q"):
            break
        if line == "/tools":
            try:
                for t in _get(url + "/tools"):
                    print(f"  {t['name']}: {t['description']}")
            except Exception as e:  # noqa: BLE001
                print(f"[error] {e}")
            continue
        if line == "/history":
            try:
                hist = _get(url + f"/history?session_id={urllib.parse.quote(args.session)}")
                for turn in hist.get("history", []):
                    print(f"  [{turn['role']}] {str(turn['content'])[:200]}")
            except Exception as e:  # noqa: BLE001
                print(f"[error] {e}")
            continue
        if line == "/clear":
            try:
                _delete(url + f"/history?session_id={urllib.parse.quote(args.session)}")
                print("  (session cleared)")
            except Exception as e:  # noqa: BLE001
                print(f"[error] {e}")
            continue

        message = SHORTCUTS.get(line, line)
        try:
            print(_chat(url, message, args.session))
        except urllib.error.HTTPError as e:
            print(f"[HTTP {e.code}] {e.read().decode()[:300]}")
        except Exception as e:  # noqa: BLE001
            print(f"[error] {e}")


if __name__ == "__main__":
    main()

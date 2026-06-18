#!/usr/bin/env python3
"""NOVA demo — walk the operator workflow through the orchestrator chat API.

Drives a scripted sequence of natural-language commands against the running
stack and prints each response. Pure stdlib.

  py demo.py                       # against localhost:8082
  py demo.py --url http://h:8082
"""
import argparse
import json
import time
import urllib.request

STEPS = [
    ("Inspect the live network", "what is the current network status?"),
    ("Check for KPI anomaly alerts", "show me alerts from the last 60 minutes"),
    ("Review autonomous SON actions", "show me recent SON actions"),
    ("Generate an MIP-optimal plan", "generate an optimal network plan using mip"),
    ("Inspect UE usage", "show me UE usage from the last 30 minutes"),
    ("Rebalance a cell", "move MLS_RWS_01 to DU-MLS-2"),
]


def chat(url, message, session):
    body = json.dumps({"message": message, "session_id": session}).encode()
    req = urllib.request.Request(url + "/chat", data=body,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=180) as r:
        return r.read().decode()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default="http://localhost:8082")
    args = ap.parse_args()
    url = args.url.rstrip("/")

    try:
        h = json.loads(urllib.request.urlopen(url + "/health", timeout=5).read())
        print(f"=== NOVA demo - backend={h.get('backend')} model={h.get('model')} ===\n")
    except Exception as e:  # noqa: BLE001
        print(f"[fatal] orchestrator unreachable at {url}: {e}")
        return

    for i, (title, msg) in enumerate(STEPS, 1):
        print(f"\n{'='*68}\nSTEP {i}/{len(STEPS)} - {title}\n  operator> {msg}\n{'-'*68}")
        try:
            print(chat(url, msg, "demo"))
        except Exception as e:  # noqa: BLE001
            print(f"[error] {e}")
        time.sleep(1)
    print(f"\n{'='*68}\nDemo complete. Open the live map at http://localhost:8083\n{'='*68}")


if __name__ == "__main__":
    main()

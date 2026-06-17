"""Atomic read/write access to topology.json — the network's source of truth.

Only the Controller writes this file. Writes go to a `.tmp` sibling then
`os.replace()` (atomic rename) so simulator pollers never read a partial file.
A process-level lock serialises concurrent mutations.
"""
import json
import os
import threading

TOPOLOGY_FILE = os.environ.get("TOPOLOGY_FILE", "/config/topology.json")

_lock = threading.Lock()


def load() -> dict:
    """Read and parse topology.json. Raises FileNotFoundError if missing."""
    with open(TOPOLOGY_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


def save_atomic(topo: dict) -> None:
    """Write topology atomically: serialise to `<file>.tmp` then rename over."""
    tmp = TOPOLOGY_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(topo, f, indent=2)
        f.write("\n")
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, TOPOLOGY_FILE)


def lock() -> threading.Lock:
    return _lock


def recompute_metadata(topo: dict) -> None:
    """Refresh derived counts in metadata in place."""
    meta = topo.setdefault("metadata", {})
    cells = topo.get("cells", {})
    meta["cell_count"] = len(cells)
    meta["site_count"] = len({c["cell_id"].rsplit("_", 1)[0] for c in cells.values()})
    meta["cell_level_max_ues"] = sum(c.get("max_ues", 0) for c in cells.values())

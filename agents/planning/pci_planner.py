"""PCI assignment via graph colouring — collision-free and confusion-free.

Two cells are "adjacent" if they are within NEIGHBOR_RADIUS_M of each other.
- Collision-free: no two adjacent cells share a PCI.
- Confusion-free: a cell's neighbours all get distinct PCIs (so no cell sees the
  same PCI from two different neighbours). Enforced by also forbidding, for each
  cell, the PCIs already used by any other neighbour of its neighbours.

Greedy smallest-available-colour over cells ordered by degree (most-constrained
first), which is effective for the sparse geographic graphs here.
"""
from propagation import haversine_m

NEIGHBOR_RADIUS_M = 1500.0
MAX_PCI = 1008  # 0..1007 in NR; we assign from 1 to keep 0 as the "unset" sentinel


def _adjacency(cells: list[dict]) -> dict[str, set[str]]:
    adj: dict[str, set[str]] = {c["cell_id"]: set() for c in cells}
    for i, a in enumerate(cells):
        for b in cells[i + 1:]:
            if haversine_m(a["lat"], a["lon"], b["lat"], b["lon"]) <= NEIGHBOR_RADIUS_M:
                adj[a["cell_id"]].add(b["cell_id"])
                adj[b["cell_id"]].add(a["cell_id"])
    return adj


def assign_pcis(cells: list[dict]) -> dict[str, int]:
    """Return {cell_id: pci}. Mutates each cell dict's 'pci' too."""
    adj = _adjacency(cells)
    by_id = {c["cell_id"]: c for c in cells}
    order = sorted(by_id, key=lambda cid: len(adj[cid]), reverse=True)

    pci: dict[str, int] = {}
    for cid in order:
        forbidden: set[int] = set()
        # collision: direct neighbours
        for n in adj[cid]:
            if n in pci:
                forbidden.add(pci[n])
        # confusion: PCIs used by neighbours-of-neighbours (2-hop)
        for n in adj[cid]:
            for nn in adj[n]:
                if nn != cid and nn in pci:
                    forbidden.add(pci[nn])
        chosen = next(p for p in range(1, MAX_PCI) if p not in forbidden)
        pci[cid] = chosen
        by_id[cid]["pci"] = chosen
    return pci


def verify(cells: list[dict]) -> dict:
    """Return {collisions, confusions} counts for a PCI assignment (for tests)."""
    adj = _adjacency(cells)
    pci = {c["cell_id"]: c.get("pci") for c in cells}
    collisions = 0
    for cid, neighbours in adj.items():
        for n in neighbours:
            if cid < n and pci[cid] == pci[n]:
                collisions += 1
    confusions = 0
    for cid, neighbours in adj.items():
        seen: dict[int, str] = {}
        for n in neighbours:
            p = pci[n]
            if p in seen:
                confusions += 1
            else:
                seen[p] = n
    return {"collisions": collisions, "confusions": confusions}

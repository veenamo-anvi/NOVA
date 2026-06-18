"""Add agent source dirs to sys.path so tests can import their modules."""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for sub in ("agents/planning", "agents/map_server", "agents/kpi_agent"):
    p = os.path.join(ROOT, sub)
    if p not in sys.path:
        sys.path.insert(0, p)

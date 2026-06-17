"""Request/response models for the Controller API."""
from pydantic import BaseModel, Field


class MoveCell(BaseModel):
    cell_id: str
    to_du_id: str


class MoveDU(BaseModel):
    du_id: str
    to_cu_id: str


class TopologyReplace(BaseModel):
    cus: dict
    dus: dict
    cells: dict
    metadata: dict | None = None


class AddCell(BaseModel):
    cell_id: str
    du_id: str
    area: str
    lat: float
    lon: float
    generation: str            # "5G" | "4G"
    band: str                  # "n78" | "n41" | "B40" | "B3"
    vendor: str
    freq_mhz: int
    pci: int = 0               # 0 -> auto-assign smallest unused PCI
    hardware_model: str = ""
    antenna_config: str = ""
    peak_dl_mbps: int = 0
    tx_power_w: float = 0.0
    idle_power_w: float = 0.0
    max_ues: int = 0
    cu_id: str = Field(default="")  # filled from the target DU if omitted

"""Request models for the Planning API."""
from pydantic import BaseModel, Field


class PlanRequest(BaseModel):
    geographic_area: str = "Malleswaram, North Bangalore"
    expected_user_density: int | None = None
    traffic_profile: dict | None = None
    spectrum_bands: list[str] | None = None
    latency_constraints: dict | None = None
    compute_resources: dict | None = None
    deployment_budget: float = 0.0
    use_mip: bool = False
    sinr_min_db: float = 10.0
    mip_time_limit_sec: int = 120
    target_active_ues: int = 16500


class MultiPeriodRequest(BaseModel):
    demand_mode: str = Field(default="permanent")  # "permanent" | "temporary"
    time_periods: list[dict] | None = None         # optional override of presets
    traffic_profile: dict | None = None
    spectrum_bands: list[str] | None = None
    deployment_budget: float = 0.0
    sinr_min_db: float = 10.0
    mip_time_limit_sec: int = 120


class ApplyRequest(BaseModel):
    plan_id: str

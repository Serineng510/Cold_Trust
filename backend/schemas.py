"""Request/response models."""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, model_validator


class SampleIn(BaseModel):
    t: int = Field(description="Epoch seconds when the sample was taken")
    temp_c: float = Field(ge=-60, le=125)
    hum: float = Field(default=0, ge=0, le=100)
    light: int = Field(ge=0, le=1024, description="Raw ADC, higher = brighter (an ESP8266 reads 1024 when saturated)")
    temp2_c: float | None = Field(default=None, ge=-60, le=125, description="Optional second thermometer")


class IngestIn(BaseModel):
    device_id: str = Field(min_length=1, max_length=40)
    seq: int = Field(ge=0)
    ts: int
    ping_type: Literal["ROUTINE", "EVENT"]
    trigger: str | None = Field(default=None, max_length=24)
    samples: list[SampleIn] = Field(min_length=1, max_length=500)
    sig: str = Field(min_length=64, max_length=64)
    demo: bool = Field(default=False, description="Node is sending mock readings (display label only)")
    demo_sensors: list[Literal["temp", "light"]] = Field(
        default_factory=list, max_length=2, description="Which sensors were mocked; empty with demo=true means temp")


class VehicleIn(BaseModel):
    lat: float | None = None
    lon: float | None = None
    speed_kmh: float | None = Field(default=None, ge=0, le=320)
    fuel_l: float | None = Field(default=None, ge=0)
    heading: str | None = None
    route_status: str | None = None
    zone: str | None = Field(default=None, description="Geofence name, or null when outside any")
    authorised_stop: bool | None = None


class EvidenceIn(BaseModel):
    device_id: str = "CC-001"
    trigger: str | None = None
    jpeg_base64: str = Field(min_length=100, max_length=3_000_000)


class OverrideIn(BaseModel):
    status: Literal["NORMAL", "UNDER_REVIEW", "QUARANTINED"]
    reason: str = Field(min_length=3, max_length=300)
    supervisor: str = Field(default="admin", max_length=60)


class ActorIn(BaseModel):
    by: str = Field(default="admin", min_length=1, max_length=60)


class ApprovalDecisionIn(BaseModel):
    approve: bool
    note: str | None = Field(default=None, max_length=300)
    by: str = Field(default="admin", min_length=1, max_length=60)


class ReassignIn(BaseModel):
    truck: str = Field(min_length=3, max_length=40)
    reason: str = Field(min_length=3, max_length=300)
    by: str = Field(default="admin", min_length=1, max_length=60)


class DemoMockIn(BaseModel):
    mock: bool | None = Field(default=None, description="Temperature: true = the node reports temp_c instead of its sensor")
    temp_c: float | None = Field(default=None, ge=-20, le=60, description="Mock temperature (C); keeps the last value if omitted")
    light_mock: bool | None = Field(default=None, description="Light: true = the node reports light_pct instead of its sensor")
    light_pct: float | None = Field(default=None, ge=0, le=100, description="Mock light level (%); keeps the last value if omitted")
    log: bool = Field(default=True, description="Write an audit entry (slider drags send log=false until released)")
    by: str = Field(default="admin", min_length=1, max_length=60)

    @model_validator(mode="after")
    def _something_to_change(self):
        if self.mock is None and self.light_mock is None:
            raise ValueError("send mock (temperature) and/or light_mock")
        return self


class VehicleActionIn(BaseModel):
    action: Literal["maintenance", "offline", "activate"]
    by: str = Field(default="admin", min_length=1, max_length=60)


class CameraObservationIn(BaseModel):
    person_present: bool
    persons: int = Field(default=0, ge=0, le=20)
    behaviours: list[str] = Field(default_factory=list, max_length=6,
                                  description="Pose cues such as 'reaching', 'crouching', 'lingering'")
    description: str = Field(default="", max_length=300)
    source: str = Field(default="camera", max_length=20)


class MaintenanceIn(BaseModel):
    hours: int = Field(default=168, ge=1, le=168, description="How much history to review: 6, 24 or 168 (7 days)")


class LoginIn(BaseModel):
    company_id: str = Field(min_length=1, max_length=40, description="Company ID of the admin or driver account")
    code: str = Field(min_length=1, max_length=200, description="The password that goes with that ID")

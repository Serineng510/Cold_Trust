"""Request/response models."""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class SampleIn(BaseModel):
    t: int = Field(description="Epoch seconds when the sample was taken")
    temp_c: float = Field(ge=-60, le=125)
    hum: float = Field(default=0, ge=0, le=100)
    light: int = Field(ge=0, le=1023, description="Raw ADC, higher = brighter")
    temp2_c: float | None = Field(default=None, ge=-60, le=125, description="Optional second thermometer")


class IngestIn(BaseModel):
    device_id: str = Field(min_length=1, max_length=40)
    seq: int = Field(ge=0)
    ts: int
    ping_type: Literal["ROUTINE", "EVENT"]
    trigger: str | None = Field(default=None, max_length=24)
    samples: list[SampleIn] = Field(min_length=1, max_length=500)
    sig: str = Field(min_length=64, max_length=64)


class VehicleIn(BaseModel):
    lat: float | None = None
    lon: float | None = None
    speed_mph: float | None = Field(default=None, ge=0, le=200)
    fuel_gal: float | None = Field(default=None, ge=0)
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


class ReassignIn(BaseModel):
    truck: str = Field(min_length=3, max_length=40)
    reason: str = Field(min_length=3, max_length=300)
    by: str = Field(default="admin", min_length=1, max_length=60)


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

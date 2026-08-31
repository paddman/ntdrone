from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class ORMModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class UserSummary(ORMModel):
    id: str
    username: str
    email: str
    full_name: str
    phone: str
    role: str
    is_active: bool
    totp_enabled: bool


class TeamSummary(ORMModel):
    id: str
    code: str
    name: str
    institution: str
    faculty: str | None
    status: str
    created_at: datetime


class SlotSummary(ORMModel):
    id: str
    name: str
    starts_at: datetime
    duration_minutes: int
    capacity: int
    resource_key: str
    is_open: bool


class BookingSummary(ORMModel):
    id: str
    team_id: str
    slot_id: str
    status: str
    queue_position: int
    created_at: datetime
    cooldown_until: datetime | None


class SimulationRunSummary(ORMModel):
    id: str
    team_id: str
    booking_id: str
    mode: str
    scenario_name: str
    status: str
    config_json: str
    metrics_json: str
    result_json: str
    created_at: datetime
    started_at: datetime | None
    ended_at: datetime | None


class LoginRequest(BaseModel):
    username: str = Field(min_length=3, max_length=80)
    password: str = Field(min_length=1, max_length=256)


class TOTPVerifyRequest(BaseModel):
    code: str = Field(min_length=6, max_length=12)


class MemberCreate(BaseModel):
    full_name: str = Field(min_length=2, max_length=200)
    username: str = Field(min_length=3, max_length=80, pattern=r"^[A-Za-z0-9._-]+$")
    email: str = Field(min_length=5, max_length=255)
    phone: str = Field(min_length=6, max_length=40)
    password: str = Field(min_length=12, max_length=256)


class BookingCreate(BaseModel):
    slot_id: str


class SlotCreate(BaseModel):
    name: str = Field(min_length=2, max_length=160)
    starts_at: datetime
    duration_minutes: int = Field(default=180, ge=30, le=1440)
    capacity: int = Field(default=1, ge=1, le=20)
    resource_key: str = Field(default="sim-01", min_length=2, max_length=80)


class QueueAdjust(BaseModel):
    queue_position: int = Field(ge=0, le=1000)
    confirm: bool = False


class SimulationCreate(BaseModel):
    booking_id: str
    mode: Literal["STANDARD", "BLIND"] = "STANDARD"
    scenario_name: str = Field(default="default", min_length=1, max_length=160)
    config: dict[str, Any] = Field(default_factory=dict)


class SimulationResultUpdate(BaseModel):
    status: Literal["RUNNING", "COMPLETED", "FAILED", "STOPPED", "CANCELLED"]
    metrics: dict[str, Any] = Field(default_factory=dict)
    result: dict[str, Any] = Field(default_factory=dict)
    output_path: str | None = None
    remote_run_id: str | None = None


class TeamDecision(BaseModel):
    action: Literal["approve", "reject"]
    reason: str | None = Field(default=None, max_length=2000)


class FeedbackCreate(BaseModel):
    rating: int = Field(ge=1, le=5)
    message: str = Field(min_length=2, max_length=4000)


class QueueNextResponse(BaseModel):
    status: Literal["success"] = "success"
    process: Literal["exe", "end"]
    run_id: str | None = None
    team_id: str | None = None
    booking_id: str | None = None
    scenario_name: str | None = None
    config: dict[str, Any] | None = None


class HealthResponse(BaseModel):
    status: Literal["ok"] = "ok"
    service: str
    version: str

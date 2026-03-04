from __future__ import annotations

import os
import random
import re
import json
import sqlite3
import threading
import time
import uuid
import math
from collections import defaultdict, deque
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import Enum
from io import BytesIO
from pathlib import Path
from typing import Any, Literal
from urllib import error as url_error
from urllib import request as url_request

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from starlette.responses import JSONResponse
from pydantic import BaseModel, Field, model_validator
from PIL import Image


BASE_DIR = Path(__file__).resolve().parent
STATIC_DIR = BASE_DIR / "static"
CONSOLE_FILE = STATIC_DIR / "index.html"
PROJECT_DIR = BASE_DIR.parent
DATA_DIR = PROJECT_DIR / "data"
IMAGE_DIR = DATA_DIR / "images"
IMAGE_DIR.mkdir(parents=True, exist_ok=True)
DB_PATH = DATA_DIR / "sattie_sky_hub.db"


class SatelliteType(str, Enum):
    EO_OPTICAL = "EO_OPTICAL"
    SAR = "SAR"


class CommandState(str, Enum):
    QUEUED = "QUEUED"
    ACKED = "ACKED"
    CAPTURING = "CAPTURING"
    DOWNLINK_READY = "DOWNLINK_READY"
    FAILED = "FAILED"


class SatelliteStatus(str, Enum):
    AVAILABLE = "AVAILABLE"
    MAINTENANCE = "MAINTENANCE"


class GroundStationType(str, Enum):
    FIXED = "FIXED"
    LAND_MOBILE = "LAND_MOBILE"
    MARITIME = "MARITIME"
    AIRBORNE = "AIRBORNE"


class GroundStationStatus(str, Enum):
    OPERATIONAL = "OPERATIONAL"
    MAINTENANCE = "MAINTENANCE"


class CreateSatelliteRequest(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    type: SatelliteType
    status: SatelliteStatus = SatelliteStatus.AVAILABLE
    satellite_id: str | None = Field(default=None, min_length=1, max_length=40)
    system_id: str | None = Field(default=None, min_length=1, max_length=40)  # backward compatibility


class UpdateSatelliteRequest(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=100)
    type: SatelliteType | None = None
    status: SatelliteStatus | None = None


class CreateGroundStationRequest(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    type: GroundStationType
    status: GroundStationStatus = GroundStationStatus.OPERATIONAL
    location: str | None = Field(default=None, max_length=120)
    ground_station_id: str | None = Field(default=None, min_length=1, max_length=40)


class UpdateGroundStationRequest(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    status: GroundStationStatus | None = None
    location: str | None = Field(default=None, max_length=120)


class CreateRequestorRequest(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    ground_station_id: str = Field(min_length=1, max_length=40)


class UpdateRequestorRequest(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    ground_station_id: str | None = Field(default=None, min_length=1, max_length=40)


class TaskPriority(str, Enum):
    BACKGROUND = "BACKGROUND"
    COMMERCIAL = "COMMERCIAL"
    URGENT = "URGENT"


class LookSide(str, Enum):
    ANY = "ANY"
    LEFT = "LEFT"
    RIGHT = "RIGHT"


class PassDirection(str, Enum):
    ANY = "ANY"
    ASCENDING = "ASCENDING"
    DESCENDING = "DESCENDING"


class DeliveryMethod(str, Enum):
    DOWNLOAD = "DOWNLOAD"
    S3 = "S3"
    WEBHOOK = "WEBHOOK"


class GenerationMode(str, Enum):
    INTERNAL = "INTERNAL"
    EXTERNAL = "EXTERNAL"


class ExternalMapSource(str, Enum):
    OSM = "OSM"


class UplinkCommandRequest(BaseModel):
    satellite_id: str
    ground_station_id: str | None = Field(default=None, min_length=1, max_length=40)
    requestor_id: str | None = Field(default=None, min_length=1, max_length=40)
    mission_name: str = Field(min_length=1, max_length=150)
    aoi_name: str = Field(default="unknown-aoi", min_length=1, max_length=120)
    aoi_center_lat: float | None = Field(default=None, ge=-90, le=90)
    aoi_center_lon: float | None = Field(default=None, ge=-180, le=180)
    # [min_lon, min_lat, max_lon, max_lat]
    aoi_bbox: list[float] | None = Field(default=None, min_length=4, max_length=4)
    window_open_utc: str | None = None
    window_close_utc: str | None = None
    priority: TaskPriority = TaskPriority.COMMERCIAL
    width: int = Field(default=1024, ge=128, le=4096)
    height: int = Field(default=1024, ge=128, le=4096)
    cloud_percent: int = Field(default=20, ge=0, le=100)
    max_cloud_cover_percent: int | None = Field(default=None, ge=0, le=100)
    max_off_nadir_deg: float | None = Field(default=None, ge=0, le=45)
    min_sun_elevation_deg: float | None = Field(default=None, ge=0, le=90)
    incidence_min_deg: float | None = Field(default=None, ge=0, le=90)
    incidence_max_deg: float | None = Field(default=None, ge=0, le=90)
    look_side: LookSide = LookSide.ANY
    pass_direction: PassDirection = PassDirection.ANY
    polarization: str | None = Field(default=None, max_length=10)
    delivery_method: DeliveryMethod = DeliveryMethod.DOWNLOAD
    delivery_path: str | None = Field(default=None, max_length=500)
    generation_mode: GenerationMode = Field(
        default=GenerationMode.INTERNAL,
        description="K-Sattie Sky Hub-only optional field. Select INTERNAL or EXTERNAL image generation.",
    )
    external_map_source: ExternalMapSource = Field(
        default=ExternalMapSource.OSM,
        description="K-Sattie Sky Hub-only optional field for EXTERNAL mode. Current supported source: OSM.",
    )
    external_map_zoom: int = Field(
        default=16,
        ge=1,
        le=19,
        description="K-Sattie Sky Hub-only optional field for EXTERNAL mode. Map zoom level (1-19).",
    )
    fail_probability: float = Field(default=0.05, ge=0.0, le=1.0)

    @model_validator(mode="after")
    def validate_business_fields(self) -> "UplinkCommandRequest":
        if (self.aoi_center_lat is None) != (self.aoi_center_lon is None):
            raise ValueError("aoi_center_lat and aoi_center_lon must be provided together")

        if self.aoi_bbox is not None:
            min_lon, min_lat, max_lon, max_lat = self.aoi_bbox
            if min_lon >= max_lon or min_lat >= max_lat:
                raise ValueError("aoi_bbox must be [min_lon, min_lat, max_lon, max_lat] with min < max")

        if self.window_open_utc and self.window_close_utc:
            try:
                open_dt = datetime.fromisoformat(self.window_open_utc.replace("Z", "+00:00"))
                close_dt = datetime.fromisoformat(self.window_close_utc.replace("Z", "+00:00"))
            except ValueError as exc:
                raise ValueError("window_open_utc/window_close_utc must be ISO8601") from exc
            if open_dt >= close_dt:
                raise ValueError("window_open_utc must be earlier than window_close_utc")

        if self.incidence_min_deg is not None and self.incidence_max_deg is not None:
            if self.incidence_min_deg > self.incidence_max_deg:
                raise ValueError("incidence_min_deg must be <= incidence_max_deg")

        if self.delivery_method in (DeliveryMethod.S3, DeliveryMethod.WEBHOOK) and not self.delivery_path:
            raise ValueError("delivery_path is required when delivery_method is S3 or WEBHOOK")

        if self.generation_mode == GenerationMode.EXTERNAL:
            has_center = self.aoi_center_lat is not None and self.aoi_center_lon is not None
            has_bbox = self.aoi_bbox is not None
            if not has_center and not has_bbox:
                raise ValueError("EXTERNAL generation requires aoi_center_lat/lon or aoi_bbox")

        return self


class UplinkCommandResponse(BaseModel):
    command_id: str
    state: CommandState
    satellite_id: str
    satellite_type: SatelliteType
    ground_station_id: str | None = None
    ground_station_name: str | None = None
    ground_station_type: GroundStationType | None = None
    requestor_id: str | None = None
    requestor_name: str | None = None
    mission_name: str
    aoi_name: str
    created_at: str


class SatelliteTypeProfileResponse(BaseModel):
    platform: str
    orbit_type: str
    nominal_altitude_km: int
    nominal_swath_km: int
    revisit_hours: int
    sensor_modes: list[str]
    default_product_type: str
    default_bands_or_polarization: list[str]


class SatelliteResponse(BaseModel):
    satellite_id: str
    internal_satellite_code: str | None = None
    name: str
    eng_model: str | None = None
    domain: str | None = None
    resolution_perf: str | None = None
    baseline_status: str | None = None
    primary_mission: str | None = None
    type: SatelliteType
    status: SatelliteStatus
    profile: SatelliteTypeProfileResponse


class SeedSatellitesResponse(BaseModel):
    satellite_ids: list[str]


class GroundStationResponse(BaseModel):
    ground_station_id: str
    internal_ground_station_code: str | None = None
    name: str
    type: GroundStationType
    status: GroundStationStatus
    location: str | None


class SeedGroundStationsResponse(BaseModel):
    ground_station_ids: list[str]


class RequestorResponse(BaseModel):
    requestor_id: str
    name: str
    ground_station_id: str
    ground_station_name: str | None = None


class SeedRequestorsResponse(BaseModel):
    requestor_ids: list[str]


class CommandStatusResponse(BaseModel):
    command_id: str
    satellite_id: str
    satellite_type: SatelliteType
    ground_station_id: str | None
    ground_station_name: str | None
    ground_station_type: GroundStationType | None
    requestor_id: str | None
    requestor_name: str | None
    mission_name: str
    aoi_name: str
    width: int
    height: int
    cloud_percent: int
    fail_probability: float
    state: CommandState
    message: str | None
    created_at: str
    updated_at: str
    download_url: str | None
    request_profile: dict[str, Any]
    acquisition_metadata: dict[str, Any] | None
    product_metadata: dict[str, Any] | None


class SaveLocalDownloadResponse(BaseModel):
    command_id: str
    saved_path: str
    file_size_bytes: int
    message: str


class ClearImagesResponse(BaseModel):
    deleted_count: int
    cleared_command_count: int
    message: str


class ClearDbResponse(BaseModel):
    deleted_satellites: int
    deleted_ground_stations: int
    deleted_requestors: int
    deleted_commands: int
    deleted_images: int
    seeded_satellites: int
    seeded_ground_stations: int
    seeded_requestors: int
    message: str


class ApiCallLogEntryResponse(BaseModel):
    time: str
    method: str
    path: str
    status: int
    summary: str
    client_ip: str


class ScenarioResponse(BaseModel):
    scenario_id: str
    scenario_name: str
    scenario_desc: str
    satellite_system_ids: list[str]


@dataclass
class Satellite:
    satellite_id: str
    name: str
    type: SatelliteType
    status: SatelliteStatus
    system_id: str | None = None
    eng_model: str | None = None
    domain: str | None = None
    resolution_perf: str | None = None
    baseline_status: str | None = None
    primary_mission: str | None = None


@dataclass
class GroundStation:
    ground_station_id: str
    name: str
    type: GroundStationType
    status: GroundStationStatus
    location: str | None
    ground_station_alias_id: str | None = None


@dataclass
class Requestor:
    requestor_id: str
    name: str
    ground_station_id: str


@dataclass(frozen=True)
class SatelliteTypeProfile:
    platform: str
    orbit_type: str
    nominal_altitude_km: int
    nominal_swath_km: int
    revisit_hours: int
    sensor_modes: list[str]
    default_product_type: str
    default_bands_or_polarization: list[str]


@dataclass
class Command:
    command_id: str
    satellite_id: str
    mission_name: str
    aoi_name: str
    width: int
    height: int
    cloud_percent: int
    fail_probability: float
    request_profile: dict[str, Any] = field(default_factory=dict)
    state: CommandState = CommandState.QUEUED
    message: str | None = None
    image_path: Path | None = None
    acquisition_metadata: dict[str, Any] | None = None
    product_metadata: dict[str, Any] | None = None
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    def update_state(self, state: CommandState, message: str | None = None) -> None:
        self.state = state
        self.message = message
        self.updated_at = datetime.now(UTC)


app = FastAPI(title="K-Sattie Sky Hub", version="0.3.0")
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

satellites: dict[str, Satellite] = {}
ground_stations: dict[str, GroundStation] = {}
requestors: dict[str, Requestor] = {}
commands: dict[str, Command] = {}
store_lock = threading.Lock()
rate_lock = threading.Lock()
rate_buckets: dict[str, deque[float]] = defaultdict(deque)
api_logs_lock = threading.Lock()
api_call_logs: deque[dict[str, Any]] = deque()
API_LOG_LIMIT = int(os.getenv("SATTI_API_LOG_LIMIT", "1000"))

API_KEY_HEADER = "x-api-key"
API_KEY = os.getenv("SATTI_API_KEY", "change-me")
RATE_LIMIT_PER_MIN = int(os.getenv("SATTI_RATE_LIMIT_PER_MIN", "600"))
ALLOWED_ORIGINS = [
    origin.strip()
    for origin in os.getenv(
        "SATTI_ALLOWED_ORIGINS",
        "http://localhost:6005,http://127.0.0.1:6005,http://localhost:6002,http://127.0.0.1:6002",
    ).split(",")
    if origin.strip()
]
LOCAL_ORIGIN_REGEX = os.getenv(
    "SATTI_ALLOWED_ORIGIN_REGEX",
    r"^https?://(localhost|127\.0\.0\.1)(:\d+)?$",
)

PUBLIC_PATHS = {
    "/",
    "/health",
    "/docs",
    "/redoc",
    "/openapi.json",
}


app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_origin_regex=LOCAL_ORIGIN_REGEX,
    allow_credentials=False,
    allow_methods=["GET", "POST", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["Content-Type", API_KEY_HEADER],
)

SATELLITE_TYPE_PROFILES: dict[SatelliteType, SatelliteTypeProfile] = {
    SatelliteType.EO_OPTICAL: SatelliteTypeProfile(
        platform="Sun-synchronous LEO",
        orbit_type="SSO",
        nominal_altitude_km=500,
        nominal_swath_km=24,
        revisit_hours=24,
        sensor_modes=["NADIR", "OFF_NADIR"],
        default_product_type="L1B_ORTHOREADY",
        default_bands_or_polarization=["R", "G", "B", "NIR"],
    ),
    SatelliteType.SAR: SatelliteTypeProfile(
        platform="Low Earth Orbit radar",
        orbit_type="LEO",
        nominal_altitude_km=550,
        nominal_swath_km=30,
        revisit_hours=12,
        sensor_modes=["SPOTLIGHT", "STRIPMAP"],
        default_product_type="GRD",
        default_bands_or_polarization=["VV", "VH"],
    ),
}


def eng_model_to_satellite_id(eng_model: str) -> str:
    value = re.sub(r"[^A-Za-z0-9]+", "-", eng_model.strip().upper())
    value = re.sub(r"-{2,}", "-", value).strip("-")
    return value or f"SAT-{uuid.uuid4().hex[:8].upper()}"

SATELLITE_BASELINES: list[dict[str, str]] = [
    {
        "system_id": "K-EO-01",
        "kor_name": "아리랑 3호",
        "eng_model": "KOMPSAT-3",
        "domain": "EO",
        "resolution_perf": "0.7m optical",
        "baseline_status": "운용 중",
        "primary_mission": "국토 관리 및 지구 정밀 관측",
    },
    {
        "system_id": "K-EO-02",
        "kor_name": "아리랑 3A호",
        "eng_model": "KOMPSAT-3A",
        "domain": "EO/IR",
        "resolution_perf": "0.55m optical+IR",
        "baseline_status": "운용 중",
        "primary_mission": "야간 관측 및 열원 탐지",
    },
    {
        "system_id": "K-EO-03",
        "kor_name": "아리랑 7호",
        "eng_model": "KOMPSAT-7",
        "domain": "EO",
        "resolution_perf": "0.3m optical",
        "baseline_status": "운용 초기",
        "primary_mission": "초고해상도 촬영",
    },
    {
        "system_id": "K-SAR-01",
        "kor_name": "아리랑 5호",
        "eng_model": "KOMPSAT-5",
        "domain": "SAR",
        "resolution_perf": "1.0m radar",
        "baseline_status": "운용 중",
        "primary_mission": "전천후 지형 관측",
    },
    {
        "system_id": "K-SAR-02",
        "kor_name": "아리랑 6호",
        "eng_model": "KOMPSAT-6",
        "domain": "SAR",
        "resolution_perf": "0.5m radar",
        "baseline_status": "발사 대기",
        "primary_mission": "정밀 전천후 감시",
    },
    {
        "system_id": "K-GEO-01",
        "kor_name": "천리안 2A호",
        "eng_model": "GK-2A",
        "domain": "GEO-EO",
        "resolution_perf": "기상/우주기상",
        "baseline_status": "운용 중",
        "primary_mission": "24시간 실시간 기상 예보 및 감시",
    },
    {
        "system_id": "K-GEO-02",
        "kor_name": "천리안 2B호",
        "eng_model": "GK-2B",
        "domain": "GEO-EO",
        "resolution_perf": "해양/대기환경",
        "baseline_status": "운용 중",
        "primary_mission": "미세먼지 이동 경로 및 해양 환경",
    },
    {
        "system_id": "MIL-425-01",
        "kor_name": "군 정찰위성 1호",
        "eng_model": "425 Project #1",
        "domain": "EO/IR",
        "resolution_perf": "0.3m class",
        "baseline_status": "전력화 완료",
        "primary_mission": "대북 전략 표적 주간 정밀 감시",
    },
    {
        "system_id": "MIL-425-02",
        "kor_name": "군 정찰위성 2호",
        "eng_model": "425 Project #2",
        "domain": "SAR",
        "resolution_perf": "0.5m class",
        "baseline_status": "전력화 완료",
        "primary_mission": "전천후 대북 감시",
    },
    {
        "system_id": "MIL-425-03",
        "kor_name": "군 정찰위성 3호",
        "eng_model": "425 Project #3",
        "domain": "SAR",
        "resolution_perf": "0.5m class",
        "baseline_status": "운용 중",
        "primary_mission": "군집 감시망 구성",
    },
    {
        "system_id": "MIL-425-04",
        "kor_name": "군 정찰위성 4호",
        "eng_model": "425 Project #4",
        "domain": "SAR",
        "resolution_perf": "0.5m class",
        "baseline_status": "운용 중",
        "primary_mission": "군집 감시망 구성 (재방문 주기 단축)",
    },
    {
        "system_id": "MIL-425-05",
        "kor_name": "군 정찰위성 5호",
        "eng_model": "425 Project #5",
        "domain": "SAR",
        "resolution_perf": "0.5m class",
        "baseline_status": "운용 초기",
        "primary_mission": "2025.11 발사 성공, 체계 완성",
    },
    {
        "system_id": "K-CAS-01",
        "kor_name": "차세대 중형 1호",
        "eng_model": "CAS500-1",
        "domain": "EO",
        "resolution_perf": "0.5m optical",
        "baseline_status": "운용 중",
        "primary_mission": "국토 자원 관리",
    },
    {
        "system_id": "K-CAS-02",
        "kor_name": "차세대 중형 2호",
        "eng_model": "CAS500-2",
        "domain": "EO",
        "resolution_perf": "0.5m optical",
        "baseline_status": "발사 예정",
        "primary_mission": "2026년 상반기 발사 (재난 대응)",
    },
    {
        "system_id": "K-NEON",
        "kor_name": "초소형 군집위성",
        "eng_model": "NEONSAT",
        "domain": "EO",
        "resolution_perf": "1.0m class constellation",
        "baseline_status": "확장 중",
        "primary_mission": "고빈도 재방문 관측",
    },
]

SCENARIO_BASELINES: list[dict[str, Any]] = [
    {
        "scenario_id": "SCN-001",
        "scenario_name": "국토 정사영상 갱신",
        "scenario_desc": "K-EO-01, K-CAS-01 기반 정사영상 주기 갱신",
        "satellite_system_ids": ["KOMPSAT-3", "CAS500-1"],
    },
    {
        "scenario_id": "SCN-002",
        "scenario_name": "야간 산불 열원 탐지",
        "scenario_desc": "K-EO-02 IR 야간 열원 탐지",
        "satellite_system_ids": ["KOMPSAT-3A"],
    },
    {
        "scenario_id": "SCN-003",
        "scenario_name": "도시변화 탐지",
        "scenario_desc": "K-EO-03 초고해상도 변화 분석",
        "satellite_system_ids": ["KOMPSAT-7"],
    },
    {
        "scenario_id": "SCN-004",
        "scenario_name": "홍수지역 SAR 판독",
        "scenario_desc": "K-SAR-01 장마철 침수 분석",
        "satellite_system_ids": ["KOMPSAT-5"],
    },
    {
        "scenario_id": "SCN-005",
        "scenario_name": "정밀 레이더 표적 재식별",
        "scenario_desc": "K-SAR-02 정밀 SAR 표적 재식별",
        "satellite_system_ids": ["KOMPSAT-6"],
    },
    {
        "scenario_id": "SCN-006",
        "scenario_name": "태풍 실황 추적",
        "scenario_desc": "K-GEO-01 기상 연속 감시",
        "satellite_system_ids": ["GK-2A"],
    },
    {
        "scenario_id": "SCN-007",
        "scenario_name": "미세먼지 이동 추적",
        "scenario_desc": "K-GEO-02 대기/해양 환경 감시",
        "satellite_system_ids": ["GK-2B"],
    },
    {
        "scenario_id": "SCN-008",
        "scenario_name": "전략표적 EO/IR 감시",
        "scenario_desc": "MIL-425-01 주간 정밀 감시",
        "satellite_system_ids": ["425-PROJECT-1"],
    },
    {
        "scenario_id": "SCN-009",
        "scenario_name": "악천후 표적 감시",
        "scenario_desc": "MIL-425-02 SAR 감시",
        "satellite_system_ids": ["425-PROJECT-2"],
    },
    {
        "scenario_id": "SCN-010",
        "scenario_name": "SAR 군집 재방문 감시",
        "scenario_desc": "MIL-425-03/04/05 군집 감시",
        "satellite_system_ids": ["425-PROJECT-3", "425-PROJECT-4", "425-PROJECT-5"],
    },
    {
        "scenario_id": "SCN-011",
        "scenario_name": "재난 대응 표준 관측",
        "scenario_desc": "K-CAS-02 재난 대응",
        "satellite_system_ids": ["CAS500-2"],
    },
    {
        "scenario_id": "SCN-012",
        "scenario_name": "초소형 군집 모니터링",
        "scenario_desc": "K-NEON 고빈도 관측",
        "satellite_system_ids": ["NEONSAT"],
    },
]

LEGACY_NAME_TO_SYSTEM_ID: dict[str, str] = {
    "KOMPSAT-3 (Arirang-3)": "KOMPSAT-3",
    "KOMPSAT-3A (Arirang-3A)": "KOMPSAT-3A",
    "CAS500-1 (NextSat-1)": "CAS500-1",
    "Cheollian-2B (GEO-KOMPSAT-2B)": "GK-2B",
    "KOMPSAT-5 (Arirang-5, SAR)": "KOMPSAT-5",
    "KOMPSAT-6 (Arirang-6, SAR)": "KOMPSAT-6",
}

SATELLITE_PUBLIC_ID_BY_BASELINE_CODE: dict[str, str] = {
    row["system_id"]: eng_model_to_satellite_id(row["eng_model"]) for row in SATELLITE_BASELINES
}
BASELINE_BY_PUBLIC_ID: dict[str, dict[str, str]] = {
    eng_model_to_satellite_id(row["eng_model"]): row for row in SATELLITE_BASELINES
}


def db_connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def ensure_db_schema() -> None:
    with db_connect() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS satellites (
                internal_satellite_code TEXT PRIMARY KEY,
                satellite_id TEXT NOT NULL UNIQUE,
                name TEXT NOT NULL,
                type TEXT NOT NULL,
                status TEXT NOT NULL,
                eng_model TEXT,
                domain TEXT,
                resolution_perf TEXT,
                baseline_status TEXT,
                primary_mission TEXT
            );

            CREATE TABLE IF NOT EXISTS ground_stations (
                internal_ground_station_code TEXT PRIMARY KEY,
                ground_station_id TEXT NOT NULL UNIQUE,
                name TEXT NOT NULL,
                type TEXT NOT NULL,
                status TEXT NOT NULL,
                location TEXT
            );

            CREATE TABLE IF NOT EXISTS requestors (
                requestor_id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                internal_ground_station_code TEXT NOT NULL,
                FOREIGN KEY (internal_ground_station_code)
                    REFERENCES ground_stations (internal_ground_station_code)
                    ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS commands (
                command_id TEXT PRIMARY KEY,
                satellite_id TEXT NOT NULL,
                mission_name TEXT NOT NULL,
                aoi_name TEXT NOT NULL,
                width INTEGER NOT NULL,
                height INTEGER NOT NULL,
                cloud_percent INTEGER NOT NULL,
                fail_probability REAL NOT NULL,
                state TEXT NOT NULL,
                message TEXT,
                image_path TEXT,
                request_profile_json TEXT NOT NULL,
                acquisition_metadata_json TEXT,
                product_metadata_json TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            """
        )
        conn.commit()


def parse_iso_z(value: str | None) -> datetime:
    if not value:
        return datetime.now(UTC)
    text = value.replace("Z", "+00:00")
    dt = datetime.fromisoformat(text)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt


def persist_satellites_locked(conn: sqlite3.Connection) -> None:
    conn.execute("DELETE FROM satellites")
    rows = [
        (
            sat.satellite_id,
            satellite_public_id(sat),
            sat.name,
            sat.type.value,
            sat.status.value,
            sat.eng_model,
            sat.domain,
            sat.resolution_perf,
            sat.baseline_status,
            sat.primary_mission,
        )
        for sat in satellites.values()
    ]
    conn.executemany(
        """
        INSERT INTO satellites (
            internal_satellite_code, satellite_id, name, type, status,
            eng_model, domain, resolution_perf, baseline_status, primary_mission
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        rows,
    )


def persist_ground_stations_locked(conn: sqlite3.Connection) -> None:
    conn.execute("DELETE FROM ground_stations")
    rows = [
        (
            station.ground_station_id,
            ground_station_public_id(station),
            station.name,
            station.type.value,
            station.status.value,
            station.location,
        )
        for station in ground_stations.values()
    ]
    conn.executemany(
        """
        INSERT INTO ground_stations (
            internal_ground_station_code, ground_station_id, name, type, status, location
        ) VALUES (?, ?, ?, ?, ?, ?)
        """,
        rows,
    )


def persist_requestors_locked(conn: sqlite3.Connection) -> None:
    conn.execute("DELETE FROM requestors")
    rows = [
        (requestor.requestor_id, requestor.name, requestor.ground_station_id)
        for requestor in requestors.values()
    ]
    conn.executemany(
        """
        INSERT INTO requestors (requestor_id, name, internal_ground_station_code)
        VALUES (?, ?, ?)
        """,
        rows,
    )


def persist_commands_locked(conn: sqlite3.Connection) -> None:
    conn.execute("DELETE FROM commands")
    rows = []
    for command in commands.values():
        rows.append(
            (
                command.command_id,
                command.satellite_id,
                command.mission_name,
                command.aoi_name,
                command.width,
                command.height,
                command.cloud_percent,
                command.fail_probability,
                command.state.value,
                command.message,
                str(command.image_path) if command.image_path is not None else None,
                json.dumps(command.request_profile or {}, ensure_ascii=False),
                json.dumps(command.acquisition_metadata, ensure_ascii=False) if command.acquisition_metadata is not None else None,
                json.dumps(command.product_metadata, ensure_ascii=False) if command.product_metadata is not None else None,
                now_iso(command.created_at),
                now_iso(command.updated_at),
            )
        )
    conn.executemany(
        """
        INSERT INTO commands (
            command_id, satellite_id, mission_name, aoi_name, width, height, cloud_percent,
            fail_probability, state, message, image_path, request_profile_json,
            acquisition_metadata_json, product_metadata_json, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        rows,
    )


def persist_all_locked() -> None:
    ensure_db_schema()
    with db_connect() as conn:
        persist_satellites_locked(conn)
        persist_ground_stations_locked(conn)
        persist_requestors_locked(conn)
        persist_commands_locked(conn)
        conn.commit()


def load_state_from_db_locked() -> None:
    satellites.clear()
    ground_stations.clear()
    requestors.clear()
    commands.clear()

    with db_connect() as conn:
        sat_rows = conn.execute(
            """
            SELECT internal_satellite_code, satellite_id, name, type, status,
                   eng_model, domain, resolution_perf, baseline_status, primary_mission
            FROM satellites
            """
        ).fetchall()
        for row in sat_rows:
            satellites[row["internal_satellite_code"]] = Satellite(
                satellite_id=row["internal_satellite_code"],
                system_id=row["satellite_id"],
                name=row["name"],
                type=SatelliteType(row["type"]),
                status=SatelliteStatus(row["status"]),
                eng_model=row["eng_model"],
                domain=row["domain"],
                resolution_perf=row["resolution_perf"],
                baseline_status=row["baseline_status"],
                primary_mission=row["primary_mission"],
            )

        station_rows = conn.execute(
            """
            SELECT internal_ground_station_code, ground_station_id, name, type, status, location
            FROM ground_stations
            """
        ).fetchall()
        for row in station_rows:
            ground_stations[row["internal_ground_station_code"]] = GroundStation(
                ground_station_id=row["internal_ground_station_code"],
                ground_station_alias_id=row["ground_station_id"],
                name=row["name"],
                type=GroundStationType(row["type"]),
                status=GroundStationStatus(row["status"]),
                location=row["location"],
            )

        requestor_rows = conn.execute(
            """
            SELECT requestor_id, name, internal_ground_station_code
            FROM requestors
            """
        ).fetchall()
        for row in requestor_rows:
            requestors[row["requestor_id"]] = Requestor(
                requestor_id=row["requestor_id"],
                name=row["name"],
                ground_station_id=row["internal_ground_station_code"],
            )

        command_rows = conn.execute(
            """
            SELECT command_id, satellite_id, mission_name, aoi_name, width, height,
                   cloud_percent, fail_probability, state, message, image_path,
                   request_profile_json, acquisition_metadata_json, product_metadata_json,
                   created_at, updated_at
            FROM commands
            """
        ).fetchall()
        for row in command_rows:
            request_profile = json.loads(row["request_profile_json"]) if row["request_profile_json"] else {}
            acquisition = json.loads(row["acquisition_metadata_json"]) if row["acquisition_metadata_json"] else None
            product = json.loads(row["product_metadata_json"]) if row["product_metadata_json"] else None
            commands[row["command_id"]] = Command(
                command_id=row["command_id"],
                satellite_id=row["satellite_id"],
                mission_name=row["mission_name"],
                aoi_name=row["aoi_name"],
                width=int(row["width"]),
                height=int(row["height"]),
                cloud_percent=int(row["cloud_percent"]),
                fail_probability=float(row["fail_probability"]),
                request_profile=request_profile,
                state=CommandState(row["state"]),
                message=row["message"],
                image_path=Path(row["image_path"]) if row["image_path"] else None,
                acquisition_metadata=acquisition,
                product_metadata=product,
                created_at=parse_iso_z(row["created_at"]),
                updated_at=parse_iso_z(row["updated_at"]),
            )

    ensure_satellite_public_ids_locked()
    ensure_ground_station_public_ids_locked()


def bootstrap_store_from_db_or_seed_locked() -> None:
    db_preexisted = DB_PATH.exists() and DB_PATH.stat().st_size > 0
    ensure_db_schema()
    if db_preexisted:
        load_state_from_db_locked()
        return
    seed_default_satellites_locked()
    seed_default_ground_stations_locked()
    seed_default_requestors_locked()
    persist_all_locked()


def clear_db_and_reset_locked() -> ClearDbResponse:
    deleted_satellites = len(satellites)
    deleted_ground_stations = len(ground_stations)
    deleted_requestors = len(requestors)
    deleted_commands = len(commands)
    deleted_images = 0

    for pattern in ("*.png", "*.jpg", "*.jpeg", "*.webp"):
        for image_file in IMAGE_DIR.glob(pattern):
            try:
                image_file.unlink()
                deleted_images += 1
            except FileNotFoundError:
                continue

    satellites.clear()
    ground_stations.clear()
    requestors.clear()
    commands.clear()
    with api_logs_lock:
        api_call_logs.clear()

    seeded_satellites = len(seed_default_satellites_locked())
    seeded_ground_stations = len(seed_default_ground_stations_locked())
    seeded_requestors = len(seed_default_requestors_locked())
    persist_all_locked()

    return ClearDbResponse(
        deleted_satellites=deleted_satellites,
        deleted_ground_stations=deleted_ground_stations,
        deleted_requestors=deleted_requestors,
        deleted_commands=deleted_commands,
        deleted_images=deleted_images,
        seeded_satellites=seeded_satellites,
        seeded_ground_stations=seeded_ground_stations,
        seeded_requestors=seeded_requestors,
        message="SQLite DB reset completed. Seed data restored and generated images removed.",
    )


@app.middleware("http")
async def auth_and_rate_limit(request: Request, call_next):
    path = request.url.path
    path_with_query = str(request.url.path)
    if request.url.query:
        path_with_query = f"{path_with_query}?{request.url.query}"
    client_ip = request.client.host if request.client else "unknown"
    user_agent = request.headers.get("user-agent", "")

    def append_api_call(status: int, summary: str) -> None:
        if path.startswith("/static") or path == "/monitor/api-calls":
            return
        entry = {
            "time": now_iso(datetime.now(UTC)),
            "method": request.method,
            "path": path_with_query,
            "status": status,
            "summary": summary,
            "client_ip": client_ip,
        }
        with api_logs_lock:
            api_call_logs.appendleft(entry)
            while len(api_call_logs) > API_LOG_LIMIT:
                api_call_logs.pop()

    if request.method == "OPTIONS":
        return await call_next(request)

    # Protect all operational APIs by default, except explicit public paths.
    if path not in PUBLIC_PATHS and not path.startswith("/static"):
        # Browser download links cannot attach custom headers easily.
        # Allow api_key query only for download endpoint.
        api_key = request.headers.get(API_KEY_HEADER, "")
        if path.startswith("/downloads/") and not api_key:
            api_key = request.query_params.get("api_key", "")
        if api_key != API_KEY:
            append_api_call(401, "Unauthorized")
            return JSONResponse(status_code=401, content={"detail": "Unauthorized"})

    # Simple fixed-window-ish limiter per client IP.
    # Set SATTI_RATE_LIMIT_PER_MIN<=0 to disable the limiter.
    if RATE_LIMIT_PER_MIN > 0:
        now_ts = time.time()
        with rate_lock:
            bucket = rate_buckets[client_ip]
            while bucket and now_ts - bucket[0] > 60:
                bucket.popleft()
            if len(bucket) >= RATE_LIMIT_PER_MIN:
                append_api_call(429, "Too Many Requests")
                return JSONResponse(status_code=429, content={"detail": "Too Many Requests"})
            bucket.append(now_ts)
    try:
        response = await call_next(request)
    except Exception:
        append_api_call(500, f"Unhandled Error | UA: {user_agent[:120]}")
        raise
    append_api_call(response.status_code, f"OK | UA: {user_agent[:120]}")
    return response


def seed_default_satellites_locked() -> list[str]:
    seeded_ids: list[str] = []
    ensure_satellite_public_ids_locked()
    for baseline in SATELLITE_BASELINES:
        public_satellite_id = eng_model_to_satellite_id(baseline["eng_model"])
        domain = baseline["domain"]
        name = f'{baseline["kor_name"]} ({baseline["eng_model"]})'
        sat_type = SatelliteType.SAR if domain == "SAR" else SatelliteType.EO_OPTICAL
        baseline_status = baseline["baseline_status"]
        exists = any(sat.system_id == public_satellite_id for sat in satellites.values())
        if exists:
            continue
        sat_id = f"sat-{uuid.uuid4().hex[:8]}"
        satellites[sat_id] = Satellite(
            satellite_id=sat_id,
            name=name,
            type=sat_type,
            status=SatelliteStatus.AVAILABLE,
            system_id=public_satellite_id,
            eng_model=baseline["eng_model"],
            domain=domain,
            resolution_perf=baseline["resolution_perf"],
            baseline_status=baseline_status,
            primary_mission=baseline["primary_mission"],
        )
        seeded_ids.append(public_satellite_id)
    return seeded_ids


def seed_default_ground_stations_locked() -> list[str]:
    seeded_ids: list[str] = []
    ensure_ground_station_public_ids_locked()
    presets = [
        ("Daejeon Mission Control Ground Station", GroundStationType.FIXED, "Daejeon"),
        ("Jeju Maritime Satellite Ground Station", GroundStationType.MARITIME, "Jeju"),
        ("Incheon Airborne Relay Ground Station", GroundStationType.AIRBORNE, "Incheon"),
    ]
    for name, station_type, location in presets:
        exists = any(station.name == name for station in ground_stations.values())
        if exists:
            continue
        used_public_ids = {ground_station_public_id(st) for st in ground_stations.values()}
        desired_alias = build_ground_station_alias(name, location)
        alias_id = make_unique_id(desired_alias, used_public_ids)
        station_id = f"gnd-{uuid.uuid4().hex[:8]}"
        ground_stations[station_id] = GroundStation(
            ground_station_id=station_id,
            ground_station_alias_id=alias_id,
            name=name,
            type=station_type,
            status=GroundStationStatus.OPERATIONAL,
            location=location,
        )
        seeded_ids.append(alias_id)
    return seeded_ids


def seed_default_requestors_locked() -> list[str]:
    seeded_ids: list[str] = []
    ensure_ground_station_public_ids_locked()
    preset_by_station_keyword = {
        "Daejeon": ["Daejeon Requestor Alpha", "Daejeon Requestor Bravo"],
        "Jeju": ["Jeju Requestor Alpha", "Jeju Requestor Bravo"],
        "Incheon": ["Incheon Requestor Alpha", "Incheon Requestor Bravo"],
    }
    for station in ground_stations.values():
        names = next((v for k, v in preset_by_station_keyword.items() if k in station.name), None)
        if names is None:
            names = [f"{station.name} Requestor Alpha", f"{station.name} Requestor Bravo"]
        for req_name in names:
            exists = any(
                requestor.name == req_name and requestor.ground_station_id == station.ground_station_id
                for requestor in requestors.values()
            )
            if exists:
                continue
            requestor_id = f"req-{uuid.uuid4().hex[:8]}"
            requestors[requestor_id] = Requestor(
                requestor_id=requestor_id,
                name=req_name,
                ground_station_id=station.ground_station_id,
            )
            seeded_ids.append(requestor_id)
    return seeded_ids


def now_iso(dt: datetime) -> str:
    return dt.isoformat().replace("+00:00", "Z")


def normalize_entity_name(name: str) -> str:
    return " ".join(name.strip().split()).casefold()


def normalize_id_token(value: str) -> str:
    token = re.sub(r"[^A-Za-z0-9]+", "", value.upper())
    return token


def make_unique_id(base: str, used_ids: set[str]) -> str:
    if base not in used_ids:
        return base
    i = 2
    while f"{base}-{i}" in used_ids:
        i += 1
    return f"{base}-{i}"


def build_ground_station_alias(name: str, location: str | None) -> str:
    location_token = normalize_id_token(location or "")
    region = (location_token[:3] if location_token else normalize_id_token(name)[:3]) or "GND"

    words = re.findall(r"[A-Za-z0-9]+", name.upper())
    stopwords = {"GROUND", "STATION", "SATELLITE", "BASE", "CENTER"}
    filtered = [w for w in words if w not in stopwords]
    if location_token and filtered and filtered[0] == location_token:
        filtered = filtered[1:]
    initials = "".join(w[0] for w in filtered[:4]) or "GS"
    return f"{region}-{initials}"


def satellite_public_id(sat: Satellite) -> str:
    return sat.system_id or sat.satellite_id


def get_satellite_and_internal_key_by_public_id(public_id: str) -> tuple[str | None, Satellite | None]:
    for internal_id, sat in satellites.items():
        if satellite_public_id(sat) == public_id:
            return internal_id, sat
    return None, None


def get_satellite_by_public_id(public_id: str) -> Satellite | None:
    _, sat = get_satellite_and_internal_key_by_public_id(public_id)
    return sat


def ensure_satellite_public_ids_locked() -> None:
    used_public_ids: set[str] = set()
    for sat in satellites.values():
        if sat.system_id:
            remapped_public = SATELLITE_PUBLIC_ID_BY_BASELINE_CODE.get(sat.system_id)
            if remapped_public and remapped_public not in used_public_ids:
                sat.system_id = remapped_public
            used_public_ids.add(sat.system_id)

    for sat in satellites.values():
        if sat.system_id:
            continue

        mapped_public_id = LEGACY_NAME_TO_SYSTEM_ID.get(sat.name)
        if mapped_public_id and mapped_public_id not in used_public_ids:
            baseline = BASELINE_BY_PUBLIC_ID.get(mapped_public_id)
            if baseline:
                sat.system_id = mapped_public_id
                sat.eng_model = baseline["eng_model"]
                sat.domain = baseline["domain"]
                sat.resolution_perf = baseline["resolution_perf"]
                sat.baseline_status = baseline["baseline_status"]
                sat.primary_mission = baseline["primary_mission"]
                sat.name = f'{baseline["kor_name"]} ({baseline["eng_model"]})'
                used_public_ids.add(mapped_public_id)
                continue

        fallback = f"USR-{uuid.uuid4().hex[:8].upper()}"
        while fallback in used_public_ids:
            fallback = f"USR-{uuid.uuid4().hex[:8].upper()}"
        sat.system_id = fallback
        used_public_ids.add(fallback)


def ground_station_public_id(station: GroundStation) -> str:
    return station.ground_station_alias_id or station.ground_station_id


def get_ground_station_and_internal_key_by_public_id(public_id: str) -> tuple[str | None, GroundStation | None]:
    for internal_id, station in ground_stations.items():
        if ground_station_public_id(station) == public_id:
            return internal_id, station
    return None, None


def get_ground_station_by_public_id(public_id: str) -> GroundStation | None:
    _, station = get_ground_station_and_internal_key_by_public_id(public_id)
    return station


def ensure_ground_station_public_ids_locked() -> None:
    used_public_ids: set[str] = set()
    # Keep explicit non-legacy aliases.
    for station in ground_stations.values():
        alias = station.ground_station_alias_id
        if alias and not re.fullmatch(r"K-GND-\d+", alias):
            if alias not in used_public_ids:
                used_public_ids.add(alias)
            else:
                station.ground_station_alias_id = None

    for station in ground_stations.values():
        if station.ground_station_alias_id and station.ground_station_alias_id in used_public_ids:
            continue
        desired = build_ground_station_alias(station.name, station.location)
        alias = make_unique_id(desired, used_public_ids)
        station.ground_station_alias_id = alias
        used_public_ids.add(alias)


def satellite_name_exists_locked(name: str, *, exclude_internal_id: str | None = None) -> bool:
    normalized = normalize_entity_name(name)
    for internal_id, sat in satellites.items():
        if exclude_internal_id is not None and internal_id == exclude_internal_id:
            continue
        if normalize_entity_name(sat.name) == normalized:
            return True
    return False


def ground_station_name_exists_locked(name: str, *, exclude_internal_id: str | None = None) -> bool:
    normalized = normalize_entity_name(name)
    for internal_id, station in ground_stations.items():
        if exclude_internal_id is not None and internal_id == exclude_internal_id:
            continue
        if normalize_entity_name(station.name) == normalized:
            return True
    return False


def random_hex_color() -> tuple[int, int, int]:
    return random.randint(0, 255), random.randint(0, 255), random.randint(0, 255)


def generate_optical_image(command: Command, output_path: Path) -> None:
    width, height = command.width, command.height
    img = Image.new("RGB", (width, height))
    px = img.load()

    c1 = random_hex_color()
    c2 = random_hex_color()
    c3 = random_hex_color()

    for y in range(height):
        t = y / max(1, height - 1)
        for x in range(width):
            s = x / max(1, width - 1)
            r = int((1 - t) * c1[0] + t * c2[0] * (0.6 + 0.4 * s)) % 256
            g = int((1 - s) * c2[1] + s * c3[1] * (0.6 + 0.4 * t)) % 256
            b = int((1 - t) * c3[2] + t * c1[2] * (0.6 + 0.4 * s)) % 256
            px[x, y] = (r, g, b)

    cloud_samples = int((width * height) * (command.cloud_percent / 100.0) * 0.03)
    for _ in range(cloud_samples):
        x = random.randint(0, width - 1)
        y = random.randint(0, height - 1)
        cloud = random.randint(190, 255)
        px[x, y] = (cloud, cloud, cloud)

    img.save(output_path, format="PNG")


def generate_sar_image(command: Command, output_path: Path) -> None:
    width, height = command.width, command.height
    img = Image.new("L", (width, height))
    px = img.load()

    for y in range(height):
        base = int(70 + (185 * y / max(1, height - 1)))
        for x in range(width):
            speckle = random.randint(-45, 45)
            v = max(0, min(255, base + speckle))
            px[x, y] = v

    img.save(output_path, format="PNG")


def latlon_to_tile(lat: float, lon: float, zoom: int) -> tuple[float, float]:
    lat = max(-85.05112878, min(85.05112878, lat))
    n = 2**zoom
    x = (lon + 180.0) / 360.0 * n
    lat_rad = math.radians(lat)
    y = (1.0 - math.log(math.tan(lat_rad) + 1 / math.cos(lat_rad)) / math.pi) / 2.0 * n
    return x, y


def derive_center_from_request_profile(request_profile: dict[str, Any]) -> tuple[float, float]:
    center = request_profile.get("aoi_center")
    if center and center.get("lat") is not None and center.get("lon") is not None:
        return float(center["lat"]), float(center["lon"])

    bbox = request_profile.get("aoi_bbox")
    if bbox and len(bbox) == 4:
        min_lon, min_lat, max_lon, max_lat = bbox
        return (float(min_lat) + float(max_lat)) / 2.0, (float(min_lon) + float(max_lon)) / 2.0

    raise ValueError("External generation requires AOI center or bbox")


def fetch_tile_osm(zoom: int, x: int, y: int) -> Image.Image:
    n = 2**zoom
    wrapped_x = x % n
    clamped_y = max(0, min(n - 1, y))
    url = f"https://tile.openstreetmap.org/{zoom}/{wrapped_x}/{clamped_y}.png"
    req = url_request.Request(
        url,
        headers={
            "User-Agent": "k-sattie-sky-hub/0.2 (+https://localhost; contact: local-dev)",
        },
    )
    with url_request.urlopen(req, timeout=8) as resp:
        raw = resp.read()
    return Image.open(BytesIO(raw)).convert("RGB")


def build_external_map_image(
    *,
    center_lat: float,
    center_lon: float,
    zoom: int,
    width: int,
    height: int,
    map_source: str = "OSM",
) -> Image.Image:
    if map_source != "OSM":
        raise ValueError(f"Unsupported external_map_source: {map_source}")

    tile_x_f, tile_y_f = latlon_to_tile(center_lat, center_lon, zoom)
    tile_x = int(tile_x_f)
    tile_y = int(tile_y_f)

    # Build a 3x3 tile mosaic around center, then crop at center and resize to target.
    mosaic = Image.new("RGB", (256 * 3, 256 * 3))
    for dy in range(-1, 2):
        for dx in range(-1, 2):
            try:
                tile_img = fetch_tile_osm(zoom, tile_x + dx, tile_y + dy)
            except (url_error.URLError, TimeoutError) as exc:
                raise ValueError(f"External map tile fetch failed: {exc}") from exc
            mosaic.paste(tile_img, ((dx + 1) * 256, (dy + 1) * 256))

    px = int((tile_x_f - tile_x) * 256) + 256
    py = int((tile_y_f - tile_y) * 256) + 256
    half = 256
    left = max(0, px - half)
    top = max(0, py - half)
    right = min(mosaic.width, px + half)
    bottom = min(mosaic.height, py + half)
    cropped = mosaic.crop((left, top, right, bottom))
    final = cropped.resize((width, height), Image.Resampling.BILINEAR)
    return final


def generate_external_map_image(command: Command, output_path: Path) -> None:
    request_profile = command.request_profile or {}
    generation = request_profile.get("generation") or {}
    map_source = generation.get("external_map_source", "OSM")
    zoom = int(generation.get("external_map_zoom", 16))
    center_lat, center_lon = derive_center_from_request_profile(request_profile)
    final = build_external_map_image(
        center_lat=center_lat,
        center_lon=center_lon,
        zoom=zoom,
        width=command.width,
        height=command.height,
        map_source=map_source,
    )
    final.save(output_path, format="PNG")


def build_mock_metadata(sat: Satellite, command: Command) -> tuple[dict[str, Any], dict[str, Any]]:
    profile = SATELLITE_TYPE_PROFILES[sat.type]
    capture_at = datetime.now(UTC)

    if sat.type == SatelliteType.EO_OPTICAL:
        acquisition = {
            "captured_at": now_iso(capture_at),
            "sensor_mode": random.choice(profile.sensor_modes),
            "off_nadir_deg": round(random.uniform(2.0, 28.0), 2),
            "sun_elevation_deg": round(random.uniform(20.0, 65.0), 2),
            "cloud_cover_percent": command.cloud_percent,
            "ground_track": random.choice(["ASCENDING", "DESCENDING"]),
            "aoi_name": command.aoi_name,
            "aoi_center": command.request_profile.get("aoi_center"),
            "aoi_bbox": command.request_profile.get("aoi_bbox"),
            "generation_mode": command.request_profile.get("generation", {}).get("mode", "INTERNAL"),
        }
        product = {
            "product_type": profile.default_product_type,
            "bands": profile.default_bands_or_polarization,
            "gsd_m": round(random.uniform(0.5, 1.5), 2),
            "width_px": command.width,
            "height_px": command.height,
            "bit_depth": 8,
            "format": "PNG",
            "image_source": command.request_profile.get("generation", {}),
        }
        return acquisition, product

    acquisition = {
        "captured_at": now_iso(capture_at),
        "sensor_mode": random.choice(profile.sensor_modes),
        "incidence_angle_deg": round(random.uniform(20.0, 45.0), 2),
        "look_side": random.choice(["LEFT", "RIGHT"]),
        "pass_direction": random.choice(["ASCENDING", "DESCENDING"]),
        "polarization": random.choice(profile.default_bands_or_polarization),
        "aoi_name": command.aoi_name,
        "aoi_center": command.request_profile.get("aoi_center"),
        "aoi_bbox": command.request_profile.get("aoi_bbox"),
        "generation_mode": command.request_profile.get("generation", {}).get("mode", "INTERNAL"),
    }
    product = {
        "product_type": profile.default_product_type,
        "resolution_m": round(random.uniform(0.8, 3.0), 2),
        "width_px": command.width,
        "height_px": command.height,
        "format": "PNG",
        "speckle_filter": random.choice(["NONE", "LEE_3x3"]),
        "image_source": command.request_profile.get("generation", {}),
    }
    return acquisition, product


def satellite_to_dict(sat: Satellite) -> dict[str, Any]:
    profile = SATELLITE_TYPE_PROFILES[sat.type]
    return {
        "satellite_id": satellite_public_id(sat),
        "internal_satellite_code": sat.satellite_id,
        "name": sat.name,
        "eng_model": sat.eng_model,
        "domain": sat.domain,
        "resolution_perf": sat.resolution_perf,
        "baseline_status": sat.baseline_status,
        "primary_mission": sat.primary_mission,
        "type": sat.type,
        "status": sat.status,
        "profile": {
            "platform": profile.platform,
            "orbit_type": profile.orbit_type,
            "nominal_altitude_km": profile.nominal_altitude_km,
            "nominal_swath_km": profile.nominal_swath_km,
            "revisit_hours": profile.revisit_hours,
            "sensor_modes": profile.sensor_modes,
            "default_product_type": profile.default_product_type,
            "default_bands_or_polarization": profile.default_bands_or_polarization,
        },
    }


def ground_station_to_dict(station: GroundStation) -> dict[str, Any]:
    return {
        "ground_station_id": ground_station_public_id(station),
        "internal_ground_station_code": station.ground_station_id,
        "name": station.name,
        "type": station.type,
        "status": station.status,
        "location": station.location,
    }


def requestor_to_dict(requestor: Requestor) -> dict[str, Any]:
    station = ground_stations.get(requestor.ground_station_id)
    return {
        "requestor_id": requestor.requestor_id,
        "name": requestor.name,
        "ground_station_id": ground_station_public_id(station) if station else requestor.ground_station_id,
        "ground_station_name": station.name if station else None,
    }


def run_pipeline(command_id: str) -> None:
    with store_lock:
        command = commands[command_id]
        sat = get_satellite_by_public_id(command.satellite_id)
        if sat is None:
            command.update_state(CommandState.FAILED, "Satellite not found")
            persist_all_locked()
            return
        if sat.status != SatelliteStatus.AVAILABLE:
            command.update_state(CommandState.FAILED, "Satellite is not available")
            persist_all_locked()
            return
        command.update_state(CommandState.QUEUED, "Queued for next contact window")
        persist_all_locked()

    # Simulate waiting for a contact window before uplink ACK.
    time.sleep(random.uniform(0.7, 1.8))

    with store_lock:
        command.update_state(CommandState.ACKED, "Uplink ACK received from satellite")
        persist_all_locked()

    # Simulate command validation/prep on satellite side.
    time.sleep(random.uniform(0.6, 1.6))

    if random.random() < (command.fail_probability * 0.6):
        with store_lock:
            command.update_state(CommandState.FAILED, "Uplink transmission failed")
            persist_all_locked()
        return

    with store_lock:
        command.update_state(CommandState.CAPTURING, "Satellite is capturing image")
        persist_all_locked()

    # Simulate capture duration.
    time.sleep(random.uniform(1.5, 3.8))

    if random.random() < (command.fail_probability * 0.4):
        with store_lock:
            command.update_state(CommandState.FAILED, "Capture aborted due to onboard condition")
            persist_all_locked()
        return

    output_path = IMAGE_DIR / f"{command.command_id}.png"
    try:
        generation_mode = (command.request_profile.get("generation", {}) or {}).get("mode", "INTERNAL")
        if generation_mode == GenerationMode.EXTERNAL.value:
            generate_external_map_image(command, output_path)
        elif sat.type == SatelliteType.EO_OPTICAL:
            generate_optical_image(command, output_path)
        else:
            generate_sar_image(command, output_path)

        with store_lock:
            command.image_path = output_path
            acquisition, product = build_mock_metadata(sat, command)
            command.acquisition_metadata = acquisition
            command.product_metadata = product
            command.update_state(CommandState.DOWNLINK_READY, "Image downlinked and ready")
            persist_all_locked()
    except Exception as exc:
        with store_lock:
            command.update_state(CommandState.FAILED, f"Post-capture pipeline failed: {exc}")
            persist_all_locked()
        return


def build_command_status(command: Command) -> CommandStatusResponse:
    sat = get_satellite_by_public_id(command.satellite_id)
    if sat is None:
        raise HTTPException(status_code=404, detail="Satellite not found")

    station = command.request_profile.get("ground_station") or {}
    requestor = command.request_profile.get("requestor") or {}
    has_file = command.image_path is not None and command.image_path.exists()
    download_url = f"/downloads/{command.command_id}" if command.state == CommandState.DOWNLINK_READY and has_file else None

    return CommandStatusResponse(
        command_id=command.command_id,
        satellite_id=command.satellite_id,
        satellite_type=sat.type,
        ground_station_id=station.get("ground_station_id"),
        ground_station_name=station.get("name"),
        ground_station_type=station.get("type"),
        requestor_id=requestor.get("requestor_id"),
        requestor_name=requestor.get("name"),
        mission_name=command.mission_name,
        aoi_name=command.aoi_name,
        width=command.width,
        height=command.height,
        cloud_percent=command.cloud_percent,
        fail_probability=command.fail_probability,
        state=command.state,
        message=command.message,
        created_at=now_iso(command.created_at),
        updated_at=now_iso(command.updated_at),
        download_url=download_url,
        request_profile=command.request_profile,
        acquisition_metadata=command.acquisition_metadata,
        product_metadata=command.product_metadata,
    )


def reconcile_downlink_integrity(command: Command) -> bool:
    """Normalize inconsistent command state when downlink file is missing."""
    if command.state != CommandState.DOWNLINK_READY:
        return False
    if command.image_path is not None and command.image_path.exists():
        return False
    command.image_path = None
    command.update_state(CommandState.FAILED, "Downlink image file missing. Retry is required.")
    persist_all_locked()
    return True


def ensure_generation_profile_for_rerun(command: Command) -> None:
    """Preserve original generation mode/options when rerunning a command."""
    generation = command.request_profile.get("generation")
    if not isinstance(generation, dict):
        generation = {}

    mode = generation.get("mode")
    if mode not in {GenerationMode.INTERNAL.value, GenerationMode.EXTERNAL.value}:
        prior_mode = None
        if isinstance(command.acquisition_metadata, dict):
            prior_mode = command.acquisition_metadata.get("generation_mode")
        if prior_mode not in {GenerationMode.INTERNAL.value, GenerationMode.EXTERNAL.value}:
            image_source = (command.product_metadata or {}).get("image_source", {})
            if isinstance(image_source, dict):
                prior_mode = image_source.get("mode")
        mode = prior_mode if prior_mode in {GenerationMode.INTERNAL.value, GenerationMode.EXTERNAL.value} else GenerationMode.INTERNAL.value

    source = generation.get("external_map_source")
    if source not in {ExternalMapSource.OSM.value}:
        source = ExternalMapSource.OSM.value

    zoom_raw = generation.get("external_map_zoom")
    try:
        zoom = int(zoom_raw)
    except (TypeError, ValueError):
        zoom = 16
    zoom = max(1, min(19, zoom))

    command.request_profile["generation"] = {
        "mode": mode,
        "external_map_source": source,
        "external_map_zoom": zoom,
    }


@app.get("/health")
def health() -> dict[Literal["status"], str]:
    return {"status": "ok"}


@app.get("/monitor/api-calls", response_model=list[ApiCallLogEntryResponse])
def get_api_call_logs(limit: int = Query(default=100, ge=1, le=500)) -> list[ApiCallLogEntryResponse]:
    with api_logs_lock:
        rows = list(api_call_logs)[:limit]
    return [ApiCallLogEntryResponse(**row) for row in rows]


@app.get("/", include_in_schema=False)
def console_index() -> FileResponse:
    if not CONSOLE_FILE.exists():
        raise HTTPException(status_code=404, detail="Console page not found")
    return FileResponse(CONSOLE_FILE)


@app.post("/satellites")
def create_satellite(req: CreateSatelliteRequest) -> dict[str, str]:
    sat_id = f"sat-{uuid.uuid4().hex[:8]}"
    public_id = (req.satellite_id or req.system_id or f"USR-{uuid.uuid4().hex[:8].upper()}").strip()
    with store_lock:
        ensure_satellite_public_ids_locked()
        if any(satellite_public_id(s) == public_id for s in satellites.values()):
            raise HTTPException(status_code=409, detail="Satellite system_id already exists")
        if satellite_name_exists_locked(req.name):
            raise HTTPException(status_code=409, detail="Satellite name already exists")
    sat = Satellite(
        satellite_id=sat_id,
        name=req.name,
        type=req.type,
        status=req.status,
        system_id=public_id,
        eng_model=None,
        domain=None,
        resolution_perf=None,
        baseline_status=None,
        primary_mission=None,
    )
    with store_lock:
        satellites[sat_id] = sat
        persist_all_locked()
    return {"satellite_id": public_id}


@app.patch("/satellites/{satellite_id}", response_model=SatelliteResponse)
def update_satellite(satellite_id: str, req: UpdateSatelliteRequest) -> SatelliteResponse:
    with store_lock:
        ensure_satellite_public_ids_locked()
        internal_id, sat = get_satellite_and_internal_key_by_public_id(satellite_id)
        if sat is None:
            raise HTTPException(status_code=404, detail="Satellite not found")
        if req.name is not None:
            if satellite_name_exists_locked(req.name, exclude_internal_id=internal_id):
                raise HTTPException(status_code=409, detail="Satellite name already exists")
            sat.name = req.name
        if req.type is not None:
            sat.type = req.type
        if req.status is not None:
            sat.status = req.status
        persist_all_locked()
        return SatelliteResponse(**satellite_to_dict(sat))


@app.delete("/satellites/{satellite_id}")
def delete_satellite(satellite_id: str) -> dict[str, str]:
    with store_lock:
        ensure_satellite_public_ids_locked()
        internal_id, sat = get_satellite_and_internal_key_by_public_id(satellite_id)
        if sat is None:
            raise HTTPException(status_code=404, detail="Satellite not found")
        removed_name = sat.name
        if internal_id is None:
            raise HTTPException(status_code=404, detail="Satellite not found")
        del satellites[internal_id]
        persist_all_locked()
    return {"deleted_satellite_id": satellite_id, "deleted_name": removed_name}


@app.post("/ground-stations")
def create_ground_station(req: CreateGroundStationRequest) -> dict[str, str]:
    station_id = f"gnd-{uuid.uuid4().hex[:8]}"
    user_input_id = (req.ground_station_id or "").strip()
    auto_desired_id = build_ground_station_alias(req.name, req.location)
    public_id = user_input_id or auto_desired_id
    station = GroundStation(
        ground_station_id=station_id,
        ground_station_alias_id=public_id,
        name=req.name,
        type=req.type,
        status=req.status,
        location=req.location,
    )
    with store_lock:
        ensure_ground_station_public_ids_locked()
        used_public_ids = {ground_station_public_id(s) for s in ground_stations.values()}
        if user_input_id:
            if user_input_id in used_public_ids:
                raise HTTPException(status_code=409, detail="Ground station id already exists")
        else:
            public_id = make_unique_id(public_id, used_public_ids)
            station.ground_station_alias_id = public_id
        if ground_station_name_exists_locked(req.name):
            raise HTTPException(status_code=409, detail="Ground station name already exists")
        ground_stations[station_id] = station
        persist_all_locked()
    return {"ground_station_id": public_id}


@app.post("/requestors")
def create_requestor(req: CreateRequestorRequest) -> dict[str, str]:
    requestor_id = f"req-{uuid.uuid4().hex[:8]}"
    with store_lock:
        ensure_ground_station_public_ids_locked()
        internal_station_id, station = get_ground_station_and_internal_key_by_public_id(req.ground_station_id)
        if station is None or internal_station_id is None:
            raise HTTPException(status_code=404, detail="Ground station not found")
        requestors[requestor_id] = Requestor(
            requestor_id=requestor_id,
            name=req.name,
            ground_station_id=internal_station_id,
        )
        persist_all_locked()
    return {"requestor_id": requestor_id}


@app.patch("/requestors/{requestor_id}", response_model=RequestorResponse)
def update_requestor(requestor_id: str, req: UpdateRequestorRequest) -> RequestorResponse:
    with store_lock:
        ensure_ground_station_public_ids_locked()
        requestor = requestors.get(requestor_id)
        if requestor is None:
            raise HTTPException(status_code=404, detail="Requestor not found")
        if req.name is not None:
            requestor.name = req.name
        if req.ground_station_id is not None:
            internal_station_id, station = get_ground_station_and_internal_key_by_public_id(req.ground_station_id)
            if station is None or internal_station_id is None:
                raise HTTPException(status_code=404, detail="Ground station not found")
            requestor.ground_station_id = internal_station_id
        persist_all_locked()
        return RequestorResponse(**requestor_to_dict(requestor))


@app.delete("/requestors/{requestor_id}")
def delete_requestor(requestor_id: str) -> dict[str, str]:
    with store_lock:
        requestor = requestors.get(requestor_id)
        if requestor is None:
            raise HTTPException(status_code=404, detail="Requestor not found")
        removed_name = requestor.name
        del requestors[requestor_id]
        persist_all_locked()
    return {"deleted_requestor_id": requestor_id, "deleted_name": removed_name}


@app.patch("/ground-stations/{ground_station_id}", response_model=GroundStationResponse)
def update_ground_station(ground_station_id: str, req: UpdateGroundStationRequest) -> GroundStationResponse:
    with store_lock:
        ensure_ground_station_public_ids_locked()
        internal_id, station = get_ground_station_and_internal_key_by_public_id(ground_station_id)
        if station is None:
            raise HTTPException(status_code=404, detail="Ground station not found")
        if req.name is not None:
            if ground_station_name_exists_locked(req.name, exclude_internal_id=internal_id):
                raise HTTPException(status_code=409, detail="Ground station name already exists")
            station.name = req.name
        if req.status is not None:
            station.status = req.status
        if req.location is not None:
            station.location = req.location
        persist_all_locked()
        return GroundStationResponse(**ground_station_to_dict(station))


@app.delete("/ground-stations/{ground_station_id}")
def delete_ground_station(ground_station_id: str) -> dict[str, str]:
    with store_lock:
        ensure_ground_station_public_ids_locked()
        internal_station_id, station = get_ground_station_and_internal_key_by_public_id(ground_station_id)
        if station is None:
            raise HTTPException(status_code=404, detail="Ground station not found")
        if internal_station_id is None:
            raise HTTPException(status_code=404, detail="Ground station not found")
        removed_name = station.name
        del ground_stations[internal_station_id]
        related_requestor_ids = [
            requestor_id
            for requestor_id, requestor in requestors.items()
            if requestor.ground_station_id == internal_station_id
        ]
        for requestor_id in related_requestor_ids:
            del requestors[requestor_id]
        persist_all_locked()
    return {"deleted_ground_station_id": ground_station_id, "deleted_name": removed_name}


@app.post("/seed/mock-ground-stations", response_model=SeedGroundStationsResponse)
def seed_mock_ground_stations() -> SeedGroundStationsResponse:
    with store_lock:
        seeded_ids = seed_default_ground_stations_locked()
        seed_default_requestors_locked()
        persist_all_locked()
    return SeedGroundStationsResponse(ground_station_ids=seeded_ids)


@app.get("/ground-stations", response_model=list[GroundStationResponse])
def list_ground_stations() -> list[GroundStationResponse]:
    with store_lock:
        ensure_ground_station_public_ids_locked()
        return [GroundStationResponse(**ground_station_to_dict(station)) for station in ground_stations.values()]


@app.post("/seed/mock-requestors", response_model=SeedRequestorsResponse)
def seed_mock_requestors() -> SeedRequestorsResponse:
    with store_lock:
        seeded_ids = seed_default_requestors_locked()
        persist_all_locked()
    return SeedRequestorsResponse(requestor_ids=seeded_ids)


@app.get("/requestors", response_model=list[RequestorResponse])
def list_requestors(
    ground_station_id: str | None = Query(default=None),
) -> list[RequestorResponse]:
    with store_lock:
        ensure_ground_station_public_ids_locked()
        rows = list(requestors.values())
        if ground_station_id is not None:
            internal_station_id, _ = get_ground_station_and_internal_key_by_public_id(ground_station_id)
            if internal_station_id is None:
                rows = []
            else:
                rows = [requestor for requestor in rows if requestor.ground_station_id == internal_station_id]
        return [RequestorResponse(**requestor_to_dict(requestor)) for requestor in rows]


@app.post("/seed/mock-satellites", response_model=SeedSatellitesResponse)
def seed_mock_satellites() -> SeedSatellitesResponse:
    with store_lock:
        seeded_ids = seed_default_satellites_locked()
        persist_all_locked()
    return SeedSatellitesResponse(satellite_ids=seeded_ids)


@app.get("/satellite-types", response_model=dict[SatelliteType, SatelliteTypeProfileResponse])
def list_satellite_types() -> dict[SatelliteType, SatelliteTypeProfileResponse]:
    return {
        sat_type: SatelliteTypeProfileResponse(
            platform=profile.platform,
            orbit_type=profile.orbit_type,
            nominal_altitude_km=profile.nominal_altitude_km,
            nominal_swath_km=profile.nominal_swath_km,
            revisit_hours=profile.revisit_hours,
            sensor_modes=profile.sensor_modes,
            default_product_type=profile.default_product_type,
            default_bands_or_polarization=profile.default_bands_or_polarization,
        )
        for sat_type, profile in SATELLITE_TYPE_PROFILES.items()
    }


@app.get("/scenarios", response_model=list[ScenarioResponse])
def list_scenarios() -> list[ScenarioResponse]:
    return [ScenarioResponse(**scenario) for scenario in SCENARIO_BASELINES]


@app.on_event("startup")
def seed_mock_data_on_startup() -> None:
    with store_lock:
        bootstrap_store_from_db_or_seed_locked()


@app.get("/satellites", response_model=list[SatelliteResponse])
def list_satellites() -> list[SatelliteResponse]:
    with store_lock:
        ensure_satellite_public_ids_locked()
        return [SatelliteResponse(**satellite_to_dict(sat)) for sat in satellites.values()]


@app.post("/uplink", response_model=UplinkCommandResponse)
def uplink_command(req: UplinkCommandRequest) -> UplinkCommandResponse:
    with store_lock:
        ensure_satellite_public_ids_locked()
        ensure_ground_station_public_ids_locked()
        sat = get_satellite_by_public_id(req.satellite_id)
        if sat is None:
            raise HTTPException(status_code=404, detail="Satellite not found")
        station = None
        station_internal_id = None
        requestor = None
        if req.ground_station_id is not None:
            station_internal_id, station = get_ground_station_and_internal_key_by_public_id(req.ground_station_id)
            if station is None:
                raise HTTPException(status_code=404, detail="Ground station not found")
            if station.status != GroundStationStatus.OPERATIONAL:
                raise HTTPException(status_code=409, detail="Ground station is not operational")
        if req.requestor_id is not None:
            requestor = requestors.get(req.requestor_id)
            if requestor is None:
                raise HTTPException(status_code=404, detail="Requestor not found")
            if req.ground_station_id is None:
                raise HTTPException(status_code=409, detail="ground_station_id is required when requestor_id is provided")
            if station_internal_id is None or requestor.ground_station_id != station_internal_id:
                raise HTTPException(status_code=409, detail="Requestor does not belong to selected ground station")

        command_id = f"cmd-{uuid.uuid4().hex[:12]}"
        ground_station_payload = None
        if station is not None:
            ground_station_payload = {
                "ground_station_id": ground_station_public_id(station),
                "name": station.name,
                "type": station.type.value,
                "status": station.status.value,
                "location": station.location,
            }
        requestor_payload = None
        if requestor is not None:
            requestor_payload = {
                "requestor_id": requestor.requestor_id,
                "name": requestor.name,
                "ground_station_id": requestor.ground_station_id,
            }
        request_profile = {
            "ground_station": ground_station_payload,
            "requestor": requestor_payload,
            "aoi_center": (
                {"lat": req.aoi_center_lat, "lon": req.aoi_center_lon}
                if req.aoi_center_lat is not None and req.aoi_center_lon is not None
                else None
            ),
            "aoi_bbox": req.aoi_bbox,
            "window_open_utc": req.window_open_utc,
            "window_close_utc": req.window_close_utc,
            "priority": req.priority.value,
            "eo_constraints": {
                "max_cloud_cover_percent": req.max_cloud_cover_percent,
                "max_off_nadir_deg": req.max_off_nadir_deg,
                "min_sun_elevation_deg": req.min_sun_elevation_deg,
            },
            "sar_constraints": {
                "incidence_min_deg": req.incidence_min_deg,
                "incidence_max_deg": req.incidence_max_deg,
                "look_side": req.look_side.value,
                "pass_direction": req.pass_direction.value,
                "polarization": req.polarization,
            },
            "delivery": {
                "method": req.delivery_method.value,
                "path": req.delivery_path,
            },
            "generation": {
                "mode": req.generation_mode.value,
                "external_map_source": req.external_map_source.value,
                "external_map_zoom": req.external_map_zoom,
            },
        }
        command = Command(
            command_id=command_id,
            satellite_id=req.satellite_id,
            mission_name=req.mission_name,
            aoi_name=req.aoi_name,
            width=req.width,
            height=req.height,
            cloud_percent=req.cloud_percent,
            fail_probability=req.fail_probability,
            request_profile=request_profile,
        )
        commands[command_id] = command
        persist_all_locked()

    t = threading.Thread(target=run_pipeline, args=(command_id,), daemon=True)
    t.start()

    return UplinkCommandResponse(
        command_id=command.command_id,
        state=command.state,
        satellite_id=command.satellite_id,
        satellite_type=sat.type,
        ground_station_id=ground_station_payload["ground_station_id"] if ground_station_payload else None,
        ground_station_name=ground_station_payload["name"] if ground_station_payload else None,
        ground_station_type=ground_station_payload["type"] if ground_station_payload else None,
        requestor_id=requestor_payload["requestor_id"] if requestor_payload else None,
        requestor_name=requestor_payload["name"] if requestor_payload else None,
        mission_name=command.mission_name,
        aoi_name=command.aoi_name,
        created_at=now_iso(command.created_at),
    )


@app.get("/commands", response_model=list[CommandStatusResponse])
def list_commands() -> list[CommandStatusResponse]:
    with store_lock:
        command_list = list(commands.values())
        for command in command_list:
            reconcile_downlink_integrity(command)
        return [build_command_status(command) for command in command_list]


@app.get("/commands/{command_id}", response_model=CommandStatusResponse)
def get_command(command_id: str) -> CommandStatusResponse:
    with store_lock:
        command = commands.get(command_id)
        if command is None:
            raise HTTPException(status_code=404, detail="Command not found")
        reconcile_downlink_integrity(command)
        return build_command_status(command)


@app.post("/commands/{command_id}/rerun", response_model=CommandStatusResponse)
def rerun_command(command_id: str) -> CommandStatusResponse:
    with store_lock:
        command = commands.get(command_id)
        if command is None:
            raise HTTPException(status_code=404, detail="Command not found")

        reconcile_downlink_integrity(command)

        if command.state in {CommandState.QUEUED, CommandState.ACKED, CommandState.CAPTURING}:
            raise HTTPException(status_code=409, detail="Command is already in progress")
        can_rerun = command.state == CommandState.FAILED or (
            command.state == CommandState.DOWNLINK_READY
            and (command.image_path is None or not command.image_path.exists())
        )
        if not can_rerun:
            raise HTTPException(status_code=409, detail="Only FAILED commands can be rerun")

        ensure_generation_profile_for_rerun(command)

        if command.image_path is not None and command.image_path.exists():
            try:
                command.image_path.unlink()
            except OSError:
                pass

        command.image_path = None
        command.acquisition_metadata = None
        command.product_metadata = None
        command.update_state(CommandState.QUEUED, "Re-run requested by operator")
        persist_all_locked()

    t = threading.Thread(target=run_pipeline, args=(command_id,), daemon=True)
    t.start()

    with store_lock:
        return build_command_status(commands[command_id])


@app.get("/downloads/{command_id}")
def download_image(command_id: str) -> FileResponse:
    with store_lock:
        command = commands.get(command_id)
        if command is None:
            raise HTTPException(status_code=404, detail="Command not found")
        reconcile_downlink_integrity(command)
        if command.state != CommandState.DOWNLINK_READY or command.image_path is None:
            raise HTTPException(status_code=409, detail="Image is not ready")
        image_path = command.image_path
    if not image_path.exists():
        with store_lock:
            reconcile_downlink_integrity(command)
        raise HTTPException(status_code=404, detail="Image file not found")

    return FileResponse(
        image_path,
        media_type="image/png",
        filename=f"{command_id}.png",
    )


@app.post("/downloads/{command_id}/save-local", response_model=SaveLocalDownloadResponse)
def save_local_download(command_id: str) -> SaveLocalDownloadResponse:
    with store_lock:
        command = commands.get(command_id)
        if command is None:
            raise HTTPException(status_code=404, detail="Command not found")
        reconcile_downlink_integrity(command)
        if command.state != CommandState.DOWNLINK_READY or command.image_path is None:
            raise HTTPException(status_code=409, detail="Image is not ready")
        image_path = command.image_path

    resolved = image_path.resolve()
    if not resolved.exists():
        with store_lock:
            reconcile_downlink_integrity(command)
        raise HTTPException(status_code=404, detail="Image file not found")

    return SaveLocalDownloadResponse(
        command_id=command_id,
        saved_path=str(resolved),
        file_size_bytes=resolved.stat().st_size,
        message="Image is saved in local data/images directory",
    )


@app.post("/images/clear", response_model=ClearImagesResponse)
def clear_images() -> ClearImagesResponse:
    deleted_count = 0
    cleared_command_count = 0

    for pattern in ("*.png", "*.jpg", "*.jpeg", "*.webp"):
        for image_file in IMAGE_DIR.glob(pattern):
            try:
                image_file.unlink()
                deleted_count += 1
            except FileNotFoundError:
                continue

    with store_lock:
        for command in commands.values():
            if command.image_path is not None:
                command.image_path = None
                if command.state == CommandState.DOWNLINK_READY:
                    command.update_state(CommandState.FAILED, "Image cleared by operator. Retry is required.")
                else:
                    command.message = "Image cleared by operator"
                    command.updated_at = datetime.now(UTC)
                cleared_command_count += 1
        persist_all_locked()

    return ClearImagesResponse(
        deleted_count=deleted_count,
        cleared_command_count=cleared_command_count,
        message="All generated sample images were cleared",
    )


@app.post("/admin/db/clear", response_model=ClearDbResponse)
def clear_db() -> ClearDbResponse:
    with store_lock:
        return clear_db_and_reset_locked()


@app.get("/preview/external-map")
def preview_external_map(
    lat: float = Query(ge=-90, le=90),
    lon: float = Query(ge=-180, le=180),
    zoom: int = Query(default=16, ge=1, le=19),
    width: int = Query(default=768, ge=128, le=4096),
    height: int = Query(default=768, ge=128, le=4096),
    source: ExternalMapSource = Query(default=ExternalMapSource.OSM),
):
    try:
        image = build_external_map_image(
            center_lat=lat,
            center_lon=lon,
            zoom=zoom,
            width=width,
            height=height,
            map_source=source.value,
        )
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"External map preview failed: {exc}") from exc

    output = BytesIO()
    image.save(output, format="PNG")
    output.seek(0)
    return StreamingResponse(output, media_type="image/png")

from __future__ import annotations

import os
import random
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


class UpdateSatelliteRequest(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=100)
    status: SatelliteStatus | None = None


class CreateGroundStationRequest(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    type: GroundStationType
    status: GroundStationStatus = GroundStationStatus.OPERATIONAL
    location: str | None = Field(default=None, max_length=120)


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
        default=19,
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
    name: str
    type: SatelliteType
    status: SatelliteStatus
    profile: SatelliteTypeProfileResponse


class SeedSatellitesResponse(BaseModel):
    satellite_ids: list[str]


class GroundStationResponse(BaseModel):
    ground_station_id: str
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


class ApiCallLogEntryResponse(BaseModel):
    time: str
    method: str
    path: str
    status: int
    summary: str
    client_ip: str


@dataclass
class Satellite:
    satellite_id: str
    name: str
    type: SatelliteType
    status: SatelliteStatus


@dataclass
class GroundStation:
    ground_station_id: str
    name: str
    type: GroundStationType
    status: GroundStationStatus
    location: str | None


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
    for origin in os.getenv("SATTI_ALLOWED_ORIGINS", "http://localhost:6005,http://127.0.0.1:6005").split(",")
    if origin.strip()
]

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
    presets = [
        ("KOMPSAT-3 (Arirang-3)", SatelliteType.EO_OPTICAL),
        ("KOMPSAT-3A (Arirang-3A)", SatelliteType.EO_OPTICAL),
        ("CAS500-1 (NextSat-1)", SatelliteType.EO_OPTICAL),
        ("Cheollian-2B (GEO-KOMPSAT-2B)", SatelliteType.EO_OPTICAL),
        ("KOMPSAT-5 (Arirang-5, SAR)", SatelliteType.SAR),
        ("KOMPSAT-6 (Arirang-6, SAR)", SatelliteType.SAR),
        ("KOMPSAT-Next-5 (C-band SAR)", SatelliteType.SAR),
    ]
    for name, sat_type in presets:
        exists = any(sat.name == name for sat in satellites.values())
        if exists:
            continue
        sat_id = f"sat-{uuid.uuid4().hex[:8]}"
        satellites[sat_id] = Satellite(
            satellite_id=sat_id,
            name=name,
            type=sat_type,
            status=SatelliteStatus.AVAILABLE,
        )
        seeded_ids.append(sat_id)
    return seeded_ids


def seed_default_ground_stations_locked() -> list[str]:
    seeded_ids: list[str] = []
    presets = [
        ("Daejeon Mission Control Ground Station", GroundStationType.FIXED, "Daejeon"),
        ("Jeju Maritime Satellite Ground Station", GroundStationType.MARITIME, "Jeju"),
        ("Incheon Airborne Relay Ground Station", GroundStationType.AIRBORNE, "Incheon"),
    ]
    for name, station_type, location in presets:
        exists = any(station.name == name for station in ground_stations.values())
        if exists:
            continue
        station_id = f"gnd-{uuid.uuid4().hex[:8]}"
        ground_stations[station_id] = GroundStation(
            ground_station_id=station_id,
            name=name,
            type=station_type,
            status=GroundStationStatus.OPERATIONAL,
            location=location,
        )
        seeded_ids.append(station_id)
    return seeded_ids


def seed_default_requestors_locked() -> list[str]:
    seeded_ids: list[str] = []
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
    zoom = int(generation.get("external_map_zoom", 19))
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
        "satellite_id": sat.satellite_id,
        "name": sat.name,
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
        "ground_station_id": station.ground_station_id,
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
        "ground_station_id": requestor.ground_station_id,
        "ground_station_name": station.name if station else None,
    }


def run_pipeline(command_id: str) -> None:
    with store_lock:
        command = commands[command_id]
        sat = satellites.get(command.satellite_id)
        if sat is None:
            command.update_state(CommandState.FAILED, "Satellite not found")
            return
        if sat.status != SatelliteStatus.AVAILABLE:
            command.update_state(CommandState.FAILED, "Satellite is not available")
            return
        command.update_state(CommandState.QUEUED, "Queued for next contact window")

    # Simulate waiting for a contact window before uplink ACK.
    time.sleep(random.uniform(0.7, 1.8))

    with store_lock:
        command.update_state(CommandState.ACKED, "Uplink ACK received from satellite")

    # Simulate command validation/prep on satellite side.
    time.sleep(random.uniform(0.6, 1.6))

    if random.random() < (command.fail_probability * 0.6):
        with store_lock:
            command.update_state(CommandState.FAILED, "Uplink transmission failed")
        return

    with store_lock:
        command.update_state(CommandState.CAPTURING, "Satellite is capturing image")

    # Simulate capture duration.
    time.sleep(random.uniform(1.5, 3.8))

    if random.random() < (command.fail_probability * 0.4):
        with store_lock:
            command.update_state(CommandState.FAILED, "Capture aborted due to onboard condition")
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
    except Exception as exc:
        with store_lock:
            command.update_state(CommandState.FAILED, f"Post-capture pipeline failed: {exc}")
        return


def build_command_status(command: Command) -> CommandStatusResponse:
    sat = satellites.get(command.satellite_id)
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
        zoom = 19
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
    sat = Satellite(
        satellite_id=sat_id,
        name=req.name,
        type=req.type,
        status=req.status,
    )
    with store_lock:
        satellites[sat_id] = sat
    return {"satellite_id": sat_id}


@app.patch("/satellites/{satellite_id}", response_model=SatelliteResponse)
def update_satellite(satellite_id: str, req: UpdateSatelliteRequest) -> SatelliteResponse:
    with store_lock:
        sat = satellites.get(satellite_id)
        if sat is None:
            raise HTTPException(status_code=404, detail="Satellite not found")
        if req.name is not None:
            sat.name = req.name
        if req.status is not None:
            sat.status = req.status
        return SatelliteResponse(**satellite_to_dict(sat))


@app.delete("/satellites/{satellite_id}")
def delete_satellite(satellite_id: str) -> dict[str, str]:
    with store_lock:
        sat = satellites.get(satellite_id)
        if sat is None:
            raise HTTPException(status_code=404, detail="Satellite not found")
        removed_name = sat.name
        del satellites[satellite_id]
    return {"deleted_satellite_id": satellite_id, "deleted_name": removed_name}


@app.post("/ground-stations")
def create_ground_station(req: CreateGroundStationRequest) -> dict[str, str]:
    station_id = f"gnd-{uuid.uuid4().hex[:8]}"
    station = GroundStation(
        ground_station_id=station_id,
        name=req.name,
        type=req.type,
        status=req.status,
        location=req.location,
    )
    with store_lock:
        ground_stations[station_id] = station
    return {"ground_station_id": station_id}


@app.post("/requestors")
def create_requestor(req: CreateRequestorRequest) -> dict[str, str]:
    requestor_id = f"req-{uuid.uuid4().hex[:8]}"
    with store_lock:
        if req.ground_station_id not in ground_stations:
            raise HTTPException(status_code=404, detail="Ground station not found")
        requestors[requestor_id] = Requestor(
            requestor_id=requestor_id,
            name=req.name,
            ground_station_id=req.ground_station_id,
        )
    return {"requestor_id": requestor_id}


@app.patch("/requestors/{requestor_id}", response_model=RequestorResponse)
def update_requestor(requestor_id: str, req: UpdateRequestorRequest) -> RequestorResponse:
    with store_lock:
        requestor = requestors.get(requestor_id)
        if requestor is None:
            raise HTTPException(status_code=404, detail="Requestor not found")
        if req.name is not None:
            requestor.name = req.name
        if req.ground_station_id is not None:
            if req.ground_station_id not in ground_stations:
                raise HTTPException(status_code=404, detail="Ground station not found")
            requestor.ground_station_id = req.ground_station_id
        return RequestorResponse(**requestor_to_dict(requestor))


@app.delete("/requestors/{requestor_id}")
def delete_requestor(requestor_id: str) -> dict[str, str]:
    with store_lock:
        requestor = requestors.get(requestor_id)
        if requestor is None:
            raise HTTPException(status_code=404, detail="Requestor not found")
        removed_name = requestor.name
        del requestors[requestor_id]
    return {"deleted_requestor_id": requestor_id, "deleted_name": removed_name}


@app.patch("/ground-stations/{ground_station_id}", response_model=GroundStationResponse)
def update_ground_station(ground_station_id: str, req: UpdateGroundStationRequest) -> GroundStationResponse:
    with store_lock:
        station = ground_stations.get(ground_station_id)
        if station is None:
            raise HTTPException(status_code=404, detail="Ground station not found")
        if req.name is not None:
            station.name = req.name
        if req.status is not None:
            station.status = req.status
        if req.location is not None:
            station.location = req.location
        return GroundStationResponse(**ground_station_to_dict(station))


@app.delete("/ground-stations/{ground_station_id}")
def delete_ground_station(ground_station_id: str) -> dict[str, str]:
    with store_lock:
        station = ground_stations.get(ground_station_id)
        if station is None:
            raise HTTPException(status_code=404, detail="Ground station not found")
        removed_name = station.name
        del ground_stations[ground_station_id]
        related_requestor_ids = [
            requestor_id
            for requestor_id, requestor in requestors.items()
            if requestor.ground_station_id == ground_station_id
        ]
        for requestor_id in related_requestor_ids:
            del requestors[requestor_id]
    return {"deleted_ground_station_id": ground_station_id, "deleted_name": removed_name}


@app.post("/seed/mock-ground-stations", response_model=SeedGroundStationsResponse)
def seed_mock_ground_stations() -> SeedGroundStationsResponse:
    with store_lock:
        seeded_ids = seed_default_ground_stations_locked()
        seed_default_requestors_locked()
    return SeedGroundStationsResponse(ground_station_ids=seeded_ids)


@app.get("/ground-stations", response_model=list[GroundStationResponse])
def list_ground_stations() -> list[GroundStationResponse]:
    with store_lock:
        return [GroundStationResponse(**ground_station_to_dict(station)) for station in ground_stations.values()]


@app.post("/seed/mock-requestors", response_model=SeedRequestorsResponse)
def seed_mock_requestors() -> SeedRequestorsResponse:
    with store_lock:
        seeded_ids = seed_default_requestors_locked()
    return SeedRequestorsResponse(requestor_ids=seeded_ids)


@app.get("/requestors", response_model=list[RequestorResponse])
def list_requestors(
    ground_station_id: str | None = Query(default=None),
) -> list[RequestorResponse]:
    with store_lock:
        rows = list(requestors.values())
        if ground_station_id is not None:
            rows = [requestor for requestor in rows if requestor.ground_station_id == ground_station_id]
        return [RequestorResponse(**requestor_to_dict(requestor)) for requestor in rows]


@app.post("/seed/mock-satellites", response_model=SeedSatellitesResponse)
def seed_mock_satellites() -> SeedSatellitesResponse:
    with store_lock:
        seeded_ids = seed_default_satellites_locked()
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


@app.get("/satellites", response_model=list[SatelliteResponse])
def list_satellites() -> list[SatelliteResponse]:
    with store_lock:
        return [SatelliteResponse(**satellite_to_dict(sat)) for sat in satellites.values()]


@app.post("/uplink", response_model=UplinkCommandResponse)
def uplink_command(req: UplinkCommandRequest) -> UplinkCommandResponse:
    with store_lock:
        sat = satellites.get(req.satellite_id)
        if sat is None:
            raise HTTPException(status_code=404, detail="Satellite not found")
        station = None
        requestor = None
        if req.ground_station_id is not None:
            station = ground_stations.get(req.ground_station_id)
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
            if requestor.ground_station_id != req.ground_station_id:
                raise HTTPException(status_code=409, detail="Requestor does not belong to selected ground station")

        command_id = f"cmd-{uuid.uuid4().hex[:12]}"
        ground_station_payload = None
        if station is not None:
            ground_station_payload = {
                "ground_station_id": station.ground_station_id,
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

    return ClearImagesResponse(
        deleted_count=deleted_count,
        cleared_command_count=cleared_command_count,
        message="All generated sample images were cleared",
    )


@app.get("/preview/external-map")
def preview_external_map(
    lat: float = Query(ge=-90, le=90),
    lon: float = Query(ge=-180, le=180),
    zoom: int = Query(default=19, ge=1, le=19),
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

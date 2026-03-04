#!/usr/bin/env bash
# -----------------------------------------------------------------------------
# K-Sattie Sky Hub - Batch Job Downlink Helper (Standalone)
#
# Purpose:
# - Execute downlink flow by requestor_id with EXTERNAL generation.
# - OpenMap in this project means EXTERNAL + OSM.
# - This script is standalone (no dependency on other shell scripts).
#
# Usage:
#   ./downlink-batch-job.sh [requestor_id] [options]
#
# Use cases (preflight included):
#   # Case 0) Preflight check only
#   bash ./downlink-batch-job.sh --check
#
#   # Case 1) Default run (auto-pick first requestor)
#   bash ./downlink-batch-job.sh
#
#   # Case 2) Specific requestor
#   bash ./downlink-batch-job.sh req-1234abcd
#
#   # Case 3) Custom center(lat/lon)
#   bash ./downlink-batch-job.sh req-1234abcd --lat 35.1028 --lon 129.0403 --zoom 16
#
#   # Case 4) bbox input
#   bash ./downlink-batch-job.sh --bbox "126.88,37.48,127.08,37.66" --zoom 15
#
#   # Case 5) Production domain
#   bash ./downlink-batch-job.sh \
#     --api-base https://echo.smartspace.co.kr \
#     --api-key <REAL_API_KEY> \
#     --zoom 16
#
#   # Case 6) Debug mode
#   bash ./downlink-batch-job.sh --debug
# -----------------------------------------------------------------------------
set -euo pipefail

REQUESTOR_ID=""
AOI_CENTER_LAT="${AOI_CENTER_LAT:-}"
AOI_CENTER_LON="${AOI_CENTER_LON:-}"
AOI_BBOX="${AOI_BBOX:-}"
EXTERNAL_MAP_ZOOM="${EXTERNAL_MAP_ZOOM:-16}"
API_BASE="${API_BASE:-http://127.0.0.1:6005}"
API_KEY="${API_KEY:-change-me}"
WAIT_TIMEOUT_SEC="${WAIT_TIMEOUT_SEC:-30}"
WAIT_INTERVAL_SEC="${WAIT_INTERVAL_SEC:-1}"
DEBUG="${DEBUG:-0}"
AUTO_SEED="${AUTO_SEED:-1}"
CHECK_ONLY="0"
MISSION_NAME="${MISSION_NAME:-batch-job-operation-by-system}"
AOI_NAME="${AOI_NAME:-openmap-aoi}"
IMG_WIDTH="${IMG_WIDTH:-512}"
IMG_HEIGHT="${IMG_HEIGHT:-512}"
CLOUD_PERCENT="${CLOUD_PERCENT:-10}"
FAIL_PROBABILITY="${FAIL_PROBABILITY:-0}"

# OpenMap mode fixed
GENERATION_MODE="EXTERNAL"
EXTERNAL_MAP_SOURCE="OSM"

usage() {
  cat <<'USAGE'
Usage:
  ./downlink-batch-job.sh [requestor_id] [options]

Options:
  -r, --requestor-id <id>   requestor_id (optional)
      --lat <value>         AOI center latitude
      --lon <value>         AOI center longitude
      --bbox "<v,v,v,v>"    AOI bbox minLon,minLat,maxLon,maxLat
      --zoom <1..19>        external map zoom (default: 16)
      --api-base <url>      API base URL (default: http://127.0.0.1:6005)
      --api-key <value>     x-api-key value (default: change-me)
      --check               run preflight checks only, then exit
      --no-auto-seed        do not call seed APIs when requestors are empty
      --debug               enable debug logs
  -h, --help                show this help
USAGE
}

need_cmd() {
  command -v "$1" >/dev/null 2>&1 || {
    echo "ERROR: required command not found: $1" >&2
    exit 1
  }
}

need_cmd curl
need_cmd python3

while [[ $# -gt 0 ]]; do
  case "$1" in
    -r|--requestor-id)
      REQUESTOR_ID="${2:-}"; shift 2 ;;
    --lat)
      AOI_CENTER_LAT="${2:-}"; shift 2 ;;
    --lon)
      AOI_CENTER_LON="${2:-}"; shift 2 ;;
    --bbox)
      AOI_BBOX="${2:-}"; shift 2 ;;
    --zoom)
      EXTERNAL_MAP_ZOOM="${2:-}"; shift 2 ;;
    --api-base)
      API_BASE="${2:-}"; shift 2 ;;
    --api-key)
      API_KEY="${2:-}"; shift 2 ;;
    --check)
      CHECK_ONLY="1"; shift ;;
    --no-auto-seed)
      AUTO_SEED="0"; shift ;;
    --debug)
      DEBUG="1"; shift ;;
    -h|--help)
      usage; exit 0 ;;
    *)
      if [[ -z "${REQUESTOR_ID}" ]]; then
        REQUESTOR_ID="$1"
        shift
      else
        echo "ERROR: unknown argument: $1" >&2
        usage
        exit 1
      fi
      ;;
  esac
done

API_BASE="${API_BASE%/}"

if [[ "${EXTERNAL_MAP_ZOOM}" =~ ^[0-9]+$ ]]; then
  if (( EXTERNAL_MAP_ZOOM < 1 )); then EXTERNAL_MAP_ZOOM=1; fi
  if (( EXTERNAL_MAP_ZOOM > 19 )); then EXTERNAL_MAP_ZOOM=19; fi
else
  EXTERNAL_MAP_ZOOM=16
fi

api_get_status() {
  local path="$1"
  curl -sS -o /dev/null -w "%{http_code}" "${API_BASE}${path}" \
    -H "x-api-key: ${API_KEY}"
}

api_get_json() {
  local path="$1"
  curl -sS "${API_BASE}${path}" -H "x-api-key: ${API_KEY}"
}

api_post_status() {
  local path="$1"
  curl -sS -o /dev/null -w "%{http_code}" -X POST "${API_BASE}${path}" \
    -H "x-api-key: ${API_KEY}"
}

count_requestors() {
  api_get_json "/requestors" | python3 -c '
import json,sys
try:
    rows=json.load(sys.stdin)
except Exception:
    print(-1); sys.exit(0)
print(len(rows) if isinstance(rows,list) else -1)
'
}

has_requestor_id() {
  local req_id="$1"
  api_get_json "/requestors" | python3 -c '
import json,sys
target=sys.argv[1]
try:
    rows=json.load(sys.stdin)
except Exception:
    print("0"); sys.exit(0)
if not isinstance(rows,list):
    print("0"); sys.exit(0)
print("1" if any(r.get("requestor_id")==target for r in rows) else "0")
' "${req_id}"
}

parse_json_or_fail() {
  local label="$1"
  python3 -c '
import json,sys
label=sys.argv[1]
raw=sys.stdin.read()
try:
    json.loads(raw)
except Exception:
    msg=raw.strip().replace("\n"," ")
    print(f"ERROR: {label} is not valid JSON: {msg[:240]}", file=sys.stderr)
    sys.exit(1)
print(raw, end="")
' "${label}"
}

fetch_json() {
  local path="$1"
  local tmp_body status
  tmp_body="$(mktemp)"
  status="$(
    curl -sS -o "${tmp_body}" -w "%{http_code}" "${API_BASE}${path}" \
      -H "x-api-key: ${API_KEY}"
  )"
  if [[ "${status}" -lt 200 || "${status}" -ge 300 ]]; then
    echo "ERROR: ${path} returned HTTP ${status}" >&2
    echo "ERROR: response: $(head -c 240 "${tmp_body}")" >&2
    rm -f "${tmp_body}"
    return 1
  fi
  cat "${tmp_body}"
  rm -f "${tmp_body}"
}

run_preflight() {
  local health_status requestor_status req_count

  health_status="$(curl -sS -o /dev/null -w "%{http_code}" "${API_BASE}/health" || true)"
  if [[ "${health_status}" != "200" ]]; then
    echo "ERROR: server not reachable: ${API_BASE} (GET /health => ${health_status})" >&2
    echo "TIP: API_BASE/API_KEY 값을 다시 확인하세요." >&2
    exit 1
  fi

  requestor_status="$(api_get_status "/requestors" || true)"
  if [[ "${requestor_status}" == "401" ]]; then
    echo "ERROR: unauthorized. Check API_KEY for ${API_BASE}" >&2
    exit 1
  fi
  if [[ "${requestor_status}" != "200" ]]; then
    echo "ERROR: /requestors returned HTTP ${requestor_status}" >&2
    exit 1
  fi

  req_count="$(count_requestors)"
  if [[ "${req_count}" == "-1" ]]; then
    echo "ERROR: /requestors returned non-JSON response" >&2
    exit 1
  fi

  if [[ "${req_count}" -eq 0 && "${AUTO_SEED}" == "1" ]]; then
    if [[ "${DEBUG}" == "1" ]]; then
      echo "DEBUG: requestors empty -> seeding ground-stations/requestors" >&2
    fi
    api_post_status "/seed/mock-ground-stations" >/dev/null || true
    api_post_status "/seed/mock-requestors" >/dev/null || true
    req_count="$(count_requestors)"
  fi

  if [[ "${req_count}" -le 0 ]]; then
    echo "ERROR: no requestor available. Seed first or run without --no-auto-seed." >&2
    exit 1
  fi

  if [[ -n "${REQUESTOR_ID}" ]]; then
    if [[ "$(has_requestor_id "${REQUESTOR_ID}")" != "1" ]]; then
      echo "ERROR: requestor_id not found: ${REQUESTOR_ID}" >&2
      exit 1
    fi
  fi

  echo "Preflight OK: api=${API_BASE}, requestors=${req_count}, mode=EXTERNAL(OSM), zoom=${EXTERNAL_MAP_ZOOM}" >&2
}

resolve_requestor_if_needed() {
  if [[ -n "${REQUESTOR_ID}" ]]; then
    return
  fi
  REQUESTOR_ID="$({
    fetch_json "/requestors" | parse_json_or_fail "/requestors" | python3 -c '
import json,sys
rows=json.load(sys.stdin)
print(rows[0]["requestor_id"] if isinstance(rows,list) and rows else "")
'
  } || true)"
  if [[ -z "${REQUESTOR_ID}" ]]; then
    echo "ERROR: failed to resolve requestor_id" >&2
    exit 1
  fi
}

build_uplink_payload() {
  local req_id="$1"
  local requestor_json satellites_json ground_station_id satellite_id

  requestor_json="$(fetch_json "/requestors")"
  requestor_json="$(printf '%s' "${requestor_json}" | parse_json_or_fail "/requestors")"

  ground_station_id="$({
    printf '%s' "${requestor_json}" | python3 -c '
import json,sys
rid=sys.argv[1]
rows=json.load(sys.stdin)
if not isinstance(rows, list):
    print("")
    sys.exit(0)
row=next((r for r in rows if r.get("requestor_id")==rid), None)
print((row or {}).get("ground_station_id",""))
' "${req_id}"
  } || true)"

  if [[ -z "${ground_station_id}" ]]; then
    echo "ERROR: requestor_id not found or unmapped to ground station: ${req_id}" >&2
    return 1
  fi

  satellites_json="$(fetch_json "/satellites")"
  satellites_json="$(printf '%s' "${satellites_json}" | parse_json_or_fail "/satellites")"

  satellite_id="$({
    printf '%s' "${satellites_json}" | python3 -c '
import json,sys
rows=json.load(sys.stdin)
if not isinstance(rows, list):
    print("")
    sys.exit(0)
sat=next((s for s in rows if s.get("status")=="AVAILABLE"), None)
print((sat or {}).get("satellite_id",""))
'
  } || true)"

  if [[ -z "${satellite_id}" ]]; then
    echo "ERROR: no AVAILABLE satellite found" >&2
    return 1
  fi

  SATTI_GEN_MODE="${GENERATION_MODE}" \
  SATTI_EXT_SOURCE="${EXTERNAL_MAP_SOURCE}" \
  SATTI_EXT_ZOOM="${EXTERNAL_MAP_ZOOM}" \
  SATTI_AOI_CENTER_LAT="${AOI_CENTER_LAT}" \
  SATTI_AOI_CENTER_LON="${AOI_CENTER_LON}" \
  SATTI_AOI_BBOX="${AOI_BBOX}" \
  SATTI_MISSION_NAME="${MISSION_NAME}" \
  SATTI_AOI_NAME="${AOI_NAME}" \
  SATTI_IMG_WIDTH="${IMG_WIDTH}" \
  SATTI_IMG_HEIGHT="${IMG_HEIGHT}" \
  SATTI_CLOUD_PERCENT="${CLOUD_PERCENT}" \
  SATTI_FAIL_PROBABILITY="${FAIL_PROBABILITY}" \
  python3 -c '
import json,os,sys

sat_id,gs_id,req_id=sys.argv[1],sys.argv[2],sys.argv[3]
mode="EXTERNAL"
ext_source="OSM"
try:
    ext_zoom=int(os.getenv("SATTI_EXT_ZOOM","16") or 16)
except Exception:
    ext_zoom=16
ext_zoom=max(1,min(19,ext_zoom))

mission_name=os.getenv("SATTI_MISSION_NAME","batch-job-operation-by-system")
aoi_name=os.getenv("SATTI_AOI_NAME","openmap-aoi")
try:
    width=int(os.getenv("SATTI_IMG_WIDTH","512") or 512)
except Exception:
    width=512
try:
    height=int(os.getenv("SATTI_IMG_HEIGHT","512") or 512)
except Exception:
    height=512
try:
    cloud=int(os.getenv("SATTI_CLOUD_PERCENT","10") or 10)
except Exception:
    cloud=10
try:
    fail=float(os.getenv("SATTI_FAIL_PROBABILITY","0") or 0)
except Exception:
    fail=0.0

lat_raw=(os.getenv("SATTI_AOI_CENTER_LAT","") or "").strip()
lon_raw=(os.getenv("SATTI_AOI_CENTER_LON","") or "").strip()
bbox_raw=(os.getenv("SATTI_AOI_BBOX","") or "").strip()

lat=None
lon=None
if lat_raw and lon_raw:
    try:
        lat=float(lat_raw); lon=float(lon_raw)
    except Exception:
        lat=None; lon=None

bbox=None
if bbox_raw:
    try:
        parts=[float(x.strip()) for x in bbox_raw.split(",")]
        if len(parts)==4:
            bbox=parts
    except Exception:
        bbox=None

if lat is None and lon is None and bbox is None:
    lat=37.5665
    lon=126.9780

print(json.dumps({
  "satellite_id": sat_id,
  "ground_station_id": gs_id,
  "requestor_id": req_id,
  "mission_name": mission_name,
  "aoi_name": aoi_name,
  "aoi_center_lat": lat,
  "aoi_center_lon": lon,
  "aoi_bbox": bbox,
  "width": width,
  "height": height,
  "cloud_percent": cloud,
  "generation_mode": mode,
  "external_map_source": ext_source,
  "external_map_zoom": ext_zoom,
  "fail_probability": fail
}, ensure_ascii=False))
' "${satellite_id}" "${ground_station_id}" "${req_id}"
}

create_uplink_for_requestor() {
  local req_id="$1"
  local payload resp cmd_id status tmp_body

  payload="$(build_uplink_payload "${req_id}")" || return 1
  if [[ -z "${payload//[[:space:]]/}" ]]; then
    echo "ERROR: generated uplink payload is empty" >&2
    return 1
  fi

  if [[ "${DEBUG}" == "1" ]]; then
    echo "DEBUG: uplink payload=${payload}" >&2
  fi

  tmp_body="$(mktemp)"
  status="$(
    curl -sS -L --post301 --post302 --post303 \
      -o "${tmp_body}" -w "%{http_code}" -X POST "${API_BASE}/uplink" \
      -H "x-api-key: ${API_KEY}" \
      -H "Content-Type: application/json" \
      --data-binary "${payload}"
  )"
  resp="$(cat "${tmp_body}")"
  rm -f "${tmp_body}"

  if [[ "${DEBUG}" == "1" ]]; then
    echo "DEBUG: /uplink status=${status}" >&2
    echo "DEBUG: /uplink response=${resp}" >&2
  fi

  printf '%s' "${resp}" | parse_json_or_fail "/uplink" >/dev/null
  cmd_id="$(printf '%s' "${resp}" | python3 -c '
import json,sys
obj=json.load(sys.stdin)
print(obj.get("command_id",""))
')"

  if [[ -z "${cmd_id}" ]]; then
    echo "ERROR: uplink creation failed (HTTP ${status}): ${resp}" >&2
    return 1
  fi

  printf '%s' "${cmd_id}"
}

read_command_state() {
  local cmd_id="$1"
  fetch_json "/commands/${cmd_id}" | parse_json_or_fail "/commands/{id}" | python3 -c '
import json,sys
obj=json.load(sys.stdin)
print(obj.get("state",""))
'
}

run_preflight
if [[ "${CHECK_ONLY}" == "1" ]]; then
  echo "Check completed. No uplink/downlink executed."
  exit 0
fi

resolve_requestor_if_needed

CMD_ID="$(create_uplink_for_requestor "${REQUESTOR_ID}")" || exit 1
CMD_STATE="QUEUED"

if [[ "${CMD_STATE}" != "DOWNLINK_READY" ]]; then
  waited=0
  while [[ "${waited}" -lt "${WAIT_TIMEOUT_SEC}" ]]; do
    sleep "${WAIT_INTERVAL_SEC}"
    waited=$(( waited + WAIT_INTERVAL_SEC ))
    CMD_STATE="$(read_command_state "${CMD_ID}")"
    if [[ "${CMD_STATE}" == "DOWNLINK_READY" ]]; then
      break
    fi
    if [[ "${CMD_STATE}" == "FAILED" ]]; then
      echo "ERROR: latest command failed for requestor_id=${REQUESTOR_ID} (command_id=${CMD_ID})" >&2
      exit 1
    fi
  done
fi

if [[ "${CMD_STATE}" != "DOWNLINK_READY" ]]; then
  echo "ERROR: timeout waiting for DOWNLINK_READY for requestor_id=${REQUESTOR_ID} (latest=${CMD_ID}, state=${CMD_STATE})" >&2
  exit 1
fi

OUT_FILE="downlink_${REQUESTOR_ID}_${CMD_ID}.png"
curl -sS -L "${API_BASE}/downloads/${CMD_ID}" \
  -H "x-api-key: ${API_KEY}" \
  -o "${OUT_FILE}"

echo "requestor_id=${REQUESTOR_ID}"
echo "command_id=${CMD_ID}"
echo "state=${CMD_STATE}"
echo "saved=${OUT_FILE}"

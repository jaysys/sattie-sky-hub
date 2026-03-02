#!/usr/bin/env bash
# -----------------------------------------------------------------------------
# K-Sattie Sky Hub - Downlink Helper (by requestor_id)
#
# What this script does:
# 1) Validates/loads requestor_id
# 2) Creates a NEW uplink command every run (always fresh command)
# 3) Polls command state until DOWNLINK_READY (or timeout/FAILED)
# 4) Downloads PNG downlink image to local file
#
# Usage:
#   ./downlink-by-requestor.sh <requestor_id>
#   ./downlink-by-requestor.sh                 # auto-picks first requestor
#
# Examples:
#   # Case 1) INTERNAL 기본값으로 실행
#   API_BASE=http://127.0.0.1:6005 API_KEY=change-me ./downlink-by-requestor.sh req-60389ddc
#
#   # Case 2) EXTERNAL + 기본 좌표(서울 37.5665,126.9780) 사용
#   GENERATION_MODE=EXTERNAL API_BASE=http://127.0.0.1:6005 API_KEY=change-me ./downlink-by-requestor.sh req-60389ddc
#
#   # Case 3) EXTERNAL + 사용자 좌표 지정
#   GENERATION_MODE=EXTERNAL AOI_CENTER_LAT=35.1028 AOI_CENTER_LON=129.0403 EXTERNAL_MAP_ZOOM=15 \
#   API_BASE=http://127.0.0.1:6005 API_KEY=change-me ./downlink-by-requestor.sh req-60389ddc
#
#   # Case 4) EXTERNAL + bbox 지정 (center 미지정 가능)
#   GENERATION_MODE=EXTERNAL AOI_BBOX="126.88,37.48,127.08,37.66" EXTERNAL_MAP_ZOOM=13 \
#   API_BASE=http://127.0.0.1:6005 API_KEY=change-me ./downlink-by-requestor.sh req-60389ddc
#
#   # 로컬 검증 (HTTP)
#   DEBUG=1 GENERATION_MODE=EXTERNAL WAIT_TIMEOUT_SEC=60 WAIT_INTERVAL_SEC=2 \
#   API_BASE=http://127.0.0.1:6005 API_KEY=change-me \
#   ./downlink-by-requestor.sh
#
#   # 원격 도메인 검증 (HTTPS)
#   DEBUG=1 GENERATION_MODE=EXTERNAL WAIT_TIMEOUT_SEC=60 WAIT_INTERVAL_SEC=2 \
#   API_BASE=https://echo.smartspace.co.kr API_KEY=change-me \
#   ./downlink-by-requestor.sh req-99580fd0
#
# Environment variables:
#   API_BASE          API base URL (default: http://127.0.0.1:6005)
#   API_KEY           x-api-key value (default: change-me)
#   WAIT_TIMEOUT_SEC  Max wait seconds for DOWNLINK_READY (default: 30)
#   WAIT_INTERVAL_SEC Poll interval seconds (default: 1)
#   DEBUG             1이면 uplink payload/응답 디버그 출력 (default: 0)
#   GENERATION_MODE   INTERNAL|EXTERNAL (default: INTERNAL)
#   EXTERNAL_MAP_SOURCE EXTERNAL 모드 지도 소스 (default: OSM)
#   EXTERNAL_MAP_ZOOM EXTERNAL 모드 zoom (default: 19)
#   AOI_CENTER_LAT/LON AOI 중심좌표(미입력 + EXTERNAL이면 서울 기본값 자동 사용)
#   AOI_BBOX          AOI bbox 문자열 "minLon,minLat,maxLon,maxLat" (optional)
#   MISSION_NAME      임무명 (default: auto-downlink-by-requestor)
#   AOI_NAME          AOI 이름 (default: auto-aoi)
#   IMG_WIDTH/IMG_HEIGHT 이미지 크기 (default: 512/512)
#   CLOUD_PERCENT     운량 (default: 10)
#   FAIL_PROBABILITY  실패확률 (default: 0)
#
# Output:
#   downlink_<requestor_id>_<command_id>.png
#
# Exit behavior:
#   - exits non-zero if requestor/satellite not found
#   - exits non-zero on FAILED state
#   - exits non-zero on timeout before DOWNLINK_READY
# -----------------------------------------------------------------------------
set -euo pipefail

API_BASE="${API_BASE:-http://127.0.0.1:6005}"
API_KEY="${API_KEY:-change-me}"
REQ_ID="${1:-}"
WAIT_TIMEOUT_SEC="${WAIT_TIMEOUT_SEC:-30}"
WAIT_INTERVAL_SEC="${WAIT_INTERVAL_SEC:-1}"
DEBUG="${DEBUG:-0}"
GENERATION_MODE="${GENERATION_MODE:-INTERNAL}"
EXTERNAL_MAP_SOURCE="${EXTERNAL_MAP_SOURCE:-OSM}"
EXTERNAL_MAP_ZOOM="${EXTERNAL_MAP_ZOOM:-19}"
AOI_CENTER_LAT="${AOI_CENTER_LAT:-}"
AOI_CENTER_LON="${AOI_CENTER_LON:-}"
AOI_BBOX="${AOI_BBOX:-}"
MISSION_NAME="${MISSION_NAME:-auto-downlink-by-requestor}"
AOI_NAME="${AOI_NAME:-auto-aoi}"
IMG_WIDTH="${IMG_WIDTH:-512}"
IMG_HEIGHT="${IMG_HEIGHT:-512}"
CLOUD_PERCENT="${CLOUD_PERCENT:-10}"
FAIL_PROBABILITY="${FAIL_PROBABILITY:-0}"

need_cmd() {
  command -v "$1" >/dev/null 2>&1 || {
    echo "ERROR: required command not found: $1" >&2
    exit 1
  }
}

need_cmd curl
need_cmd python3

API_BASE="${API_BASE%/}"

fetch_json() {
  local path="$1"
  local tmp_body status
  tmp_body="$(mktemp)"
  status="$(
    curl -sSL -o "${tmp_body}" -w "%{http_code}" "${API_BASE}${path}" \
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

if [[ -z "${REQ_ID}" ]]; then
  REQ_ID="$(
    fetch_json "/requestors" | parse_json_or_fail "/requestors" | python3 -c '
import json,sys
rows=json.load(sys.stdin)
if isinstance(rows, dict):
    print("")
    sys.exit(0)
print(rows[0]["requestor_id"] if rows else "")
'
  )"
fi

if [[ -z "${REQ_ID}" ]]; then
  echo "ERROR: no requestor found. Run setup first: POST /seed/mock-requestors" >&2
  exit 1
fi

build_uplink_payload() {
  local req_id="$1"
  local requestor_json satellites_json ground_station_id satellite_id

  requestor_json="$(fetch_json "/requestors")"
  requestor_json="$(printf '%s' "${requestor_json}" | parse_json_or_fail "/requestors")"
  ground_station_id="$(
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
  )"
  if [[ -z "${ground_station_id}" ]]; then
    echo "ERROR: requestor_id not found or unmapped to ground station: ${req_id}" >&2
    return 1
  fi

  satellites_json="$(fetch_json "/satellites")"
  satellites_json="$(printf '%s' "${satellites_json}" | parse_json_or_fail "/satellites")"
  satellite_id="$(
    printf '%s' "${satellites_json}" | python3 -c '
import json,sys
rows=json.load(sys.stdin)
if not isinstance(rows, list):
    print("")
    sys.exit(0)
sat=next((s for s in rows if s.get("status")=="AVAILABLE"), None)
print((sat or {}).get("satellite_id",""))
'
  )"
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
mode=(os.getenv("SATTI_GEN_MODE","INTERNAL") or "INTERNAL").upper()
if mode not in ("INTERNAL","EXTERNAL"):
    mode="INTERNAL"
ext_source=(os.getenv("SATTI_EXT_SOURCE","OSM") or "OSM").upper()
if ext_source not in ("OSM",):
    ext_source="OSM"
try:
    ext_zoom=int(os.getenv("SATTI_EXT_ZOOM","19") or 19)
except Exception:
    ext_zoom=19
ext_zoom=max(1,min(19,ext_zoom))

mission_name=os.getenv("SATTI_MISSION_NAME","auto-downlink-by-requestor")
aoi_name=os.getenv("SATTI_AOI_NAME","auto-aoi")
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

# EXTERNAL requires AOI center or bbox; use Seoul center as fallback.
if mode=="EXTERNAL" and lat is None and lon is None and bbox is None:
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
  cmd_id="$(
    printf '%s' "${resp}" | python3 -c '
import json,sys
try:
    obj=json.load(sys.stdin)
except Exception:
    print("")
    raise
print(obj.get("command_id",""))
'
  )"
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

CMD_ID="$(create_uplink_for_requestor "${REQ_ID}")" || exit 1
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
      echo "ERROR: latest command failed for requestor_id=${REQ_ID} (command_id=${CMD_ID})" >&2
      exit 1
    fi
  done
fi

if [[ "${CMD_STATE}" != "DOWNLINK_READY" ]]; then
  echo "ERROR: timeout waiting for DOWNLINK_READY for requestor_id=${REQ_ID} (latest=${CMD_ID}, state=${CMD_STATE})" >&2
  exit 1
fi

OUT_FILE="downlink_${REQ_ID}_${CMD_ID}.png"
curl -sS -L "${API_BASE}/downloads/${CMD_ID}" \
  -H "x-api-key: ${API_KEY}" \
  -o "${OUT_FILE}"

echo "requestor_id=${REQ_ID}"
echo "command_id=${CMD_ID}"
echo "state=${CMD_STATE}"
echo "saved=${OUT_FILE}"

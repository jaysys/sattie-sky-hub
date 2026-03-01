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
#   API_BASE=http://127.0.0.1:6005 API_KEY=change-me ./downlink-by-requestor.sh req-60389ddc
#
#   WAIT_TIMEOUT_SEC=60 WAIT_INTERVAL_SEC=2 API_BASE=http://127.0.0.1:6005 API_KEY=change-me ./downlink-by-requestor.sh req-60389ddc
#
#   WAIT_TIMEOUT_SEC=60 WAIT_INTERVAL_SEC=2 API_BASE=http://127.0.0.1:6005 API_KEY=change-me ./downlink-by-requestor.sh req-60389ddc
#
# Environment variables:
#   API_BASE          API base URL (default: http://127.0.0.1:6005)
#   API_KEY           x-api-key value (default: change-me)
#   WAIT_TIMEOUT_SEC  Max wait seconds for DOWNLINK_READY (default: 30)
#   WAIT_INTERVAL_SEC Poll interval seconds (default: 1)
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

need_cmd() {
  command -v "$1" >/dev/null 2>&1 || {
    echo "ERROR: required command not found: $1" >&2
    exit 1
  }
}

need_cmd curl
need_cmd python3

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

  python3 -c '
import json,sys
sat_id,gs_id,req_id=sys.argv[1],sys.argv[2],sys.argv[3]
print(json.dumps({
  "satellite_id": sat_id,
  "ground_station_id": gs_id,
  "requestor_id": req_id,
  "mission_name": "auto-downlink-by-requestor",
  "aoi_name": "auto-aoi",
  "width": 512,
  "height": 512,
  "cloud_percent": 10,
  "fail_probability": 0
}, ensure_ascii=False))
' "${satellite_id}" "${ground_station_id}" "${req_id}"
}

create_uplink_for_requestor() {
  local req_id="$1"
  local payload resp cmd_id
  payload="$(build_uplink_payload "${req_id}")" || return 1
  resp="$(
    curl -sSL -X POST "${API_BASE}/uplink" \
      -H "x-api-key: ${API_KEY}" \
      -H "Content-Type: application/json" \
      -d "${payload}"
  )"
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
    echo "ERROR: uplink creation failed: ${resp}" >&2
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

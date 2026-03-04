# K-Sattie Sky Hub Mock 데이터 이용 API 설계서

- 문서명: K-Sattie Sky Hub Mock 데이터 이용 API 설계서
- 프로젝트: K-Sattie Sky Hub
- 버전: v0.6
- 작성일: 2026-03-04

## 0. 현행화 우선 규칙 (2026-03-04)

아래 항목은 본 문서의 다른 섹션보다 우선 적용되는 현행 기준이다.

- 식별자 정책
  - 위성 외부 식별자: `satellite_id` (영문 모델명 기반)
    - 예: `KOMPSAT-3`, `KOMPSAT-3A`, `GK-2A`, `425-PROJECT-1`, `CAS500-1`, `NEONSAT`
  - 위성 내부 식별자: `internal_satellite_code` (`sat-xxxx`)
  - 지상국 외부 식별자: `ground_station_id` (약칭 코드, 지역명+이니셜)
    - 예: `DAE-MC`, `JEJ-M`, `INC-AR`
  - 지상국 내부 식별자: `internal_ground_station_code` (`gnd-xxxx`)

- 생성 API 입력
  - `POST /satellites`: `satellite_id(optional)`, `name`, `type`, `status`
    - 하위호환으로 `system_id` 입력도 허용
  - `POST /ground-stations`: `ground_station_id(optional)`, `name`, `type`, `status`, `location`
  - `ground_station_id` 미입력 시 약칭 코드 자동 생성(중복 시 `-2`, `-3` suffix)

- 중복 제약
  - 위성/지상국 `name` 중복 불가 (`POST`, `PATCH` 모두 `409`)
  - 비교 기준: 대소문자/앞뒤 공백/연속 공백 무시

- 자동 시드
  - 서버 시작 시 기본 자동 실행:
    - `POST /seed/mock-satellites`
    - `POST /seed/mock-ground-stations`
    - `POST /seed/mock-requestors`
  - 제어 환경변수: `SATTI_AUTO_SEED_ON_STARTUP` (`1` 기본, `0` 비활성)

- 응답 필드 현행
  - `GET /satellites`: `satellite_id`, `internal_satellite_code`, `name`, `type`, `status`, `profile`, `eng_model`, `domain`, `resolution_perf`, `baseline_status`, `primary_mission`
  - `GET /ground-stations`: `ground_station_id`, `internal_ground_station_code`, `name`, `type`, `status`, `location`

- 시나리오
  - `GET /scenarios` 제공
  - `SCN-001`~`SCN-012`은 위 영문 `satellite_id` 기준으로 매핑

## 1. 목적

본 문서는 위성-지상국 통신 시뮬레이터의 목데이터 구조와 API 동작 기준을 정의한다.
목표는 다음과 같다.

- EO/SAR 위성 유형별로 일관된 촬영/산출 메타데이터 제공
- 실제 비즈니스 Tasking 흐름(요청 제약조건, 우선순위, 전송 방식) 모사
- 프런트엔드 콘솔, 외부 클라이언트, 자동화 테스트가 동일한 응답 규약 사용

## 2. 적용 범위

- 백엔드: FastAPI (`app/main.py`)
- 이미지 저장 경로: `data/images`
- 인증/제어:
  - API Key (`x-api-key`)
  - CORS (`SATTI_ALLOWED_ORIGINS`)
  - IP 기준 Rate Limit (`SATTI_RATE_LIMIT_PER_MIN`)
- API:
  - `GET /health`, `GET /`
  - `POST /satellites`, `GET /satellites`, `PATCH /satellites/{id}`, `DELETE /satellites/{id}`
  - `POST /seed/mock-satellites`
  - `POST /ground-stations`, `GET /ground-stations`, `PATCH /ground-stations/{id}`, `DELETE /ground-stations/{id}`
  - `POST /seed/mock-ground-stations`
  - `GET /satellite-types`
  - `POST /uplink`
  - `GET /commands`, `GET /commands/{id}`, `POST /commands/{id}/rerun`
  - `GET /downloads/{id}`, `POST /downloads/{id}/save-local`
  - `POST /images/clear`
  - `GET /preview/external-map`

## 3. 보안 및 운영 규약

### 3.1 인증

- 공개 경로: `/`, `/health`, `/docs`, `/redoc`, `/openapi.json`
- 나머지 운영 API는 `x-api-key` 필요
- 실패 시: `401 Unauthorized`
- 다운로드 링크는 브라우저 직접 접근을 위해 `GET /downloads/{id}?api_key=...` 허용

### 3.2 Rate Limit

- 클라이언트 IP 기준 분당 요청 제한
- 기본값: `600` req/min
- `SATTI_RATE_LIMIT_PER_MIN<=0` 설정 시 비활성화
- 초과 시: `429 Too Many Requests`

### 3.3 CORS

- `SATTI_ALLOWED_ORIGINS` 환경변수 기반 허용 Origin 제어

### 3.4 콘솔 권한 모드(Mock User)

- 본 문서 기준 콘솔 UI는 목 사용자 2종을 제공:
  - `admin`: 모든 메뉴/관리 기능 허용
  - `operator`: 위성/지상국 관리 기능 비허용
- `operator`일 때 콘솔은 다음을 차단:
  - `Satellites` 탭 접근
  - 관리성 API UI 호출 (`POST/PATCH/DELETE /satellites*`, `POST/PATCH/DELETE /ground-stations*`, Seed API 2종)
- 주의: 위 규칙은 현재 프런트엔드 목 권한 제어이며, 백엔드 강제 RBAC는 별도 구현 대상

## 4. 도메인 모델

### 4.1 위성 유형

- `EO_OPTICAL`
- `SAR`

### 4.2 명령 상태

- `QUEUED`
- `ACKED`
- `CAPTURING`
- `DOWNLINK_READY`
- `FAILED`

### 4.3 위성 상태

- `AVAILABLE`
- `MAINTENANCE`

### 4.4 지상국 유형

- `FIXED`
- `LAND_MOBILE`
- `MARITIME`
- `AIRBORNE`

### 4.5 지상국 상태

- `OPERATIONAL`
- `MAINTENANCE`

## 5. API별 사용 목적과 요청/응답

모든 보호 API 호출 예시는 `x-api-key` 헤더 기준으로 작성한다.

### 5.1 `POST /seed/mock-satellites`

언제 사용하나:

- 시뮬레이터 초기 데이터(한국 위성 기반 목 위성)를 일괄 생성할 때 사용
- 이미 같은 이름의 위성이 있으면 중복 생성하지 않음

요청:

- Body 없음

응답:

- `satellite_ids`: 이번 호출에서 신규 생성된 위성 ID 목록

### 5.2 `POST /seed/mock-ground-stations`

언제 사용하나:

- 업링크 요청 주체(지상국) 목 데이터를 일괄 생성할 때 사용
- 이미 같은 이름의 지상국이 있으면 중복 생성하지 않음

요청:

- Body 없음

응답:

- `ground_station_ids`: 이번 호출에서 신규 생성된 지상국 ID 목록

### 5.3 `GET /ground-stations`

언제 사용하나:

- 업링크 요청 주체로 사용할 지상국 목록을 조회할 때 사용
- 운영/테스트에서 지상국 상태를 확인할 때 사용

요청:

- 없음

응답:

- 지상국 배열 (`ground_station_id`, `name`, `type`, `status`, `location`)

### 5.4 `POST /ground-stations`

언제 사용하나:

- Seed 외에 운영자가 지상국을 수동 등록할 때 사용

요청:

- `name`, `type`, `status(기본 OPERATIONAL)`, `location(optional)`

응답:

- `ground_station_id`

### 5.5 `PATCH /ground-stations/{id}`

언제 사용하나:

- 지상국 이름/상태/위치 변경

요청:

- `name` 또는 `status` 또는 `location`

응답:

- 수정된 지상국 객체

### 5.6 `DELETE /ground-stations/{id}`

언제 사용하나:

- 운용대상에서 지상국을 제거할 때 사용

요청:

- Path `ground_station_id`

응답:

- 삭제된 지상국 ID, 이름

### 5.7 `GET /satellite-types`

언제 사용하나:

- EO/SAR별 프로파일(궤도, 모드, 기본 산출물)을 화면에 설명할 때 사용
- 업링크 UI에서 입력 제약조건 안내 기준값으로 사용

요청:

- Query/Body 없음

응답:

- `EO_OPTICAL`, `SAR` 키별 프로파일 객체

### 5.8 `POST /satellites`

언제 사용하나:

- Seed 외에 운영자가 위성을 수동 등록할 때 사용

요청:

- `name`, `type`, `status(기본 AVAILABLE)`

응답:

- `satellite_id`

### 5.9 `GET /satellites`

언제 사용하나:

- 현재 운용 가능한 위성 목록 조회
- 업링크 대상 위성 선택 전 상태 점검

요청:

- 없음

응답:

- 위성 배열 (`satellite_id`, `name`, `type`, `status`, `profile`)

### 5.10 `PATCH /satellites/{id}`

언제 사용하나:

- 위성 이름/운용상태 변경

요청:

- `name` 또는 `status`

응답:

- 수정된 위성 객체

### 5.11 `DELETE /satellites/{id}`

언제 사용하나:

- 운용대상에서 위성을 제거할 때 사용

요청:

- Path `satellite_id`

응답:

- 삭제된 위성 ID, 이름

### 5.12 `POST /uplink`

언제 사용하나:

- 지상국이 위성에 촬영 명령(Tasking)을 전송할 때 사용
- 명령 생성 후 백그라운드 파이프라인(상태전이+촬영+다운링크 준비) 자동 시작

요청 핵심 필드:

- 식별: `satellite_id`, `ground_station_id(optional)`, `mission_name`, `aoi_name`
- AOI/시간: `aoi_center_lat/lon`, `aoi_bbox`, `window_open_utc`, `window_close_utc`
- 우선순위: `priority` (`BACKGROUND|COMMERCIAL|URGENT`)
- 영상 옵션: `width`, `height`, `cloud_percent`
- EO 제약: `max_cloud_cover_percent`, `max_off_nadir_deg`, `min_sun_elevation_deg`
- SAR 제약: `incidence_min_deg`, `incidence_max_deg`, `look_side`, `pass_direction`, `polarization`
- 전달 방식: `delivery_method` (`DOWNLOAD|S3|WEBHOOK`), `delivery_path`
- 생성 방식: `generation_mode` (`INTERNAL|EXTERNAL`), `external_map_source`, `external_map_zoom`
- 실패 주입: `fail_probability`

응답:

- `command_id`, 초기 `state(QUEUED)`, 위성/임무 정보, 지상국 정보, 생성시각

### 5.13 `GET /commands`

언제 사용하나:

- 최근/전체 명령 상태를 테이블로 한 번에 조회할 때 사용
- 대시보드/모니터링 화면에서 다건 상태 갱신에 사용

응답:

- `CommandStatusResponse[]`
- 각 항목에 `request_profile`, `acquisition_metadata`, `product_metadata` 포함

### 5.14 `GET /commands/{id}`

언제 사용하나:

- 특정 명령의 진행 상태를 폴링할 때 사용
- 다운로드 가능 시점(`DOWNLINK_READY`) 판정에 사용

응답:

- 명령 상세 상태
- 파일이 실제 존재할 때만 `download_url` 노출
- `DOWNLINK_READY` 상태라도 파일이 없으면 서버가 자동으로 `FAILED`(이미지 누락)로 정합성 보정

### 5.15 `GET /downloads/{id}`

언제 사용하나:

- 다운링크 완료된 이미지 파일 직접 다운로드

응답:

- 성공: PNG 파일 스트림
- 실패:
  - `404`: 명령 없음 또는 파일 없음
  - `409`: 아직 준비 전 또는 파일 누락으로 `FAILED` 전환된 상태

### 5.16 `POST /downloads/{id}/save-local`

언제 사용하나:

- 파일 다운로드 대신 서버 로컬 저장 정보(저장 경로/크기)를 확인할 때 사용

응답:

- `saved_path`, `file_size_bytes`, `message`
- 파일 누락 상태에서는 정합성 보정 후 `409` 또는 `404` 반환 가능

### 5.17 `POST /images/clear`

언제 사용하나:

- 테스트 생성 이미지를 일괄 정리할 때 사용
- `data/images` 내부 이미지 파일(`png/jpg/jpeg/webp`) 삭제 + 명령의 이미지 참조 초기화
- `DOWNLINK_READY` 명령은 즉시 `FAILED`로 전환되어 재시도 가능 상태로 변경

응답:

- `deleted_count`, `cleared_command_count`, `message`

### 5.18 `POST /commands/{id}/rerun`

언제 사용하나:

- 기존 명령이 `FAILED` 상태일 때 동일 `command_id`로 재수행할 때 사용
- 운영 콘솔의 Retry 버튼 동작에 사용
- 다운링크 성공 이력이 있었어도 파일이 누락된 경우(자동 `FAILED` 보정) 재수행에 사용

요청:

- Path `command_id`
- Body 없음

응답:

- 재수행 시작 직후 최신 `CommandStatusResponse`

검증/제약:

- `FAILED` 상태에서 허용
- 진행 중(`QUEUED/ACKED/CAPTURING`) 또는 정상 완료(`DOWNLINK_READY` + 파일 존재) 상태는 `409`
- 명령이 없으면 `404`

### 5.19 `GET /preview/external-map`

언제 사용하나:

- `generation_mode=EXTERNAL` 사용 전 지도 타일 기반 미리보기를 확인할 때 사용
- UI의 `Map Preview` 버튼 동작에 사용

요청(Query):

- `lat` (필수)
- `lon` (필수)
- `zoom` (기본 19, 1~19)
- `width` (기본 768)
- `height` (기본 768)
- `source` (기본 `OSM`)

응답:

- 성공: PNG 이미지 바이너리
- 실패: 오류 메시지(JSON 또는 text)

## 6. 업링크 비즈니스 필드 설계

### 6.1 AOI/시간 필드

- `ground_station_id`: 업링크 요청 지상국 식별자(선택)
- `aoi_center_lat`, `aoi_center_lon`: 점 기반 AOI 중심
- `aoi_bbox`: `[min_lon, min_lat, max_lon, max_lat]`
- `window_open_utc`, `window_close_utc`: ISO8601 UTC 시간창

검증:

- 중심 위경도는 반드시 쌍으로 입력
- bbox는 min < max
- 시간창은 open < close

### 6.2 EO 제약 필드

- `max_cloud_cover_percent`: 허용 최대 운량
- `max_off_nadir_deg`: 허용 최대 오프나딜
- `min_sun_elevation_deg`: 최소 태양고도

### 6.3 SAR 제약 필드

- `incidence_min_deg`, `incidence_max_deg`
- `look_side`: `ANY|LEFT|RIGHT`
- `pass_direction`: `ANY|ASCENDING|DESCENDING`
- `polarization`: 예 `VV`, `VH`

검증:

- `incidence_min_deg <= incidence_max_deg`

### 6.4 전달(Delivery) 필드

- `delivery_method`: `DOWNLOAD|S3|WEBHOOK`
- `delivery_path`: S3 경로 또는 Webhook URL

검증:

- `delivery_method`가 `S3`/`WEBHOOK`이면 `delivery_path` 필수

### 6.5 생성(Generation) 필드

- `generation_mode`: `INTERNAL|EXTERNAL`
- `external_map_source`: 현재 `OSM` 지원
- `external_map_zoom`: `1~19`

주의:

- 본 항목은 시뮬레이터 품질 검증을 위한 선택 옵션이다.
- 실제 위성 연동 운영 API에서는 필수 항목이 아니며, 기본적으로 생략 가능하다.

검증:

- `generation_mode=EXTERNAL`일 때 `aoi_center_lat/lon` 또는 `aoi_bbox` 필수

## 7. 상태 전이 및 시뮬레이션 동작

### 7.1 정상/실패 경로

- 정상: `QUEUED -> ACKED -> CAPTURING -> DOWNLINK_READY`
- 실패: 각 단계에서 `FAILED` 전이 가능

### 7.2 단계별 시간 지연(모사)

- contact window 대기: `0.7 ~ 1.8s` 후 `ACKED`
- 위성 내부 준비: `0.6 ~ 1.6s` 후 `CAPTURING`
- 촬영 소요: `1.5 ~ 3.8s` 후 완료/실패

### 7.3 실패 확률 모델

- ACK 이전 구간 실패: `fail_probability * 0.6`
- CAPTURING 이후 실패: `fail_probability * 0.4`
- 예외 발생 시 `FAILED`로 종료

## 8. 위성 유형별 메타데이터 규칙

### 8.1 EO (`EO_OPTICAL`)

`acquisition_metadata`:

| 필드 | 값 범위/형식 | 설명 |
|---|---|---|
| `captured_at` | UTC ISO8601 | 촬영 완료 시각 |
| `sensor_mode` | `NADIR \| OFF_NADIR` | 촬영 기하 모드 |
| `off_nadir_deg` | `2.0~28.0` | 오프나딜 각도 |
| `sun_elevation_deg` | `20.0~65.0` | 태양고도 |
| `cloud_cover_percent` | `0~100` | 운량 추정치 |
| `ground_track` | `ASCENDING \| DESCENDING` | 궤도 진행 방향 |
| `aoi_name` | string | AOI 이름 |
| `aoi_center` | object/null | 요청 AOI 중심 좌표 |
| `aoi_bbox` | array/null | 요청 AOI bbox |

`product_metadata`:

| 필드 | 값 범위/형식 | 설명 |
|---|---|---|
| `product_type` | `L1B_ORTHOREADY` | 기본 광학 산출물 타입 |
| `bands` | `R,G,B,NIR` | 밴드 정보 |
| `gsd_m` | `0.5~1.5` | 공간해상도 |
| `width_px` | int | 가로 픽셀 |
| `height_px` | int | 세로 픽셀 |
| `bit_depth` | `8` | 비트 심도 |
| `format` | `PNG` | 파일 포맷 |

### 8.2 SAR (`SAR`)

`acquisition_metadata`:

| 필드 | 값 범위/형식 | 설명 |
|---|---|---|
| `captured_at` | UTC ISO8601 | 촬영 완료 시각 |
| `sensor_mode` | `SPOTLIGHT \| STRIPMAP` | SAR 모드 |
| `incidence_angle_deg` | `20.0~45.0` | 입사각 |
| `look_side` | `LEFT \| RIGHT` | 관측 측면 |
| `pass_direction` | `ASCENDING \| DESCENDING` | 궤도 방향 |
| `polarization` | `VV \| VH` | 편파 |
| `aoi_name` | string | AOI 이름 |
| `aoi_center` | object/null | 요청 AOI 중심 좌표 |
| `aoi_bbox` | array/null | 요청 AOI bbox |

`product_metadata`:

| 필드 | 값 범위/형식 | 설명 |
|---|---|---|
| `product_type` | `GRD` | 기본 SAR 산출물 타입 |
| `resolution_m` | `0.8~3.0` | 유효 해상도 |
| `width_px` | int | 가로 픽셀 |
| `height_px` | int | 세로 픽셀 |
| `format` | `PNG` | 파일 포맷 |
| `speckle_filter` | `NONE \| LEE_3x3` | 노이즈 필터 |

## 9. 목 위성 시드 데이터

`POST /seed/mock-satellites` 기본 구성:

- KOMPSAT-3 (Arirang-3) - EO
- KOMPSAT-3A (Arirang-3A) - EO
- CAS500-1 (NextSat-1) - EO
- Cheollian-2B (GEO-KOMPSAT-2B) - EO
- KOMPSAT-5 (Arirang-5, SAR) - SAR
- KOMPSAT-6 (Arirang-6, SAR) - SAR
- KOMPSAT-Next-5 (C-band SAR) - SAR

## 10. 콘솔 기본값 프리셋 규칙

`Send A Uplink` 화면은 위성 선택 시 입력 필드를 자동 세팅한다.

- 1순위: 시드 7개 위성 이름 기반 프리셋 적용
- 2순위: 시드 외 위성은 위성 유형(`EO_OPTICAL`/`SAR`) fallback 프리셋 적용

자동 세팅 대상:

- `mission_name`, `aoi_name`
- `aoi_center_lat/lon`, `aoi_bbox`
- `priority`
- `width`, `height`, `cloud_percent`, `fail_probability`
- EO 제약 필드, SAR 제약 필드, Delivery 필드

## 11. 저장/다운로드 정책

- 생성 이미지 저장: `data/images/{command_id}.png`
- `download_url` 노출 조건:
  - 상태가 `DOWNLINK_READY`
  - 파일이 실제로 존재
- 상태가 `DOWNLINK_READY`여도 파일이 없으면 자동으로 `FAILED` 전환(이미지 누락)
- `POST /images/clear` 실행 시:
  - 파일 삭제
  - 명령의 `image_path` 비움
  - 기존 `DOWNLINK_READY` 명령은 `FAILED`로 전환되어 Retry 가능

## 12. 표준 호출 예시

```bash
API_KEY='change-me'
BASE='http://127.0.0.1:6005'

curl -s -X POST "$BASE/seed/mock-satellites" -H "x-api-key: $API_KEY"
curl -s "$BASE/satellites" -H "x-api-key: $API_KEY"

curl -s -X POST "$BASE/uplink" \
  -H "x-api-key: $API_KEY" \
  -H 'Content-Type: application/json' \
  -d '{
    "satellite_id":"KOMPSAT-3",
    "mission_name":"harbor-monitoring",
    "aoi_name":"incheon-port",
    "aoi_center_lat":37.45,
    "aoi_center_lon":126.62,
    "window_open_utc":"2026-02-28T01:00:00Z",
    "window_close_utc":"2026-02-28T03:00:00Z",
    "priority":"URGENT",
    "max_cloud_cover_percent":25,
    "delivery_method":"DOWNLOAD",
    "fail_probability":0.05
  }'

curl -s "$BASE/commands" -H "x-api-key: $API_KEY"
curl -L "$BASE/downloads/cmd-xxxxxxxxxxxx?api_key=$API_KEY" -o result.png
curl -s -X POST "$BASE/images/clear" -H "x-api-key: $API_KEY"
```

## 13. 향후 확장

- AOI 폴리곤(GeoJSON) 직접 입력 지원
- Contact window 계산 로직 고도화(TLE 기반)
- 산출물 포맷 확대(GeoTIFF/COG)
- Delivery 실동작(S3 업로드/Webhook 전송) 시뮬레이션 분리

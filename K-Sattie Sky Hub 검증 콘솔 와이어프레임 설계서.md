# K-Sattie Sky Hub 검증 콘솔 와이어프레임 설계서

- 문서명: K-Sattie Sky Hub 검증 콘솔 와이어프레임 설계서
- 화면 타이틀: K-Sattie Sky Hub (uplink수신-> 위성촬영 -> downlink전송)
- 프로젝트: K-Sattie Sky Hub
- 목적 URL: `http://localhost:6005`
- 버전: v0.5
- 작성일: 2026-03-04

## 0. 현행화 우선 규칙 (2026-03-04)

아래 항목은 본 문서의 이전 섹션보다 우선 적용되는 현행 콘솔 기준이다.

- 좌측 메뉴 구조
  - `Dashboard`
  - `Satellites`
  - `Satellites Performance`
  - `Payload Monitoring`
  - `Diagnostics` (기본 접힘 `▸`)
    - `Send A Uplink`
    - `Multi Payload Scenario`
    - `Commands Monitor`

- Satellites 테이블 컬럼
  - `satellite_id`
  - `internal_satellite_code`
  - `name`
  - `type`
  - `status`
  - `profile`
  - `actions`

- Ground Stations 테이블 컬럼
  - `ground_station_id`
  - `internal_ground_station_code`
  - `name`
  - `type`
  - `status`
  - `location`
  - `actions`

- Create 폼 입력
  - Create Satellite: `satellite_id(optional)` + `name` + `type` + `status`
  - Create Ground Station: `ground_station_id(optional)` + `name` + `type` + `status` + `location`

- Edit 동작
  - Satellite Edit에서 `name`, `type`, `status` 변경 가능
  - Ground Station Edit에서 `name`, `status`, `location` 변경 가능

- 초기 데이터
  - 서버 기동 시 기본으로 Mock Satellites/Ground Stations/Requestors 자동 시드
  - 수동 시드 버튼은 재실행(idempotent) 용도

- 식별자 규칙
  - `satellite_id`: 영문 모델명 기반 ID (예: `KOMPSAT-3`, `425-PROJECT-1`)
  - `ground_station_id`: 지역명+이니셜 약칭 ID (예: `DAE-MC`)

## 1. 목적

본 문서는 `http://localhost:6005` 콘솔 화면에서 다음을 검증할 수 있도록 현재 구현 기준으로 정리한 와이어프레임 설계서이다.

- 위성 CRUD 및 시드 데이터 운용 검증
- 업링크 명령 전송과 상태 전이 모니터링 검증
- 다운로드 링크/이미지 정리 동작 검증
- API 호출 로그와 시나리오 테스트 검증

## 2. 콘솔 정보구조(IA)

단일 페이지 탭 구조:

- `Dashboard`
- `Satellites`
- `Satellites Performance`
- `Commands Monitor`
- `Send A Uplink`
- `Multi Payload Scenario`

고정 공통 영역:

- Header: Mock User(admin/operator), API Base URL, API Key, `Apply URL`, `Clear Images`, `Clear Logs`
- 하단: `Recent API Calls` (최대 300라인, 화면상 20줄 표시 + 내부 스크롤)

## 3. 공통 레이아웃 와이어프레임

```text
+----------------------------------------------------------------------------------+
| Header: K-Sattie Sky Hub                                                         |
| [Mock User] [Base URL] [x-api-key] [Apply URL] [Clear Images] [Clear Logs]     |
+----------------------------------------------------------------------------------+
| Left Nav Tabs            | Main Content                                          |
| - Dashboard              | 선택한 탭 카드/테이블/폼 표시                         |
| - Satellites      |                                                       |
| - Satellites Performance |                                                       |
| - Commands Monitor        |                                                       |
| - Send A Uplink |                                                       |
| - Multi Payload Scenario     |                                                       |
+----------------------------------------------------------------------------------+
| Recent API Calls (Time / Method / Path / Status / Summary)                       |
| - 컬럼 비율: 10% / 10% / 20% / 10% / 50%                                         |
| - 시간 형식: MM/DD HH:MM:SS                                                      |
| - Summary: pretty JSON, 전체 문자열 표시                                         |
+----------------------------------------------------------------------------------+
```

## 4. 화면별 설계

### 4.1 Dashboard (Sky Hub Dashboard)

목적:

- 콘솔 접속 직후 전체 상태를 한 번에 확인
- 주요 API quick check 실행
- 시드 위성 요약과 명령 KPI 확인

구성:

- `Sky Hub Dashboard`
  - `Health Check` 버튼 (`GET /health`)
- KPI 카드
  - Satellites
  - Ground Stations
  - Uplink Commands
  - Downlink Ready
  - Failed
- `Command State Distribution`
- `Korean Satellite Seed Summary`
  - 한국 위성명 목록 프리뷰

동작 규칙:

- Dashboard 탭 전환 시 즉시 데이터 갱신

### 4.2 Satellites

목적:

- 시드/수동 생성/수정/삭제를 통한 위성 자원 관리 검증

구성:

- `Setup Initial Mock Satellites` 버튼 (`POST /seed/mock-satellites`)
- `Setup Initial Mock Ground Stations` 버튼 (`POST /seed/mock-ground-stations`)
- `Show Satellite Types Available` 버튼 (`GET /satellite-types`)
- `Create Satellite` 폼
  - `name`, `type`, `status`, `Create`
- `Satellites Table`
  - 컬럼: ID, Name, Type, Status, Profile, Actions(Edit/Delete)
- `Create Ground Station` 폼
  - `name`, `type`, `status`, `location(optional)`, `Create`
- `Ground Stations Table`
  - 컬럼: ID, Name, Type, Status, Location, Actions(Edit/Delete)
- `Update Selected` 폼
  - 테이블에서 `Edit` 선택 후 `name/status` 수정

동작 규칙:

- Create/Update/Delete 직후 위성 목록과 대시보드가 자동 반영
- Delete 시 별도 Apply 버튼 없이 즉시 서버 반영
- `Status` 표시는 텍스트 색상 규칙 적용:
  - `AVAILABLE`: 녹색 텍스트
  - 그 외 상태: 빨간색 텍스트
- `Actions`는 버튼 박스가 아닌 밑줄 링크(`Edit`, `Delete`)로 표시
- Ground Station도 동일하게 `Edit/Delete` 링크 및 편집 모달 제공

### 4.3 Send A Uplink

목적:

- 비즈니스형 업링크 요청 파라미터 검증
- Tasking 제약조건 입력 후 명령 생성

구성:

- 생성 모드 토글: `내부생성` / `외부생성`
- 외부생성 옵션: `external_map_source`, `external_map_zoom`
- 대상 위성 선택: `/satellites` 기반 드롭다운
- 요청 지상국 선택: `/ground-stations` 기반 드롭다운 (`ground_station_id`, 없으면 null)
- 기본 필드: `mission_name`, `aoi_name`, `priority`
- AOI/시간: `aoi_center_lat/lon`, `aoi_bbox`, `window_open_utc/window_close_utc`
- 영상/실패 제어: `width`, `height`, `cloud_percent`, `fail_probability`
- EO 제약: `max_cloud_cover_percent`, `max_off_nadir_deg`, `min_sun_elevation_deg`
- SAR 제약: `incidence_min_deg/max_deg`, `look_side`, `pass_direction`, `polarization`
- 전달 설정: `delivery_method`, `delivery_path`
- `Send Uplink` 버튼

동작 규칙:

- 위성 선택 변경 시 자동 기본값 세팅:
  - 시드 7개 위성은 이름 기반 프리셋 자동 입력
  - 시드 외 위성은 EO/SAR 유형별 fallback 프리셋 자동 입력
- 각 입력 박스는 마우스 오버 시 커스텀 툴팁 표시(포인터 오른쪽 위치)
- `외부생성` 선택 시 위경도 또는 bbox를 기반으로 외부 지도 타일 이미지 생성 경로 사용
- 위 생성 모드/외부맵 옵션은 시뮬레이터 전용 선택 항목(실운영 연동에서는 필수 아님)
- 정상 입력 시 `POST /uplink` 실행 후 결과 JSON 표시
- 성공 시 즉시 `Commands Monitor` 탭으로 자동 전환

### 4.4 Commands Monitor

목적:

- 명령 상태 전이 실시간 모니터링
- 다운로드 가능 시점 확인

구성:

- `command_id` 입력 + `Fetch`
- `Auto Poll: ON/OFF` 토글
  - 동작 중 버튼 텍스트를 빨간색 강조
- 상태 타임라인
  - `QUEUED -> ACKED -> CAPTURING -> DOWNLINK_READY/FAILED`
- `acquisition_metadata / product_metadata` JSON 뷰어
- 다운로드 링크 배지
- `Completed Image Links` 테이블
  - 컬럼: Command ID, Satellite Name, State, Image Created At, Downlink

동작 규칙:

- `Fetch` 시 상태가 진행 중이면 자동 follow 폴링으로 최종 상태까지 추적
- `DOWNLINK_READY` + 파일 존재 시 다운로드 링크 활성화
- `DOWNLINK_READY`라도 파일 누락이 감지되면 서버가 `FAILED`(이미지 누락)로 자동 보정
- 누락 보정된 `FAILED` 명령은 `Retry`로 재실행 가능
- `POST /images/clear` 이후 링크 테이블에서 해당 다운로드 항목 제거
- 폴링 중 과도한 요청 방지를 위해 대시보드 전체 갱신 호출을 최소화하고, 완료 링크는 로컬 캐시 기반으로 즉시 반영

### 4.5 Multi Payload Scenario

목적:

- 대표 정상/오류 시나리오 회귀 검증

구성:

- 시나리오 버튼:
  - SCN-001 Normal EO
  - SCN-002 Normal SAR
  - SCN-003 Download 404
  - SCN-004 Download 409
  - SCN-005 Maintenance Fail
  - SCN-006 Invalid Sat 404
- 결과 지표: Pass/Fail 카운트
- 실행 로그 출력

## 5. API-UI 매핑(현행)

| UI 액션 | API | 목적 | 기대 응답 |
|---|---|---|---|
| Health Check | `GET /health` | 서버 생존 확인 | `200`, `{status:"ok"}` |
| Show Satellite Types Available | `GET /satellite-types` | EO/SAR 유형 안내 | `200`, 타입 프로파일 |
| Setup Initial Mock Satellites | `POST /seed/mock-satellites` | 한국 위성 시드 생성 | `200`, `satellite_ids` |
| Setup Initial Mock Ground Stations | `POST /seed/mock-ground-stations` | 업링크 요청 지상국 시드 생성 | `200`, `ground_station_ids` |
| Ground Stations 목록 조회 | `GET /ground-stations` | 지상국 선택 드롭다운 구성 | `200`, 지상국 배열 |
| Create Satellite | `POST /satellites` | 위성 수동 등록 | `200`, `satellite_id` |
| Update Selected | `PATCH /satellites/{id}` | 위성 수정 | `200`, 수정 객체 |
| Delete Satellite | `DELETE /satellites/{id}` | 위성 삭제 | `200`, 삭제 정보 |
| Create Ground Station | `POST /ground-stations` | 지상국 수동 등록 | `200`, `ground_station_id` |
| Update Ground Station | `PATCH /ground-stations/{id}` | 지상국 수정 | `200`, 수정 객체 |
| Delete Ground Station | `DELETE /ground-stations/{id}` | 지상국 삭제 | `200`, 삭제 정보 |
| Send Uplink | `POST /uplink` | 촬영 명령 전송 | `200`, `command_id/state` |
| Fetch / Auto Poll | `GET /commands/{id}` | 상태 모니터링 | `200`, 상태/메타데이터 |
| Retry (Failed/missing file) | `POST /commands/{id}/rerun` | 실패/이미지누락 명령 재수행 | `200` 상태 객체 / `409` |
| Commands 목록 갱신 | `GET /commands` | 전체 상태 집계 | `200`, 명령 배열 |
| Download 링크 | `GET /downloads/{id}` | 이미지 파일 수신 | `200` PNG / `404` / `409` |
| Save Local Download | `POST /downloads/{id}/save-local` | 로컬 저장 메타 확인 | `200`, 저장 정보 |
| Clear Images | `POST /images/clear` | 테스트 이미지 일괄 삭제 | `200`, 삭제 건수 |
| External Map Preview | `GET /preview/external-map` | 외부지도 미리보기 이미지 | `200` PNG |

## 6. 메뉴/권한 규칙 (Mock User)

- 사용자 유형:
  - `admin`: 모든 탭 접근 + 위성/지상국 관리(Create/Update/Delete/Seed) 허용
  - `operator`: `Satellites` 탭 비노출, 위성/지상국 관리 API UI 호출 차단(시뮬레이션 `403`)
- Header의 Mock User 셀렉터로 사용자 전환 가능
- 현재 사용자 정보는 Header에 표시되며 local storage(`simMockUserId`)에 유지

## 7. 인증/에러 UX 규칙

- 공개 경로 외 API는 `x-api-key` 필수
- 인증 실패 시 로그에 `401 Unauthorized` 기록
- 과호출 시 `429 Too Many Requests` 기록
- 다운로드 링크는 브라우저 접근을 위해 `api_key` 쿼리 파라미터 자동 부착

## 8. 로그 영역(Recent API Calls) 규칙

- 로그는 최신 항목이 위에 추가(prepend)
- 최대 300라인 유지
- `Path` 컬럼은 상대폭 기준으로 표시(20%)
- `Summary` 컬럼은 상대폭 50%로 JSON 구조를 줄바꿈 표시

## 9. 상태 시뮬레이션 검증 포인트

- 시간 지연 모사:
  - `QUEUED` 대기 후 `ACKED`
  - 준비 후 `CAPTURING`
  - 촬영 완료 시 `DOWNLINK_READY` 또는 실패 시 `FAILED`
- 실패 확률은 `fail_probability`를 통해 조정
- 상태 전이 도중 UI는 타임라인/메타/다운로드 링크를 즉시 반영

## 10. 운영 검증 순서(권장)

1. Dashboard에서 `Health Check` 실행
2. `admin` 사용자 선택 후 Satellites에서 `Setup Initial Mock Satellites` 실행
3. Send A Uplink에서 유효 파라미터 입력 후 `Send Uplink`
4. 자동 전환된 Commands Monitor에서 상태 완료(`DOWNLINK_READY`) 확인
5. 다운로드 링크 검증
6. `Clear Images` 실행 후 링크 테이블 정리 상태 확인
7. Multi Payload Scenario에서 오류 케이스 회귀 확인

PRAGMA foreign_keys = ON;

-- 1) 마스터
CREATE TABLE IF NOT EXISTS satellite_master (
  system_id TEXT PRIMARY KEY,
  kor_name TEXT NOT NULL,
  eng_model TEXT NOT NULL,
  domain TEXT NOT NULL,
  resolution_perf TEXT NOT NULL,
  status TEXT NOT NULL,
  primary_mission TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS scenario_master (
  scenario_id TEXT PRIMARY KEY,
  scenario_name TEXT NOT NULL,
  scenario_desc TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS scenario_satellite (
  scenario_id TEXT NOT NULL,
  system_id TEXT NOT NULL,
  PRIMARY KEY (scenario_id, system_id),
  FOREIGN KEY (scenario_id) REFERENCES scenario_master(scenario_id) ON DELETE CASCADE,
  FOREIGN KEY (system_id) REFERENCES satellite_master(system_id) ON DELETE CASCADE
);

-- 2) 운영
CREATE TABLE IF NOT EXISTS tasking_order (
  task_id TEXT PRIMARY KEY,
  scenario_id TEXT NOT NULL,
  satellite_id TEXT NOT NULL,
  target_aoi TEXT NOT NULL,
  priority TEXT NOT NULL CHECK (priority IN ('LOW','MEDIUM','HIGH','CRITICAL')),
  due_utc TEXT NOT NULL,
  created_utc TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')),
  FOREIGN KEY (scenario_id) REFERENCES scenario_master(scenario_id),
  FOREIGN KEY (satellite_id) REFERENCES satellite_master(system_id)
);

CREATE TABLE IF NOT EXISTS acquisition_scene (
  scene_id TEXT PRIMARY KEY,
  task_id TEXT NOT NULL,
  satellite_id TEXT NOT NULL,
  sensor_mode TEXT NOT NULL,
  acquired_utc TEXT NOT NULL,
  cloud_pct REAL,
  gsd_m REAL,
  quality_score REAL CHECK (quality_score BETWEEN 0 AND 1),
  FOREIGN KEY (task_id) REFERENCES tasking_order(task_id) ON DELETE CASCADE,
  FOREIGN KEY (satellite_id) REFERENCES satellite_master(system_id)
);

CREATE TABLE IF NOT EXISTS detection_event (
  event_id TEXT PRIMARY KEY,
  scene_id TEXT NOT NULL,
  event_type TEXT NOT NULL,
  confidence REAL NOT NULL CHECK (confidence BETWEEN 0 AND 1),
  lat REAL NOT NULL,
  lon REAL NOT NULL,
  metric_value REAL,
  metric_unit TEXT,
  detected_utc TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')),
  FOREIGN KEY (scene_id) REFERENCES acquisition_scene(scene_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS alert_log (
  alert_id TEXT PRIMARY KEY,
  event_id TEXT NOT NULL,
  severity TEXT NOT NULL CHECK (severity IN ('INFO','WARN','HIGH','CRITICAL')),
  recipient TEXT NOT NULL,
  status TEXT NOT NULL CHECK (status IN ('PENDING','SENT','FAILED','ACK')),
  sent_utc TEXT,
  FOREIGN KEY (event_id) REFERENCES detection_event(event_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS system_health (
  health_id INTEGER PRIMARY KEY AUTOINCREMENT,
  satellite_id TEXT NOT NULL,
  observed_utc TEXT NOT NULL,
  power_pct REAL CHECK (power_pct BETWEEN 0 AND 100),
  thermal_c REAL,
  link_status TEXT NOT NULL CHECK (link_status IN ('OK','DEGRADED','DOWN')),
  attitude_mode TEXT,
  FOREIGN KEY (satellite_id) REFERENCES satellite_master(system_id)
);

CREATE INDEX IF NOT EXISTS idx_tasking_sat_due ON tasking_order(satellite_id, due_utc);
CREATE INDEX IF NOT EXISTS idx_scene_task ON acquisition_scene(task_id);
CREATE INDEX IF NOT EXISTS idx_event_scene ON detection_event(scene_id);
CREATE INDEX IF NOT EXISTS idx_health_sat_time ON system_health(satellite_id, observed_utc);

-- 3) 위성 기초데이터(15)
INSERT INTO satellite_master VALUES
('K-EO-01','아리랑 3호','KOMPSAT-3','EO','0.7m optical','운용 중','국토 관리 및 지구 정밀 관측'),
('K-EO-02','아리랑 3A호','KOMPSAT-3A','EO/IR','0.55m optical+IR','운용 중','야간 관측 및 열원 탐지'),
('K-EO-03','아리랑 7호','KOMPSAT-7','EO','0.3m optical','운용 초기','초고해상도 촬영'),
('K-SAR-01','아리랑 5호','KOMPSAT-5','SAR','1.0m radar','운용 중','전천후 지형 관측'),
('K-SAR-02','아리랑 6호','KOMPSAT-6','SAR','0.5m radar','발사 대기','2026년 3분기 발사 예정'),
('K-GEO-01','천리안 2A호','GK-2A','GEO-EO','기상/우주기상','운용 중','24시간 실시간 기상 예보 및 감시'),
('K-GEO-02','천리안 2B호','GK-2B','GEO-EO','해양/대기환경','운용 중','미세먼지 이동 경로 및 해양 환경'),
('MIL-425-01','군 정찰위성 1호','425 Project #1','EO/IR','0.3m class','전력화 완료','대북 전략 표적 주간 정밀 감시'),
('MIL-425-02','군 정찰위성 2호','425 Project #2','SAR','0.5m class','전력화 완료','전천후 대북 감시'),
('MIL-425-03','군 정찰위성 3호','425 Project #3','SAR','0.5m class','운용 중','군집 감시망 구성'),
('MIL-425-04','군 정찰위성 4호','425 Project #4','SAR','0.5m class','운용 중','군집 감시망 구성 (재방문 주기 단축)'),
('MIL-425-05','군 정찰위성 5호','425 Project #5','SAR','0.5m class','운용 초기','2025.11 발사 성공, 체계 완성'),
('K-CAS-01','차세대 중형 1호','CAS500-1','EO','0.5m optical','운용 중','국토 자원 관리'),
('K-CAS-02','차세대 중형 2호','CAS500-2','EO','0.5m optical','발사 예정','2026년 상반기 발사 (재난 대응)'),
('K-NEON','초소형 군집위성','NEONSAT','EO','1.0m class constellation','확장 중','고빈도 재방문 관측');

-- 4) 시나리오(12)
INSERT INTO scenario_master VALUES
('SCN-001','국토 정사영상 갱신','K-EO-01, K-CAS-01 기반 정사영상 주기 갱신'),
('SCN-002','야간 산불 열원 탐지','K-EO-02 IR 야간 열원 탐지'),
('SCN-003','도시변화 탐지','K-EO-03 초고해상도 변화 분석'),
('SCN-004','홍수지역 SAR 판독','K-SAR-01 장마철 침수 분석'),
('SCN-005','정밀 레이더 표적 재식별','K-SAR-02 정밀 SAR 표적 재식별'),
('SCN-006','태풍 실황 추적','K-GEO-01 기상 연속 감시'),
('SCN-007','미세먼지 이동 추적','K-GEO-02 대기/해양 환경 감시'),
('SCN-008','전략표적 EO/IR 감시','MIL-425-01 주간 정밀 감시'),
('SCN-009','악천후 표적 감시','MIL-425-02 SAR 감시'),
('SCN-010','SAR 군집 재방문 감시','MIL-425-03/04/05 군집 감시'),
('SCN-011','재난 대응 표준 관측','K-CAS-02 재난 대응'),
('SCN-012','초소형 군집 모니터링','K-NEON 고빈도 관측');

INSERT INTO scenario_satellite VALUES
('SCN-001','K-EO-01'),('SCN-001','K-CAS-01'),
('SCN-002','K-EO-02'),
('SCN-003','K-EO-03'),
('SCN-004','K-SAR-01'),
('SCN-005','K-SAR-02'),
('SCN-006','K-GEO-01'),
('SCN-007','K-GEO-02'),
('SCN-008','MIL-425-01'),
('SCN-009','MIL-425-02'),
('SCN-010','MIL-425-03'),('SCN-010','MIL-425-04'),('SCN-010','MIL-425-05'),
('SCN-011','K-CAS-02'),
('SCN-012','K-NEON');

-- 5) 샘플 운영데이터 1건
INSERT INTO tasking_order(task_id,scenario_id,satellite_id,target_aoi,priority,due_utc)
VALUES ('TASK-20260304-0007','SCN-002','K-EO-02','Gangwon-do_bbox_129.0_37.0_129.6_37.6','HIGH','2026-03-04T15:00:00Z');

INSERT INTO acquisition_scene(scene_id,task_id,satellite_id,sensor_mode,acquired_utc,cloud_pct,gsd_m,quality_score)
VALUES ('SCN-K-EO-02-20260304-150312','TASK-20260304-0007','K-EO-02','EO_IR_NIGHT','2026-03-04T15:03:12Z',12.3,0.55,0.91);

INSERT INTO detection_event(event_id,scene_id,event_type,confidence,lat,lon,metric_value,metric_unit)
VALUES ('DET-20260304-8841','SCN-K-EO-02-20260304-150312','WILDFIRE_HOTSPOT',0.94,37.4123,128.9122,18400,'m2');

INSERT INTO alert_log(alert_id,event_id,severity,recipient,status,sent_utc)
VALUES ('ALT-20260304-120','DET-20260304-8841','CRITICAL','NEMA/KFS','SENT','2026-03-04T15:05:00Z');

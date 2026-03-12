# OCI DB Metric Report Tool

OCI Database (PostgreSQL / MySQL HeatWave) 모니터링 메트릭을 시계열로 수집하고,
차트 + 통계 + Markdown 리포트를 자동 생성하는 도구입니다.

**CLI** + **Web UI** 두 가지 방식 지원.

---

## 1. Prerequisites

| Tool | Version | 설치 확인 | 용도 |
|------|---------|----------|------|
| OCI CLI | 3.x+ | `oci --version` | OCI Monitoring API 호출 |
| Python 3 | 3.8+ | `python3 --version` | 차트 생성, 웹 서비스 |
| jq | 1.6+ | `jq --version` | JSON → CSV 변환 |

### OCI CLI 설치 & 설정

```bash
# 설치
bash -c "$(curl -L https://raw.githubusercontent.com/oracle/oci-cli/master/scripts/install/install.sh)"

# 프로파일 설정 → ~/.oci/config 생성
oci setup config
```

필요 권한: Compartment에 대한 `monitoring metric read`

---

## 2. 설치

```bash
git clone https://github.com/jaesucjang/oci-db-metric-report.git
cd oci-db-metric-report
pip3 install -r requirements.txt
```

---

## 3. Web UI 사용법

### 3-1. 서버 기동

```bash
# 기본 실행 (포트 5050)
python3 app.py

# 백그라운드 실행
nohup python3 app.py > app.log 2>&1 &

# 포트 변경 시 app.py 마지막 줄 수정:
#   app.run(host="0.0.0.0", port=8080)
```

접속: **http://localhost:5050**

### 3-2. 사용 Flow

```
1. Sample Configs 에서 템플릿 클릭 (또는 직접 입력)
   ↓
2. Compartment ID, 시간 범위, Namespace 확인/수정
   ↓
3. [Generate Report] 클릭
   ↓
4. 실시간 진행률 확인 (수집 → 차트 → 리포트)
   ↓
5. 결과 확인: 차트 / 통계 테이블 / 다운로드
```

### 3-3. 주요 기능

| 기능 | 설명 |
|------|------|
| **Sample Configs** | MySQL Benchmark / PostgreSQL Benchmark / Load Test 템플릿 원클릭 로드 |
| **실시간 진행률** | 수집 → 차트 → 리포트 단계별 프로그레스 바 + 라이브 로그 |
| **차트 뷰어** | Overview / Detail / Zoom 3종 차트, 클릭 시 확대 |
| **통계 테이블** | Mean / Max / Min / P95 / Std 자동 계산 |
| **다운로드** | 차트 PNG, CSV, Markdown 리포트 개별 다운로드 |
| **History** | 이전 실행 내역 조회/재확인 |

### 3-4. 내장 Sample Configs

| 이름 | Namespace | 설명 |
|------|-----------|------|
| MySQL Benchmark Sample | `oci_mysql_database` | mysqlslap 벤치마크 메트릭 (1 OCPU/16GB) |
| PostgreSQL Benchmark Sample | `oci_postgresql` | PGBench 벤치마크 메트릭 (2 OCPU/32GB) |
| MySQL Load Test Template | `oci_mysql_database` | sysbench 로드테스트 템플릿 (시간 직접 입력) |

### 3-5. API Endpoints

| Method | Endpoint | 설명 |
|--------|----------|------|
| GET | `/` | 메인 대시보드 |
| POST | `/api/run` | 리포트 생성 시작 (JSON body) |
| GET | `/api/status/<job_id>` | 작업 상태 조회 |
| GET | `/api/log/<job_id>` | 실행 로그 조회 |
| GET | `/api/chart/<job_id>/<filename>` | 차트 이미지 조회 |
| GET | `/api/report/<job_id>` | Markdown 리포트 조회 |
| GET | `/api/download/<job_id>/<filename>` | 파일 다운로드 |
| GET | `/api/samples` | 샘플 config 목록 |
| GET | `/api/samples/<id>` | 샘플 config 상세 |
| GET | `/api/jobs` | 실행 히스토리 |
| GET | `/report/<job_id>` | 리포트 뷰어 페이지 |

### 3-6. 서버 중지

```bash
# PID 확인 후 중지
pgrep -f "python3 app.py"
kill <PID>

# 또는 한번에
pkill -f "python3 app.py"
```

---

## 4. CLI 사용법

### 4-1. 설정 파일 생성

```bash
cp config.env.example config.env
vi config.env
```

| 항목 | 필수 | 설명 | 예시 |
|------|------|------|------|
| `OCI_CONFIG_FILE` | | OCI config 파일 경로 | `~/.oci/config` |
| `OCI_PROFILE` | | OCI CLI 프로파일 이름 | `DEFAULT` |
| `COMPARTMENT_ID` | **필수** | Compartment OCID | `ocid1.compartment.oc1..aaa...` |
| `NAMESPACE` | **필수** | DB 서비스 네임스페이스 | `oci_mysql_database` / `oci_postgresql` |
| `INTERVAL` | | 수집 간격 | `1m`, `5m`, `1h` |
| `START_TIME` | **필수** | 수집 시작 (UTC) | `2026-03-12T06:00:00Z` |
| `END_TIME` | **필수** | 수집 종료 (UTC) | `2026-03-12T07:00:00Z` |
| `BENCH_START` | | 벤치마크 시작 (차트 하이라이트) | `2026-03-12T06:29:00Z` |
| `BENCH_END` | | 벤치마크 종료 | `2026-03-12T06:33:00Z` |
| `REPORT_TITLE` | | 리포트 제목 | `OCI MySQL Metric Report` |

### 4-2. All-in-One 실행

```bash
./generate_report.sh
```

### 4-3. 단계별 실행

```bash
# Step 1: 메트릭 수집
./fetch_metrics.sh

# Step 2: 차트 생성
python3 generate_charts.py output/metrics_oci_mysql_database_20260312_120000

# Step 3: 리포트 생성 (기존 데이터 사용)
./generate_report.sh output/metrics_oci_mysql_database_20260312_120000
```

### 4-4. 다른 config 파일 사용

```bash
./generate_report.sh /path/to/another-config.env
```

---

## 5. Output 구조

```
output/metrics_oci_mysql_database_20260312_120000/
├── _metadata.json           # 수집 메타데이터
├── CPUUtilization.json      # 원본 JSON (per metric)
├── CPUUtilization.csv       # 시계열 CSV (timestamp, value)
├── Statements.json
├── Statements.csv
├── ...                      # (22개 메트릭)
├── chart_overview.png       # 카테고리별 종합 차트
├── chart_detail.png         # 개별 메트릭 상세 차트
├── chart_zoom.png           # 벤치마크 구간 확대 차트
├── stats_summary.csv        # 통계 요약 (mean, max, min, p95, std)
└── REPORT.md                # 최종 Markdown 리포트
```

---

## 6. Supported Metrics

### MySQL (`oci_mysql_database`) - 22 metrics

| Category | Metrics |
|----------|---------|
| Performance | CPUUtilization, MemoryUtilization, OCPUsUsed, OCPUsAllocated |
| Memory | MemoryUsed, MemoryAllocated |
| Connections | ActiveConnections, CurrentConnections |
| Query | Statements, StatementLatency |
| Disk I/O | DbVolumeReadOperations, DbVolumeWriteOperations, DbVolumeReadBytes, DbVolumeWriteBytes, DbVolumeUtilization |
| Network | NetworkReceiveBytes, NetworkTransmitBytes |
| Storage | StorageUsed, StorageAllocated |
| Backup | BackupSize, BackupTime, BackupFailure |

### PostgreSQL (`oci_postgresql`) - 15 metrics

| Category | Metrics |
|----------|---------|
| Performance | CpuUtilization, MemoryUtilization, BufferCacheHitRatio |
| Connections | Connections |
| Safety | Deadlocks, TxidWrapLimit |
| Disk I/O | ReadIops, WriteIops, ReadLatency, WriteLatency, ReadThroughput, WriteThroughput |
| Storage | DataUsedStorage, UsedStorage, WalUsedStorage |

> PostgreSQL HA 환경에서는 PRIMARY / READ_REPLICA 노드별로 분리 수집됩니다.

---

## 7. File Structure

```
oci-db-metric-report/
├── README.md                 # 이 문서
├── app.py                    # Flask 웹 서비스 (포트 5050)
├── templates/
│   ├── index.html            # 메인 대시보드 (설정/진행률/결과)
│   └── report.html           # 리포트 뷰어
├── config.env.example        # CLI용 설정 템플릿
├── fetch_metrics.sh          # OCI Monitoring API 메트릭 수집
├── generate_charts.py        # 차트 3종 생성 (Overview/Detail/Zoom)
├── generate_report.sh        # All-in-One 리포트 생성 (CLI)
├── requirements.txt          # Python 의존성 (flask, pandas, matplotlib)
├── .gitignore                # output/, config.env 제외
└── output/                   # (gitignore) 수집 결과
```

---

## 8. Notes

- **메트릭 이름 주의**: MySQL `CPUUtilization` (대문자) vs PostgreSQL `CpuUtilization` (카멜케이스)
- **IOPS 이름 차이**: MySQL `DbVolumeReadOperations` vs PostgreSQL `ReadIops`
- **시간대**: 모든 시각은 **UTC** 기준
- **시간 형식**: `YYYY-MM-DDTHH:MM:SSZ` (Web UI는 자동 변환)

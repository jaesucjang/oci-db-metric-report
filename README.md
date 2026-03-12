# OCI DB Metric Report Tool

OCI Database (PostgreSQL / MySQL HeatWave) 모니터링 메트릭을 시계열로 수집하고,
차트 + 통계 + Markdown 리포트를 자동 생성하는 도구입니다.

**CLI** + **Web UI** 두 가지 방식 지원.

## Quick Start (Web UI)

```bash
git clone https://github.com/jaesucjang/oci-db-metric-report.git
cd oci-db-metric-report
pip3 install -r requirements.txt
python3 app.py
# → http://localhost:5050 접속
```

1. **Sample Configs** 에서 MySQL/PostgreSQL 샘플 클릭 → 자동 입력
2. Compartment ID, 시간 범위 수정
3. **Generate Report** 클릭 → 수집 → 차트 → 리포트 자동 생성
4. 결과 차트/통계/다운로드 확인

## Quick Start (CLI)

```bash
# 1. Clone
git clone https://github.com/jaesucjang/oci-db-metric-report.git
cd oci-db-metric-report

# 2. Python 의존성 설치
pip3 install -r requirements.txt

# 3. 설정 파일 생성
cp config.env.example config.env
vi config.env   # OCI 프로파일, 컴파트먼트 ID, 시간 범위 입력

# 4. 리포트 생성 (수집 → 차트 → MD 리포트 원스텝)
./generate_report.sh
```

## Prerequisites

| Tool | Version | 설치 확인 |
|------|---------|----------|
| OCI CLI | 3.x+ | `oci --version` |
| Python 3 | 3.8+ | `python3 --version` |
| jq | 1.6+ | `jq --version` |
| pandas | 1.5+ | `pip3 install pandas` |
| matplotlib | 3.6+ | `pip3 install matplotlib` |

### OCI CLI 설정

```bash
# OCI CLI 설치 (아직 없다면)
bash -c "$(curl -L https://raw.githubusercontent.com/oracle/oci-cli/master/scripts/install/install.sh)"

# OCI 프로파일 설정
oci setup config
# → ~/.oci/config 에 프로파일 생성됨
```

## Configuration (`config.env`)

`config.env.example`을 복사하여 편집합니다:

```bash
cp config.env.example config.env
```

| 항목 | 필수 | 설명 | 예시 |
|------|------|------|------|
| `OCI_CONFIG_FILE` | | OCI config 파일 경로 | `~/.oci/config` |
| `OCI_PROFILE` | | OCI CLI 프로파일 이름 | `DEFAULT` |
| `COMPARTMENT_ID` | **필수** | Compartment OCID | `ocid1.compartment.oc1..aaa...` |
| `NAMESPACE` | **필수** | DB 서비스 네임스페이스 | `oci_mysql_database` or `oci_postgresql` |
| `INTERVAL` | | 수집 간격 | `1m`, `5m`, `1h` |
| `START_TIME` | **필수** | 수집 시작 시각 (UTC) | `2026-03-12T06:00:00Z` |
| `END_TIME` | **필수** | 수집 종료 시각 (UTC) | `2026-03-12T07:00:00Z` |
| `BENCH_START` | | 벤치마크 시작 시각 (차트 하이라이트) | `2026-03-12T06:29:00Z` |
| `BENCH_END` | | 벤치마크 종료 시각 | `2026-03-12T06:33:00Z` |
| `REPORT_TITLE` | | 리포트 제목 | `OCI MySQL Metric Report` |

## Usage

### 방법 1: All-in-One (수집 + 차트 + 리포트)

```bash
./generate_report.sh
```

### 방법 2: 단계별 실행

```bash
# Step 1: 메트릭 수집
./fetch_metrics.sh

# Step 2: 차트 생성
python3 generate_charts.py output/metrics_oci_mysql_database_20260312_120000

# Step 3: 리포트 생성 (기존 데이터 사용)
./generate_report.sh output/metrics_oci_mysql_database_20260312_120000
```

### 방법 3: 다른 config 파일 사용

```bash
./generate_report.sh /path/to/another-config.env
```

## Output

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

## Supported Metrics

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

## Web UI Features

- **Sample Configs**: MySQL Benchmark / PostgreSQL Benchmark / Load Test 템플릿 원클릭 로드
- **Real-time Progress**: 수집 → 차트 → 리포트 단계별 진행률 표시
- **Live Log**: OCI CLI 실행 로그 실시간 확인
- **Interactive Charts**: 차트 클릭 시 확대 모달
- **Statistics Table**: Mean/Max/Min/P95/Std 테이블
- **Download**: 차트 PNG, 통계 CSV, Markdown 리포트 개별 다운로드
- **History**: 이전 실행 내역 조회

## File Structure

```
oci-db-metric-report/
├── README.md                 # 이 문서
├── app.py                    # Flask 웹 서비스
├── templates/
│   ├── index.html            # 메인 대시보드
│   └── report.html           # 리포트 뷰어
├── config.env.example        # CLI용 설정 템플릿
├── fetch_metrics.sh          # 메트릭 수집 스크립트
├── generate_charts.py        # 차트 생성 (Python)
├── generate_report.sh        # All-in-One 리포트 생성 (CLI)
├── requirements.txt          # Python 의존성
├── .gitignore                # output/, config.env 제외
└── output/                   # (gitignore) 수집 결과
```

## Notes

- **메트릭 이름 주의**: MySQL은 `CPUUtilization` (대문자), PostgreSQL은 `CpuUtilization` (카멜케이스)
- **IOPS 이름 차이**: MySQL은 `DbVolumeReadOperations`, PostgreSQL은 `ReadIops`
- **시간대**: 모든 시각은 UTC 기준
- **OCI CLI 권한**: Compartment에 대한 `monitoring metric read` 권한 필요

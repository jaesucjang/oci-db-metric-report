#!/bin/bash
# ============================================================
# generate_report.sh - OCI DB Metric Report Generator (All-in-One)
# ============================================================
# Collects metrics, generates charts, and produces a Markdown report.
#
# Usage:
#   ./generate_report.sh                     # uses config.env
#   ./generate_report.sh /path/to/config.env
#   ./generate_report.sh <metrics_dir>       # skip collection, use existing data
# ============================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

# --- Detect mode ---
# If argument is a directory, skip collection
if [ -n "${1:-}" ] && [ -d "$1" ]; then
  echo "[Mode] Using existing metrics: $1"
  METRICS_DIR="$1"
  META_FILE="${METRICS_DIR}/_metadata.json"
  if [ ! -f "$META_FILE" ]; then
    echo "ERROR: _metadata.json not found in $METRICS_DIR"
    echo "  Run fetch_metrics.sh first to collect metrics."
    exit 1
  fi
else
  # --- Step 1: Collect metrics ---
  CONFIG_FILE="${1:-${SCRIPT_DIR}/config.env}"
  if [ ! -f "$CONFIG_FILE" ]; then
    echo "ERROR: Config file not found: $CONFIG_FILE"
    echo "  cp config.env.example config.env  # then edit"
    exit 1
  fi
  echo "============================================================"
  echo " Step 1/3: Collecting metrics..."
  echo "============================================================"
  bash "${SCRIPT_DIR}/fetch_metrics.sh" "$CONFIG_FILE"

  # Find the latest output directory
  METRICS_DIR=$(ls -dt "${SCRIPT_DIR}"/output/metrics_* 2>/dev/null | head -1)
  META_FILE="${METRICS_DIR}/_metadata.json"
fi

if [ ! -d "$METRICS_DIR" ]; then
  echo "ERROR: No metrics directory found."
  exit 1
fi

# --- Step 2: Generate charts ---
echo ""
echo "============================================================"
echo " Step 2/3: Generating charts..."
echo "============================================================"

# Check Python deps
if ! python3 -c "import pandas, matplotlib" 2>/dev/null; then
  echo "Installing Python dependencies..."
  pip3 install pandas matplotlib --quiet 2>/dev/null || \
  pip3 install pandas matplotlib --quiet --break-system-packages 2>/dev/null
fi

python3 "${SCRIPT_DIR}/generate_charts.py" "$METRICS_DIR"

# --- Step 3: Generate Markdown report ---
echo ""
echo "============================================================"
echo " Step 3/3: Generating Markdown report..."
echo "============================================================"

# Read metadata
NS=$(jq -r '.namespace // "unknown"' "$META_FILE")
START=$(jq -r '.start_time // ""' "$META_FILE")
END=$(jq -r '.end_time // ""' "$META_FILE")
BENCH_S=$(jq -r '.bench_start // ""' "$META_FILE")
BENCH_E=$(jq -r '.bench_end // ""' "$META_FILE")
TITLE=$(jq -r '.report_title // "OCI DB Metric Report"' "$META_FILE")
COLLECTED=$(jq -r '.collected_at // ""' "$META_FILE")
PROFILE=$(jq -r '.oci_profile // "DEFAULT"' "$META_FILE")
INTERVAL_VAL=$(jq -r '.interval // "1m"' "$META_FILE")

REPORT_FILE="${METRICS_DIR}/REPORT.md"

# --- Count metrics ---
CSV_COUNT=$(find "$METRICS_DIR" -name "*.csv" ! -name "_*" ! -name "stats_*" -size +0 | wc -l | tr -d ' ')
EMPTY_COUNT=$(find "$METRICS_DIR" -name "*.csv" ! -name "_*" ! -name "stats_*" -empty | wc -l | tr -d ' ')

cat > "$REPORT_FILE" <<REPORTEOF
# ${TITLE}

**Generated**: ${COLLECTED}
**Namespace**: \`${NS}\`
**Period**: ${START} ~ ${END}
**Interval**: ${INTERVAL_VAL}
**OCI Profile**: ${PROFILE}

---

## 1. Collection Summary

| Item | Value |
|------|-------|
| Metrics with data | ${CSV_COUNT} |
| Metrics empty | ${EMPTY_COUNT} |
| Data points per metric | ~$(head -1 "${METRICS_DIR}/"*.csv 2>/dev/null | wc -l | tr -d ' ') |
REPORTEOF

# Benchmark info
if [ -n "$BENCH_S" ] && [ "$BENCH_S" != "null" ]; then
  cat >> "$REPORT_FILE" <<BENCHEOF
| Benchmark window | ${BENCH_S} ~ ${BENCH_E} |
BENCHEOF
fi

cat >> "$REPORT_FILE" <<CHARTSEOF

---

## 2. Charts

### 2-1. Overview (All metrics by category)

![Overview](chart_overview.png)

### 2-2. Individual Metric Details

![Detail](chart_detail.png)

CHARTSEOF

if [ -n "$BENCH_S" ] && [ "$BENCH_S" != "null" ]; then
  cat >> "$REPORT_FILE" <<ZOOMEOF
### 2-3. Benchmark Zoom-in

![Zoom](chart_zoom.png)

ZOOMEOF
fi

# --- Stats table ---
cat >> "$REPORT_FILE" <<STATSHEADER

---

## 3. Statistics Summary

| Metric | Mean | Max | Min | P95 | Std |
|--------|------|-----|-----|-----|-----|
STATSHEADER

if [ -f "${METRICS_DIR}/stats_summary.csv" ]; then
  tail -n +2 "${METRICS_DIR}/stats_summary.csv" | while IFS=',' read -r metric mean max min p95 std count; do
    printf "| %s | %s | %s | %s | %s | %s |\n" "$metric" "$mean" "$max" "$min" "$p95" "$std" >> "$REPORT_FILE"
  done
fi

# --- Metric list ---
cat >> "$REPORT_FILE" <<LISTEOF

---

## 4. Collected Metrics

LISTEOF

if [ "$NS" = "oci_mysql_database" ]; then
  cat >> "$REPORT_FILE" <<MYSQLEOF
| # | Metric Name | Category | Description |
|---|-------------|----------|-------------|
| 1 | CPUUtilization | Performance | CPU usage (%) |
| 2 | MemoryUtilization | Performance | Memory usage (%) |
| 3 | MemoryUsed | Memory | Used memory (GB) |
| 4 | MemoryAllocated | Memory | Allocated memory (GB) |
| 5 | OCPUsUsed | Performance | OCPU usage |
| 6 | OCPUsAllocated | Performance | Allocated OCPUs |
| 7 | ActiveConnections | Connections | Active connections |
| 8 | CurrentConnections | Connections | Total connections |
| 9 | Statements | Query | Cumulative SQL statements |
| 10 | StatementLatency | Query | Avg statement latency (us) |
| 11 | DbVolumeReadOperations | Disk I/O | Read IOPS |
| 12 | DbVolumeWriteOperations | Disk I/O | Write IOPS |
| 13 | DbVolumeReadBytes | Disk I/O | Read throughput (bytes) |
| 14 | DbVolumeWriteBytes | Disk I/O | Write throughput (bytes) |
| 15 | DbVolumeUtilization | Storage | Volume utilization (%) |
| 16 | NetworkReceiveBytes | Network | Network receive (bytes) |
| 17 | NetworkTransmitBytes | Network | Network transmit (bytes) |
| 18 | StorageUsed | Storage | Used storage (GB) |
| 19 | StorageAllocated | Storage | Allocated storage (GB) |
| 20 | BackupSize | Backup | Backup size (bytes) |
| 21 | BackupTime | Backup | Backup duration (seconds) |
| 22 | BackupFailure | Backup | Backup failure count |
MYSQLEOF

elif [ "$NS" = "oci_postgresql" ]; then
  cat >> "$REPORT_FILE" <<PGEOF
| # | Metric Name | Category | Description |
|---|-------------|----------|-------------|
| 1 | CpuUtilization | Performance | CPU usage (%) |
| 2 | MemoryUtilization | Performance | Memory usage (%) |
| 3 | Connections | Connections | Current connections |
| 4 | BufferCacheHitRatio | Performance | Buffer cache hit ratio (%) |
| 5 | Deadlocks | Safety | Deadlock count |
| 6 | TxidWrapLimit | Safety | TxID wraparound limit |
| 7 | ReadIops | Disk I/O | Read IOPS |
| 8 | WriteIops | Disk I/O | Write IOPS |
| 9 | ReadLatency | Disk I/O | Read latency (ms) |
| 10 | WriteLatency | Disk I/O | Write latency (ms) |
| 11 | ReadThroughput | Disk I/O | Read throughput (bytes/s) |
| 12 | WriteThroughput | Disk I/O | Write throughput (bytes/s) |
| 13 | DataUsedStorage | Storage | Data used storage (bytes) |
| 14 | UsedStorage | Storage | Total used storage (bytes) |
| 15 | WalUsedStorage | Storage | WAL used storage (bytes) |
PGEOF
fi

# --- Footer ---
cat >> "$REPORT_FILE" <<FOOTER

---

## 5. Reproduction

\`\`\`bash
# 1. Edit config
cp config.env.example config.env
vi config.env

# 2. Run all-in-one
./generate_report.sh

# Or step-by-step:
./fetch_metrics.sh                  # collect
python3 generate_charts.py <dir>    # chart
./generate_report.sh <dir>          # report only
\`\`\`

---

*Generated by [oci-db-metric-report](https://github.com/jaesujan/oci-db-metric-report)*
FOOTER

echo "  Report: $REPORT_FILE"
echo ""
echo "============================================================"
echo " All done!"
echo "============================================================"
echo " Output directory : $METRICS_DIR"
echo " Report           : $REPORT_FILE"
echo " Charts           : chart_overview.png, chart_detail.png, chart_zoom.png"
echo " Stats CSV        : stats_summary.csv"
echo "============================================================"

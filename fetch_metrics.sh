#!/bin/bash
# ============================================================
# fetch_metrics.sh - OCI DB Monitoring Metrics Collector
# ============================================================
# Collects all available metrics as time-series JSON + CSV
# Supports: oci_postgresql, oci_mysql_database
#
# Usage:
#   ./fetch_metrics.sh                    # uses config.env
#   ./fetch_metrics.sh /path/to/config.env
# ============================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
CONFIG_FILE="${1:-${SCRIPT_DIR}/config.env}"

# --- Load config ---
if [ ! -f "$CONFIG_FILE" ]; then
  echo "ERROR: Config file not found: $CONFIG_FILE"
  echo "  cp config.env.example config.env  # then edit"
  exit 1
fi
source "$CONFIG_FILE"

# --- Validate required fields ---
: "${COMPARTMENT_ID:?'COMPARTMENT_ID is required in config.env'}"
: "${NAMESPACE:?'NAMESPACE is required in config.env'}"
: "${START_TIME:?'START_TIME is required in config.env'}"
: "${END_TIME:?'END_TIME is required in config.env'}"
INTERVAL="${INTERVAL:-1m}"

# --- OCI CLI profile ---
OCI_PROFILE_ARG=""
if [ -n "${OCI_PROFILE:-}" ] && [ "$OCI_PROFILE" != "DEFAULT" ]; then
  OCI_PROFILE_ARG="--profile $OCI_PROFILE"
fi
OCI_CONFIG_ARG=""
if [ -n "${OCI_CONFIG_FILE:-}" ] && [ "$OCI_CONFIG_FILE" != "~/.oci/config" ]; then
  OCI_CONFIG_ARG="--config-file $OCI_CONFIG_FILE"
fi

# --- Output directory ---
if [ -z "${OUTPUT_DIR:-}" ]; then
  OUTPUT_DIR="${SCRIPT_DIR}/output/metrics_${NAMESPACE}_$(date +%Y%m%d_%H%M%S)"
fi
mkdir -p "$OUTPUT_DIR"

echo "============================================================"
echo " OCI DB Metric Collector"
echo "============================================================"
echo " Namespace  : $NAMESPACE"
echo " Interval   : $INTERVAL"
echo " Period     : $START_TIME ~ $END_TIME"
echo " Compartment: ${COMPARTMENT_ID:0:30}..."
echo " Profile    : ${OCI_PROFILE:-DEFAULT}"
echo " Output     : $OUTPUT_DIR/"
echo "============================================================"
echo ""

fetch_metric() {
  local metric_name="$1"
  local query_text="$2"
  local out_prefix="$3"

  echo "  Fetching ${metric_name}..."
  oci monitoring metric-data summarize-metrics-data \
    $OCI_CONFIG_ARG $OCI_PROFILE_ARG \
    --compartment-id "$COMPARTMENT_ID" \
    --namespace "$NAMESPACE" \
    --query-text "$query_text" \
    --start-time "$START_TIME" --end-time "$END_TIME" \
    --output json > "${OUTPUT_DIR}/${out_prefix}.json" 2>&1

  # JSON -> CSV
  jq -r '.data[0]."aggregated-datapoints"[]? | [.timestamp, .value] | @csv' \
    "${OUTPUT_DIR}/${out_prefix}.json" > "${OUTPUT_DIR}/${out_prefix}.csv" 2>/dev/null
}

# --- PostgreSQL ---
if [ "$NAMESPACE" = "oci_postgresql" ]; then
  METRICS=(CpuUtilization MemoryUtilization Connections BufferCacheHitRatio \
           Deadlocks TxidWrapLimit \
           ReadIops WriteIops ReadLatency WriteLatency \
           ReadThroughput WriteThroughput \
           DataUsedStorage UsedStorage WalUsedStorage)

  ROLES=(PRIMARY READ_REPLICA)

  for ROLE in "${ROLES[@]}"; do
    echo "--- ${ROLE} ---"
    for M in "${METRICS[@]}"; do
      fetch_metric "$M" "${M}[${INTERVAL}]{dbInstanceRole = \"${ROLE}\"}.mean()" "${ROLE}_${M}"
    done
  done

# --- MySQL ---
elif [ "$NAMESPACE" = "oci_mysql_database" ]; then
  METRICS=(CPUUtilization MemoryUtilization MemoryUsed MemoryAllocated \
           OCPUsUsed OCPUsAllocated \
           ActiveConnections CurrentConnections \
           Statements StatementLatency \
           DbVolumeReadOperations DbVolumeWriteOperations \
           DbVolumeReadBytes DbVolumeWriteBytes DbVolumeUtilization \
           NetworkReceiveBytes NetworkTransmitBytes \
           StorageUsed StorageAllocated \
           BackupSize BackupTime BackupFailure)

  echo "--- MySQL DB System ---"
  for M in "${METRICS[@]}"; do
    fetch_metric "$M" "${M}[${INTERVAL}].mean()" "$M"
  done

else
  echo "ERROR: Unknown namespace: $NAMESPACE"
  echo "Supported: oci_postgresql, oci_mysql_database"
  exit 1
fi

# --- Save metadata ---
cat > "${OUTPUT_DIR}/_metadata.json" <<EOFMETA
{
  "namespace": "$NAMESPACE",
  "compartment_id": "$COMPARTMENT_ID",
  "start_time": "$START_TIME",
  "end_time": "$END_TIME",
  "interval": "$INTERVAL",
  "bench_start": "${BENCH_START:-}",
  "bench_end": "${BENCH_END:-}",
  "report_title": "${REPORT_TITLE:-OCI DB Metric Report}",
  "collected_at": "$(date -u +%Y-%m-%dT%H:%M:%SZ)",
  "oci_profile": "${OCI_PROFILE:-DEFAULT}"
}
EOFMETA

echo ""
echo "============================================================"
echo " Done! Collected to: $OUTPUT_DIR/"
echo "============================================================"
echo " JSON files : ${OUTPUT_DIR}/*.json"
echo " CSV files  : ${OUTPUT_DIR}/*.csv"
echo " Metadata   : ${OUTPUT_DIR}/_metadata.json"
echo ""
echo " Next step  : ./generate_report.sh $OUTPUT_DIR"
echo "============================================================"

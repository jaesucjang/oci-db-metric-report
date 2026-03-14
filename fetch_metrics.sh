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
MISSING=""
[ -z "${COMPARTMENT_ID:-}" ] && MISSING="$MISSING COMPARTMENT_ID"
[ -z "${NAMESPACE:-}" ] && MISSING="$MISSING NAMESPACE"
[ -z "${START_TIME:-}" ] && MISSING="$MISSING START_TIME"
[ -z "${END_TIME:-}" ] && MISSING="$MISSING END_TIME"
if [ -n "$MISSING" ]; then
  echo "ERROR: Missing required fields:$MISSING"
  exit 1
fi
INTERVAL="${INTERVAL:-1m}"

# --- OCI CLI pre-check ---
echo "[Pre-check] Verifying OCI CLI..."
if ! command -v oci &>/dev/null; then
  echo "ERROR: OCI CLI not found. Install: https://docs.oracle.com/en-us/iaas/Content/API/SDKDocs/cliinstall.htm"
  exit 1
fi
echo "  OCI CLI: $(which oci)"

# --- OCI config file check ---
OCI_PROFILE_ARG=""
if [ -n "${OCI_PROFILE:-}" ] && [ "$OCI_PROFILE" != "DEFAULT" ]; then
  OCI_PROFILE_ARG="--profile $OCI_PROFILE"
fi
OCI_CONFIG_ARG=""
OCI_CONFIG_PATH="${OCI_CONFIG_FILE:-$HOME/.oci/config}"
OCI_CONFIG_PATH="${OCI_CONFIG_PATH/#\~/$HOME}"
if [ -n "${OCI_CONFIG_FILE:-}" ] && [ "$OCI_CONFIG_FILE" != "~/.oci/config" ]; then
  OCI_CONFIG_ARG="--config-file $OCI_CONFIG_FILE"
fi

echo "[Pre-check] OCI config file: $OCI_CONFIG_PATH"
if [ ! -f "$OCI_CONFIG_PATH" ]; then
  echo "ERROR: OCI config file not found: $OCI_CONFIG_PATH"
  echo "  Run 'oci setup config' or register config in OCI Settings tab."
  exit 1
fi
echo "  Config file: OK"

# Check key_file in config
KEY_FILE=$(grep -A20 "^\[${OCI_PROFILE:-DEFAULT}\]" "$OCI_CONFIG_PATH" 2>/dev/null | grep "^key_file" | head -1 | cut -d= -f2 | tr -d ' ')
KEY_FILE="${KEY_FILE/#\~/$HOME}"
if [ -n "$KEY_FILE" ]; then
  echo "[Pre-check] API key file: $KEY_FILE"
  if [ ! -f "$KEY_FILE" ]; then
    echo "ERROR: API key file not found: $KEY_FILE"
    echo "  Upload the key file or fix key_file path in OCI config."
    exit 1
  fi
  echo "  Key file: OK"
fi

# --- Quick auth test ---
echo "[Pre-check] Testing OCI authentication..."
AUTH_TEST=$(oci iam region list $OCI_CONFIG_ARG $OCI_PROFILE_ARG --output json 2>&1)
if [ $? -ne 0 ]; then
  echo "ERROR: OCI authentication failed!"
  echo "--- OCI CLI output ---"
  echo "$AUTH_TEST"
  echo "---"
  echo ""
  echo "Possible causes:"
  echo "  1. API key not registered in OCI console (fingerprint mismatch)"
  echo "  2. key_file path incorrect"
  echo "  3. Tenancy/User OCID incorrect"
  echo "  4. Config profile name mismatch"
  exit 1
fi
echo "  Authentication: OK"

# --- Test compartment access ---
echo "[Pre-check] Testing compartment access: ${COMPARTMENT_ID:0:40}..."
COMP_TEST=$(oci monitoring metric list \
  $OCI_CONFIG_ARG $OCI_PROFILE_ARG \
  --compartment-id "$COMPARTMENT_ID" \
  --namespace "$NAMESPACE" \
  --output json 2>&1)
COMP_RC=$?
if [ $COMP_RC -ne 0 ]; then
  echo "ERROR: Cannot access compartment or namespace!"
  echo "--- OCI CLI output ---"
  echo "$COMP_TEST"
  echo "---"
  echo ""
  echo "Possible causes:"
  echo "  1. Compartment OCID incorrect or does not exist"
  echo "  2. No permission: need 'monitoring metric read' on this compartment"
  echo "  3. Namespace '$NAMESPACE' has no resources in this compartment"
  echo "  4. Wrong region (check region in OCI config)"
  exit 1
fi
METRIC_COUNT=$(echo "$COMP_TEST" | jq '.data | length' 2>/dev/null || echo "0")
echo "  Compartment access: OK ($METRIC_COUNT metrics available)"
if [ "$METRIC_COUNT" = "0" ]; then
  echo "WARNING: No metrics found for namespace '$NAMESPACE' in this compartment."
  echo "  This compartment may not have any $NAMESPACE DB resources."
  echo "  Check compartment OCID and ensure DB instances exist."
  exit 1
fi

# --- Output directory ---
if [ -z "${OUTPUT_DIR:-}" ]; then
  OUTPUT_DIR="${SCRIPT_DIR}/output/metrics_${NAMESPACE}_$(date +%Y%m%d_%H%M%S)"
fi
mkdir -p "$OUTPUT_DIR"

echo ""
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

SUCCESS_COUNT=0
FAIL_COUNT=0
EMPTY_COUNT=0

fetch_metric() {
  local metric_name="$1"
  local query_text="$2"
  local out_prefix="$3"

  echo -n "  Fetching ${metric_name}..."
  local OUTPUT
  OUTPUT=$(oci monitoring metric-data summarize-metrics-data \
    $OCI_CONFIG_ARG $OCI_PROFILE_ARG \
    --compartment-id "$COMPARTMENT_ID" \
    --namespace "$NAMESPACE" \
    --query-text "$query_text" \
    --start-time "$START_TIME" --end-time "$END_TIME" \
    --output json 2>&1)
  local RC=$?

  if [ $RC -ne 0 ]; then
    echo " FAILED"
    echo "    Error: $(echo "$OUTPUT" | head -3)"
    echo "$OUTPUT" > "${OUTPUT_DIR}/${out_prefix}.json"
    FAIL_COUNT=$((FAIL_COUNT + 1))
    return
  fi

  echo "$OUTPUT" > "${OUTPUT_DIR}/${out_prefix}.json"

  # JSON -> CSV
  local CSV_DATA
  CSV_DATA=$(jq -r '.data[0]."aggregated-datapoints"[]? | [.timestamp, .value] | @csv' \
    "${OUTPUT_DIR}/${out_prefix}.json" 2>/dev/null)

  if [ -z "$CSV_DATA" ]; then
    echo " OK (no data points)"
    EMPTY_COUNT=$((EMPTY_COUNT + 1))
  else
    local POINTS
    POINTS=$(echo "$CSV_DATA" | wc -l | tr -d ' ')
    echo " OK ($POINTS points)"
    SUCCESS_COUNT=$((SUCCESS_COUNT + 1))
  fi
  echo "$CSV_DATA" > "${OUTPUT_DIR}/${out_prefix}.csv"
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

  # Read Replica only metrics
  REPLICA_ONLY_METRICS=(ChannelLag ChannelFailure)

  # Detect Read Replicas by checking ChannelLag metric
  REPLICA_NAMES=$(oci monitoring metric list \
    $OCI_CONFIG_ARG $OCI_PROFILE_ARG \
    --compartment-id "$COMPARTMENT_ID" \
    --namespace "$NAMESPACE" \
    --name "ChannelLag" \
    --output json 2>/dev/null | jq -r '.data[].dimensions.resourceName // empty' 2>/dev/null | sort -u)

  # Determine resource filter for Source DB System
  # If replicas exist, we need to exclude them from Source queries
  SOURCE_FILTER=""
  if [ -n "${RESOURCE_NAME:-}" ]; then
    SOURCE_FILTER="{resourceName = \"${RESOURCE_NAME}\"}"
  fi

  echo "--- MySQL Source DB System ---"
  for M in "${METRICS[@]}"; do
    if [ -n "$SOURCE_FILTER" ]; then
      fetch_metric "$M" "${M}[${INTERVAL}]${SOURCE_FILTER}.mean()" "$M"
    else
      fetch_metric "$M" "${M}[${INTERVAL}].mean()" "$M"
    fi
  done

  # Fetch Read Replica metrics
  if [ -n "$REPLICA_NAMES" ]; then
    for RNAME in $REPLICA_NAMES; do
      echo ""
      echo "--- MySQL Read Replica: ${RNAME} ---"
      # Common metrics for replica
      for M in "${METRICS[@]}"; do
        # Skip backup metrics for replicas
        case "$M" in BackupSize|BackupTime|BackupFailure) continue ;; esac
        fetch_metric "REPLICA_${M}" "${M}[${INTERVAL}]{resourceName = \"${RNAME}\"}.mean()" "REPLICA_${M}"
      done
      # Replica-only metrics
      for M in "${REPLICA_ONLY_METRICS[@]}"; do
        fetch_metric "REPLICA_${M}" "${M}[${INTERVAL}]{resourceName = \"${RNAME}\"}.mean()" "REPLICA_${M}"
      done
    done
  else
    echo "  (No Read Replicas detected)"
  fi

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
echo " Collection Summary"
echo "============================================================"
echo " Success (with data) : $SUCCESS_COUNT"
echo " Success (empty)     : $EMPTY_COUNT"
echo " Failed              : $FAIL_COUNT"
echo " Output              : $OUTPUT_DIR/"
echo "============================================================"

if [ $SUCCESS_COUNT -eq 0 ] && [ $FAIL_COUNT -gt 0 ]; then
  echo ""
  echo "ERROR: All metric collections failed!"
  echo "Check the error messages above."
  exit 1
fi

if [ $SUCCESS_COUNT -eq 0 ] && [ $EMPTY_COUNT -gt 0 ]; then
  echo ""
  echo "WARNING: All metrics returned empty data."
  echo "Possible causes:"
  echo "  1. Time range has no data (DB was not running during this period)"
  echo "  2. Wrong compartment (DB is in a different compartment)"
  echo "  3. Metrics not yet available (wait a few minutes after DB creation)"
fi

echo ""
echo " Next step  : ./generate_report.sh $OUTPUT_DIR"
echo "============================================================"

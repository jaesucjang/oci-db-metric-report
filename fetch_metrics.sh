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

MAX_PARALLEL=${MAX_PARALLEL:-10}
STATUS_DIR=$(mktemp -d)

fetch_metric_worker() {
  local metric_name="$1"
  local query_text="$2"
  local out_prefix="$3"

  local OUTPUT
  OUTPUT=$(oci monitoring metric-data summarize-metrics-data \
    $OCI_CONFIG_ARG $OCI_PROFILE_ARG \
    --compartment-id "$COMPARTMENT_ID" \
    --namespace "$NAMESPACE" \
    --query-text "$query_text" \
    --start-time "$START_TIME" --end-time "$END_TIME" \
    --output json 2>&1)
  local RC=$?

  echo "$OUTPUT" > "${OUTPUT_DIR}/${out_prefix}.json"

  if [ $RC -ne 0 ]; then
    echo "FAIL" > "${STATUS_DIR}/${out_prefix}"
    echo "  ${metric_name}... FAILED"
    return
  fi

  local CSV_DATA
  CSV_DATA=$(jq -r '.data[0]."aggregated-datapoints"[]? | [.timestamp, .value] | @csv' \
    "${OUTPUT_DIR}/${out_prefix}.json" 2>/dev/null)
  echo "$CSV_DATA" > "${OUTPUT_DIR}/${out_prefix}.csv"

  if [ -z "$CSV_DATA" ]; then
    echo "EMPTY" > "${STATUS_DIR}/${out_prefix}"
    echo "  ${metric_name}... OK (empty)"
  else
    local POINTS=$(echo "$CSV_DATA" | wc -l | tr -d ' ')
    echo "SUCCESS" > "${STATUS_DIR}/${out_prefix}"
    echo "  ${metric_name}... OK ($POINTS pts)"
  fi
}

# --- Build job list: metric_name|query_text|out_prefix ---
JOBS_LIST=()

if [ "$NAMESPACE" = "oci_postgresql" ]; then
  METRICS=(CpuUtilization MemoryUtilization Connections BufferCacheHitRatio \
           Deadlocks TxidWrapLimit \
           ReadIops WriteIops ReadLatency WriteLatency \
           ReadThroughput WriteThroughput \
           DataUsedStorage UsedStorage WalUsedStorage)

  for ROLE in PRIMARY READ_REPLICA; do
    for M in "${METRICS[@]}"; do
      JOBS_LIST+=("${M}|${M}[${INTERVAL}]{dbInstanceRole = \"${ROLE}\"}.mean()|${ROLE}_${M}")
    done
  done

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

  REPLICA_ONLY_METRICS=(ChannelLag ChannelFailure)

  SOURCE_FILTER=""
  REPLICA_NAMES=""
  if [ -n "${RESOURCE_NAME:-}" ]; then
    SOURCE_FILTER="{resourceName = \"${RESOURCE_NAME}\"}"

    # Get source DB system ID from monitoring metrics
    echo "  Looking up source DB system ID for: ${RESOURCE_NAME}..."
    SOURCE_RID=$(oci monitoring metric list \
      $OCI_CONFIG_ARG $OCI_PROFILE_ARG \
      --compartment-id "$COMPARTMENT_ID" \
      --namespace "$NAMESPACE" \
      --name "CPUUtilization" \
      --output json 2>/dev/null | \
      jq -r --arg rn "$RESOURCE_NAME" \
        '[.data[] | select(.dimensions.resourceName == $rn)] | .[0].dimensions.resourceId // empty' 2>/dev/null)

    if [ -n "$SOURCE_RID" ]; then
      echo "  Source DB System ID: ${SOURCE_RID:0:50}..."
      # List replicas belonging to this specific source DB
      REPLICA_NAMES=$(oci mysql replica list \
        $OCI_CONFIG_ARG $OCI_PROFILE_ARG \
        --compartment-id "$COMPARTMENT_ID" \
        --db-system-id "$SOURCE_RID" \
        --output json 2>/dev/null | \
        jq -r '.data[].display_name // .data[]."display-name" // empty' 2>/dev/null | sort -u)
      echo "  Replicas from MySQL API: $(echo "$REPLICA_NAMES" | tr '\n' ', ')"
    fi
  fi

  # Fallback: discover all replicas from ChannelLag metrics in compartment
  if [ -z "$REPLICA_NAMES" ]; then
    echo "  Falling back to ChannelLag metric discovery (all replicas in compartment)..."
    REPLICA_NAMES=$(oci monitoring metric list \
      $OCI_CONFIG_ARG $OCI_PROFILE_ARG \
      --compartment-id "$COMPARTMENT_ID" \
      --namespace "$NAMESPACE" \
      --name "ChannelLag" \
      --output json 2>/dev/null | jq -r '.data[].dimensions.resourceName // empty' 2>/dev/null | sort -u)
  fi

  for M in "${METRICS[@]}"; do
    JOBS_LIST+=("${M}|${M}[${INTERVAL}]${SOURCE_FILTER}.mean()|${M}")
  done

  if [ -n "$REPLICA_NAMES" ]; then
    RIDX=0
    for RNAME in $REPLICA_NAMES; do
      RIDX=$((RIDX + 1))
      RTAG="REPLICA${RIDX}"
      echo "  Replica #${RIDX}: ${RNAME} → prefix ${RTAG}_"
      for M in "${METRICS[@]}"; do
        case "$M" in BackupSize|BackupTime|BackupFailure) continue ;; esac
        JOBS_LIST+=("${RTAG}_${M}|${M}[${INTERVAL}]{resourceName = \"${RNAME}\"}.mean()|${RTAG}_${M}")
      done
      for M in "${REPLICA_ONLY_METRICS[@]}"; do
        JOBS_LIST+=("${RTAG}_${M}|${M}[${INTERVAL}]{resourceName = \"${RNAME}\"}.mean()|${RTAG}_${M}")
      done
    done
    echo "  Total Read Replicas: ${RIDX}"
  else
    echo "  (No Read Replicas detected)"
  fi
else
  echo "ERROR: Unknown namespace: $NAMESPACE"
  echo "Supported: oci_postgresql, oci_mysql_database"
  exit 1
fi

# --- Run all fetches in parallel ---
TOTAL=${#JOBS_LIST[@]}
echo "Fetching $TOTAL metrics (${MAX_PARALLEL} parallel)..."

RUNNING_PIDS=()
for JOB in "${JOBS_LIST[@]}"; do
  IFS='|' read -r MNAME MQUERY MPREFIX <<< "$JOB"

  fetch_metric_worker "$MNAME" "$MQUERY" "$MPREFIX" &
  RUNNING_PIDS+=($!)

  # Limit concurrency
  while [ ${#RUNNING_PIDS[@]} -ge $MAX_PARALLEL ]; do
    NEW_PIDS=()
    for pid in "${RUNNING_PIDS[@]}"; do
      if kill -0 "$pid" 2>/dev/null; then
        NEW_PIDS+=("$pid")
      fi
    done
    RUNNING_PIDS=("${NEW_PIDS[@]}")
    [ ${#RUNNING_PIDS[@]} -ge $MAX_PARALLEL ] && sleep 0.1
  done
done

# Wait for remaining
wait

# --- Aggregate results ---
SUCCESS_COUNT=$(grep -rl "SUCCESS" "${STATUS_DIR}/" 2>/dev/null | wc -l | tr -d ' ')
FAIL_COUNT=$(grep -rl "FAIL" "${STATUS_DIR}/" 2>/dev/null | wc -l | tr -d ' ')
EMPTY_COUNT=$(grep -rl "EMPTY" "${STATUS_DIR}/" 2>/dev/null | wc -l | tr -d ' ')
rm -rf "$STATUS_DIR"

# --- Save metadata ---
REPLICA_JSON="[]"
if [ -n "${REPLICA_NAMES:-}" ]; then
  REPLICA_JSON="["
  RIDX2=0
  for RNAME in $REPLICA_NAMES; do
    RIDX2=$((RIDX2 + 1))
    [ $RIDX2 -gt 1 ] && REPLICA_JSON="${REPLICA_JSON},"
    REPLICA_JSON="${REPLICA_JSON}{\"index\":${RIDX2},\"name\":\"${RNAME}\"}"
  done
  REPLICA_JSON="${REPLICA_JSON}]"
fi

cat > "${OUTPUT_DIR}/_metadata.json" <<EOFMETA
{
  "namespace": "$NAMESPACE",
  "compartment_id": "$COMPARTMENT_ID",
  "start_time": "$START_TIME",
  "end_time": "$END_TIME",
  "interval": "$INTERVAL",
  "bench_start": "${BENCH_START:-}",
  "bench_end": "${BENCH_END:-}",
  "resource_name": "${RESOURCE_NAME:-}",
  "report_title": "${REPORT_TITLE:-OCI DB Metric Report}",
  "collected_at": "$(date -u +%Y-%m-%dT%H:%M:%SZ)",
  "oci_profile": "${OCI_PROFILE:-DEFAULT}",
  "replicas": ${REPLICA_JSON}
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

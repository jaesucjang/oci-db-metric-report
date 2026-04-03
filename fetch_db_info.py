#!/usr/bin/env python3
"""
Fetch OCI DB system details and configuration parameters.
Extracts DB system OCID from collected metric JSON, then queries OCI API.
"""

import json
import os
import re
import subprocess
import sys
from glob import glob

# Key performance parameters to extract
MYSQL_KEY_PARAMS = {
    "innodb_buffer_pool_size", "max_connections", "innodb_io_capacity",
    "innodb_io_capacity_max", "innodb_log_file_size", "innodb_log_buffer_size",
    "sort_buffer_size", "join_buffer_size", "tmp_table_size", "max_heap_table_size",
    "table_open_cache", "thread_cache_size", "innodb_flush_log_at_trx_commit",
    "sync_binlog", "binlog_expire_logs_seconds",
}

PG_KEY_PARAMS = {
    "shared_buffers", "max_connections", "effective_cache_size", "work_mem",
    "maintenance_work_mem", "wal_buffers", "max_wal_size", "min_wal_size",
    "checkpoint_completion_target", "random_page_cost", "effective_io_concurrency",
    "max_worker_processes", "max_parallel_workers", "max_parallel_workers_per_gather",
}


def extract_resource_id(metrics_dir, metadata=None):
    """Extract resourceId from collected metric JSON files.
    Prioritize Source DB (non-replica) files over replica files.
    """
    # Try non-REPLICA files first (Source DB)
    source_files = []
    replica_files = []
    for jf in sorted(glob(os.path.join(metrics_dir, "*.json"))):
        basename = os.path.basename(jf)
        if basename.startswith("_"):
            continue
        if re.match(r"REPLICA\d*_", basename):
            replica_files.append(jf)
        else:
            source_files.append(jf)

    # Also check metadata for explicit resource_name
    target_name = (metadata or {}).get("resource_name", "")

    for jf in source_files + replica_files:
        try:
            with open(jf) as f:
                data = json.load(f)
            items = data.get("data", [])
            if items:
                dims = items[0].get("dimensions", {})
                rid = dims.get("resourceId", "")
                rname = dims.get("resourceName", "")
                if rid:
                    # If target_name specified, match it
                    if target_name and rname != target_name:
                        continue
                    # Skip replica OCIDs for db-system get
                    if "replica" in rid.lower() and source_files:
                        continue
                    return rid, rname
        except Exception:
            continue

    # Fallback: return any resourceId found
    for jf in source_files + replica_files:
        try:
            with open(jf) as f:
                data = json.load(f)
            items = data.get("data", [])
            if items:
                dims = items[0].get("dimensions", {})
                rid = dims.get("resourceId", "")
                rname = dims.get("resourceName", "")
                if rid:
                    return rid, rname
        except Exception:
            continue
    return None, None


def run_oci(cmd, timeout=30):
    """Run OCI CLI command and return parsed JSON or None."""
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        if result.returncode != 0:
            print(f"  OCI CLI error: {result.stderr[:200]}")
            return None
        return json.loads(result.stdout)
    except subprocess.TimeoutExpired:
        print("  OCI CLI timeout")
        return None
    except Exception as e:
        print(f"  Error: {e}")
        return None


def fetch_mysql_info(db_system_id, oci_args, compartment_id=""):
    """Fetch MySQL DB System details and configuration."""
    info = {"db_type": "MySQL"}

    # Get DB system details
    print(f"  Fetching MySQL DB System: {db_system_id[:50]}...")
    cmd = ["oci", "mysql", "db-system", "get",
           *oci_args, "--db-system-id", db_system_id, "--output", "json"]
    resp = run_oci(cmd)
    if not resp:
        return info

    d = resp.get("data", {})
    shape_name = d.get("shape-name", "")
    data_storage = d.get("data-storage-size-in-gbs", 0)

    info.update({
        "db_system_id": db_system_id,
        "display_name": d.get("display-name", ""),
        "shape": shape_name,
        "storage_gb": data_storage,
        "db_version": d.get("mysql-version", ""),
        "ha_enabled": d.get("is-highly-available", False),
        "lifecycle_state": d.get("lifecycle-state", ""),
        "fault_domain": d.get("fault-domain", ""),
        "availability_domain": d.get("availability-domain", ""),
    })

    # Get OCPU/Memory from Shape API (not in db-system get)
    comp = compartment_id or d.get("compartment-id", "")
    if shape_name and comp:
        print(f"  Fetching Shape info: {shape_name}...")
        cmd = ["oci", "mysql", "shape", "list",
               *oci_args, "--compartment-id", comp, "--output", "json"]
        shapes_resp = run_oci(cmd)
        if shapes_resp:
            for s in shapes_resp.get("data", []):
                if s.get("name") == shape_name:
                    info["ocpu_count"] = s.get("cpu-core-count", "")
                    info["memory_gb"] = s.get("memory-size-in-gbs", "")
                    break

    # Get configuration
    config_id = d.get("configuration-id", "")
    if config_id:
        info["configuration_id"] = config_id
        print(f"  Fetching MySQL Configuration: {config_id[:50]}...")
        cmd = ["oci", "mysql", "configuration", "get",
               *oci_args, "--configuration-id", config_id, "--output", "json"]
        cfg_resp = run_oci(cmd)
        if cfg_resp:
            cfg_data = cfg_resp.get("data", {})
            info["configuration_name"] = cfg_data.get("display-name", "")
            info["shape_name"] = cfg_data.get("shape-name", "")

            # Extract key parameters
            variables = cfg_data.get("variables", {})
            params = {}
            for k, v in variables.items():
                # Convert kebab-case to underscore for matching
                param_name = k.replace("-", "_")
                if param_name in MYSQL_KEY_PARAMS and v is not None:
                    params[param_name] = str(v)
            info["parameters"] = params

    return info


def fetch_pg_info(db_system_id, oci_args):
    """Fetch PostgreSQL DB System details and configuration."""
    info = {"db_type": "PostgreSQL"}

    print(f"  Fetching PostgreSQL DB System: {db_system_id[:50]}...")
    cmd = ["oci", "psql", "db-system", "get",
           *oci_args, "--db-system-id", db_system_id, "--output", "json"]
    resp = run_oci(cmd)
    if not resp:
        return info

    d = resp.get("data", {})
    info.update({
        "db_system_id": db_system_id,
        "display_name": d.get("display-name", ""),
        "shape": d.get("shape", ""),
        "db_version": d.get("db-version", ""),
        "lifecycle_state": d.get("lifecycle-state", ""),
        "instance_count": d.get("instance-count", 1),
        "ha_enabled": (d.get("instance-count", 1) or 1) > 1,
    })

    # Storage details
    storage = d.get("storage-details", {})
    if storage:
        info["storage_gb"] = storage.get("system-type", "")
        info["storage_iops"] = storage.get("iops", "")

    # Shape details
    shape_details = d.get("instances", [])
    if shape_details:
        inst = shape_details[0] if shape_details else {}
        info["availability_domain"] = inst.get("availability-domain", "")

    # OCPU / Memory from shape
    shape = d.get("shape", "")
    info["shape"] = shape
    ocpu = d.get("instance-ocpu-count", "")
    mem = d.get("instance-memory-size-in-gbs", "")
    if ocpu:
        info["ocpu_count"] = ocpu
    if mem:
        info["memory_gb"] = mem

    # Get configuration
    config_id = d.get("config-id", "")
    if config_id:
        info["configuration_id"] = config_id
        print(f"  Fetching PostgreSQL Configuration: {config_id[:50]}...")
        cmd = ["oci", "psql", "configuration", "get",
               *oci_args, "--configuration-id", config_id, "--output", "json"]
        cfg_resp = run_oci(cmd)
        if cfg_resp:
            cfg_data = cfg_resp.get("data", {})
            info["configuration_name"] = cfg_data.get("display-name", "")
            info["shape_name"] = cfg_data.get("shape-name", "")

            # Extract key parameters
            db_config = cfg_data.get("db-configuration-overrides", [])
            config_items = cfg_data.get("configuration-details", {})
            items = config_items.get("items", []) if isinstance(config_items, dict) else []

            params = {}
            # Try overrides first
            for item in db_config:
                name = item.get("config-key", "")
                value = item.get("override-value", "") or item.get("default-value", "")
                if name in PG_KEY_PARAMS and value:
                    params[name] = str(value)
            # Then try configuration details items
            for item in items:
                name = item.get("config-key", "")
                value = item.get("default-value", "")
                if name in PG_KEY_PARAMS and value and name not in params:
                    params[name] = str(value)
            info["parameters"] = params

    return info


def main():
    if len(sys.argv) < 2:
        print("Usage: python3 fetch_db_info.py <metrics_dir> [--config-file PATH] [--profile NAME]")
        sys.exit(1)

    metrics_dir = sys.argv[1]

    # Parse OCI CLI args
    oci_args = []
    i = 2
    while i < len(sys.argv):
        if sys.argv[i] == "--config-file" and i + 1 < len(sys.argv):
            oci_args += ["--config-file", sys.argv[i + 1]]
            i += 2
        elif sys.argv[i] == "--profile" and i + 1 < len(sys.argv):
            oci_args += ["--profile", sys.argv[i + 1]]
            i += 2
        else:
            i += 1

    # Read metadata
    meta_path = os.path.join(metrics_dir, "_metadata.json")
    if not os.path.isfile(meta_path):
        print("ERROR: _metadata.json not found")
        sys.exit(1)

    with open(meta_path) as f:
        meta = json.load(f)

    namespace = meta.get("namespace", "")
    output_path = os.path.join(metrics_dir, "_db_info.json")

    # Extract resource ID from metrics
    resource_id, resource_name = extract_resource_id(metrics_dir, meta)
    if not resource_id:
        print("WARNING: Could not extract resourceId from metrics")
        json.dump({"error": "resourceId not found in metrics"}, open(output_path, "w"), indent=2)
        return

    print(f"  Resource: {resource_name} ({resource_id[:50]}...)")

    # Fetch DB info based on namespace
    compartment_id = meta.get("compartment_id", "")
    if "mysql" in namespace.lower():
        info = fetch_mysql_info(resource_id, oci_args, compartment_id)
    elif "postgresql" in namespace.lower() or "psql" in namespace.lower():
        info = fetch_pg_info(resource_id, oci_args)
    else:
        print(f"WARNING: Unknown namespace: {namespace}")
        info = {"error": f"Unknown namespace: {namespace}"}

    info["resource_name"] = resource_name

    with open(output_path, "w") as f:
        json.dump(info, f, indent=2, default=str)
    print(f"  Saved: {output_path}")


if __name__ == "__main__":
    main()

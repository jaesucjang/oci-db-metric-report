#!/usr/bin/env python3
"""
OCI DB Metric Report - Web Service
Flask app for collecting OCI DB metrics and generating visual reports.
"""

import json
import os
import subprocess
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone, timedelta
from decimal import Decimal
from pathlib import Path
from queue import Queue, Empty

from flask import Flask, render_template, request, jsonify, send_from_directory, redirect, url_for, Response

app = Flask(__name__)

# ============================================================
# OCI Profile Management
# ============================================================
OCI_PROFILES_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".oci_profiles")


def get_profiles_dir():
    os.makedirs(OCI_PROFILES_DIR, exist_ok=True)
    return OCI_PROFILES_DIR


def list_profiles():
    """List saved OCI profiles."""
    pdir = get_profiles_dir()
    profiles = []
    for d in sorted(os.listdir(pdir)):
        meta_path = os.path.join(pdir, d, "meta.json")
        if os.path.isfile(meta_path):
            with open(meta_path) as f:
                meta = json.load(f)
            meta["id"] = d
            # Check if config & key files exist
            meta["has_config"] = os.path.isfile(os.path.join(pdir, d, "config"))
            meta["has_key"] = os.path.isfile(os.path.join(pdir, d, "oci_api_key.pem"))
            profiles.append(meta)
    return profiles


def get_profile_oci_paths(profile_id):
    """Return (config_path, key_path) for a saved profile, or None."""
    pdir = os.path.join(get_profiles_dir(), profile_id)
    config_path = os.path.join(pdir, "config")
    if not os.path.isfile(config_path):
        return None, None
    return config_path, pdir


def normalize_iso_time(val):
    """Ensure datetime string is full ISO 8601 with seconds and Z suffix."""
    if not val:
        return ""
    val = val.strip()
    # Remove trailing Z for parsing
    raw = val.rstrip("Z")
    # Try parsing various formats
    for fmt in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M"):
        try:
            dt = datetime.strptime(raw, fmt)
            return dt.strftime("%Y-%m-%dT%H:%M:%SZ")
        except ValueError:
            continue
    # If already has Z and seconds, return as-is
    if val.endswith("Z") and len(val) >= 20:
        return val
    return val + ":00Z" if not val.endswith("Z") else val
app.config["OUTPUT_BASE"] = os.path.join(os.path.dirname(__file__), "output")
app.config["MAX_CONTENT_LENGTH"] = 16 * 1024 * 1024

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))

# Persistent job tracker
JOBS_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "output", "jobs.json")
jobs = {}
jobs_lock = threading.Lock()


def load_jobs():
    """Load jobs from disk on startup."""
    global jobs
    with jobs_lock:
        if os.path.isfile(JOBS_FILE):
            try:
                with open(JOBS_FILE) as f:
                    jobs = json.load(f)
            except Exception:
                jobs = {}


def save_jobs():
    """Persist jobs to disk (excludes log to keep file small)."""
    with jobs_lock:
        try:
            os.makedirs(os.path.dirname(JOBS_FILE), exist_ok=True)
            data = {}
            for jid, j in jobs.items():
                data[jid] = {k: v for k, v in j.items() if k != "log"}
                data[jid]["log"] = ""  # don't persist full logs
            with open(JOBS_FILE, "w") as f:
                json.dump(data, f, default=str)
        except Exception:
            pass


def run_job(job_id, config):
    """Background job: fetch metrics → generate charts → generate report."""
    jobs[job_id]["status"] = "collecting"
    jobs[job_id]["progress"] = 10

    out_dir = os.path.join(app.config["OUTPUT_BASE"], f"job_{job_id}")
    metrics_dir = os.path.join(out_dir, f"metrics_{config['namespace']}")
    os.makedirs(metrics_dir, exist_ok=True)

    try:
        # --- Step 1: Write temp config ---
        # Resolve OCI config file path from saved profile or manual input
        oci_config_file = config.get("oci_config_file", "~/.oci/config")
        oci_profile = config.get("oci_profile", "DEFAULT")

        profile_id = config.get("oci_profile_id", "")
        if profile_id:
            saved_config, _ = get_profile_oci_paths(profile_id)
            if saved_config:
                oci_config_file = saved_config
                oci_profile = "DEFAULT"  # saved config uses DEFAULT section
                jobs[job_id]["log"] += f"Using saved OCI profile: {profile_id}\n"
                jobs[job_id]["log"] += f"Config: {saved_config}\n\n"
            else:
                jobs[job_id]["status"] = "error"
                jobs[job_id]["error"] = f"Saved profile '{profile_id}' not found. It may have been deleted."
                jobs[job_id]["log"] += f"ERROR: Saved profile '{profile_id}' not found!\n"
                jobs[job_id]["log"] += "The profile config file does not exist. It may have been deleted by another user.\n"
                jobs[job_id]["log"] += "Please select a different profile or re-register in OCI Settings.\n"
                return

        # Verify OCI config file exists
        resolved_config = os.path.expanduser(oci_config_file)
        if not os.path.isfile(resolved_config):
            jobs[job_id]["status"] = "error"
            jobs[job_id]["error"] = f"OCI config file not found: {oci_config_file}"
            jobs[job_id]["log"] += f"ERROR: OCI config file not found: {resolved_config}\n"
            jobs[job_id]["log"] += "Run 'oci setup config' or register config in OCI Settings tab.\n"
            return

        jobs[job_id]["log"] += f"OCI Config: {resolved_config}\n"
        jobs[job_id]["log"] += f"OCI Profile: {oci_profile}\n"
        jobs[job_id]["log"] += f"Compartment: {config['compartment_id'][:40]}...\n"
        jobs[job_id]["log"] += f"Namespace: {config['namespace']}\n"
        jobs[job_id]["log"] += f"Period: {config['start_time']} ~ {config['end_time']}\n\n"

        config_path = os.path.join(out_dir, "config.env")
        with open(config_path, "w") as f:
            f.write(f'OCI_CONFIG_FILE="{oci_config_file}"\n')
            f.write(f'OCI_PROFILE="{oci_profile}"\n')
            f.write(f'COMPARTMENT_ID="{config["compartment_id"]}"\n')
            f.write(f'NAMESPACE="{config["namespace"]}"\n')
            f.write(f'INTERVAL="{config.get("interval", "1m")}"\n')
            f.write(f'START_TIME="{config["start_time"]}"\n')
            f.write(f'END_TIME="{config["end_time"]}"\n')
            f.write(f'BENCH_START="{config.get("bench_start", "")}"\n')
            f.write(f'BENCH_END="{config.get("bench_end", "")}"\n')
            f.write(f'RESOURCE_NAME="{config.get("resource_name", "")}"\n')
            f.write(f'REPORT_TITLE="{config.get("report_title", "OCI DB Metric Report")}"\n')
            f.write(f'OUTPUT_DIR="{metrics_dir}"\n')

        jobs[job_id]["progress"] = 15

        # --- Step 1: Fetch metrics (streaming progress) ---
        jobs[job_id]["log"] += "=== Step 1: Collecting metrics (parallel) ===\n"
        proc = subprocess.Popen(
            ["bash", os.path.join(SCRIPT_DIR, "fetch_metrics.sh"), config_path],
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1
        )
        output_lines = []
        metric_done = 0
        for line in proc.stdout:
            output_lines.append(line)
            jobs[job_id]["log"] += line
            # Count completed metrics for progress
            if "... OK" in line or "... FAILED" in line:
                metric_done += 1
                # Progress: 15% to 48% during collection
                jobs[job_id]["progress"] = min(48, 15 + int(metric_done * 0.8))
                # Show current metric in status
                metric_name = line.strip().split("...")[0].strip()
                jobs[job_id]["collecting_metric"] = metric_name
        proc.wait()
        jobs[job_id].pop("collecting_metric", None)

        if proc.returncode != 0:
            jobs[job_id]["status"] = "error"
            output = "".join(output_lines)
            if "authentication failed" in output.lower() or "NotAuthenticated" in output:
                jobs[job_id]["error"] = "OCI authentication failed. Check API key and config."
            elif "not found" in output.lower() and "config" in output.lower():
                jobs[job_id]["error"] = "OCI config or key file not found."
            elif "NotAuthorizedOrNotFound" in output or "permission" in output.lower():
                jobs[job_id]["error"] = "Permission denied. Check compartment access rights."
            elif "No metrics found" in output or "0 metrics available" in output:
                jobs[job_id]["error"] = "No DB resources found in this compartment/namespace."
            elif "All metric collections failed" in output:
                jobs[job_id]["error"] = "All metrics failed. Check OCI CLI errors in log."
            else:
                jobs[job_id]["error"] = "Metric collection failed. See log for details."
            return

        # --- Step 2: DB Info + Charts (parallel) ---
        jobs[job_id]["status"] = "charting"
        jobs[job_id]["progress"] = 50
        jobs[job_id]["log"] += "\n=== Step 2: DB Info + Charts (parallel) ===\n"

        db_info_cmd = [
            "python3", os.path.join(SCRIPT_DIR, "fetch_db_info.py"), metrics_dir,
            "--config-file", oci_config_file,
        ]
        if oci_profile and oci_profile != "DEFAULT":
            db_info_cmd += ["--profile", oci_profile]
        charts_cmd = [
            "python3", os.path.join(SCRIPT_DIR, "generate_charts.py"), metrics_dir,
        ]

        # Launch both in parallel
        proc_dbinfo = subprocess.Popen(db_info_cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        proc_charts = subprocess.Popen(charts_cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)

        # Wait for both
        dbinfo_out, _ = proc_dbinfo.communicate(timeout=60)
        charts_out, _ = proc_charts.communicate(timeout=120)

        jobs[job_id]["log"] += f"[DB Info] {dbinfo_out}"
        if proc_dbinfo.returncode != 0:
            jobs[job_id]["log"] += "WARNING: DB info fetch failed (non-fatal)\n"

        jobs[job_id]["log"] += f"[Charts] {charts_out}"
        if proc_charts.returncode != 0:
            jobs[job_id]["status"] = "error"
            jobs[job_id]["error"] = "Chart generation failed."
            return

        jobs[job_id]["progress"] = 80

        # --- Step 3: Generate markdown report ---
        jobs[job_id]["status"] = "reporting"
        jobs[job_id]["log"] += "\n=== Step 3: Generating report ===\n"
        result = subprocess.run(
            ["bash", os.path.join(SCRIPT_DIR, "generate_report.sh"), metrics_dir],
            capture_output=True, text=True, timeout=60
        )
        jobs[job_id]["log"] += result.stdout + result.stderr

        # --- Step 5: AI Analysis (GenAI) ---
        if config.get("use_genai", False):
            jobs[job_id]["status"] = "analyzing"
            jobs[job_id]["progress"] = 90
            jobs[job_id]["log"] += "\n=== Step 4: AI Analysis (GenAI) ===\n"
            try:
                from genai_analysis import generate_ai_analysis
                ai_result = generate_ai_analysis(metrics_dir, config.get("namespace", ""))
                if ai_result and not ai_result.startswith("[GenAI Error]"):
                    analysis_path = os.path.join(metrics_dir, "analysis.md")
                    with open(analysis_path, "a") as f:
                        f.write("\n\n---\n\n")
                        f.write("### AI-Powered Analysis (OCI GenAI)\n\n")
                        f.write(ai_result)
                        f.write("\n")
                    jobs[job_id]["log"] += "AI analysis generated successfully.\n"
                elif ai_result:
                    jobs[job_id]["log"] += f"{ai_result}\n"
                else:
                    jobs[job_id]["log"] += "GenAI not configured or no data. Skipped.\n"
            except Exception as e:
                jobs[job_id]["log"] += f"AI analysis error (non-fatal): {e}\n"
        else:
            jobs[job_id]["log"] += "\n=== Step 4: AI Analysis — Skipped (disabled) ===\n"

        jobs[job_id]["progress"] = 100
        jobs[job_id]["status"] = "done"
        jobs[job_id]["metrics_dir"] = metrics_dir

        # Save to recent configs only on success
        save_recent_config(config)

        # Parse stats
        stats_path = os.path.join(metrics_dir, "stats_summary.csv")
        if os.path.exists(stats_path):
            import csv
            with open(stats_path) as sf:
                reader = csv.DictReader(sf)
                jobs[job_id]["stats"] = list(reader)

    except subprocess.TimeoutExpired:
        jobs[job_id]["status"] = "error"
        jobs[job_id]["error"] = "Timeout: operation took too long."
    except Exception as e:
        jobs[job_id]["status"] = "error"
        jobs[job_id]["error"] = str(e)
    finally:
        save_jobs()


# ============================================================
# Routes
# ============================================================

@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/compartments", methods=["POST"])
def api_compartments():
    """List child compartments of a given parent (or tenancy root)."""
    data = request.json or {}
    parent_id = data.get("parent_id", "")
    profile_id = data.get("oci_profile_id", "")
    oci_profile = data.get("oci_profile", "DEFAULT") or "DEFAULT"
    oci_config_file = data.get("oci_config_file", "~/.oci/config") or "~/.oci/config"

    # Build OCI CLI args
    oci_args = []
    oci_config_path = os.path.expanduser(oci_config_file)
    if profile_id:
        config_path, _ = get_profile_oci_paths(profile_id)
        if config_path:
            oci_args += ["--config-file", config_path]
            oci_config_path = config_path
            oci_profile = "DEFAULT"  # saved config uses DEFAULT section
    else:
        if oci_config_file != "~/.oci/config":
            oci_args += ["--config-file", oci_config_file]
        if oci_profile != "DEFAULT":
            oci_args += ["--profile", oci_profile]

    # If no parent_id, use tenancy root
    if not parent_id:
        try:
            import configparser
            cfg = configparser.ConfigParser()
            cfg.read(oci_config_path)
            parent_id = cfg.get(oci_profile, "tenancy", fallback=None)
        except Exception:
            pass
        if not parent_id:
            return jsonify({"error": "Cannot read tenancy from OCI config"}), 500

    cmd = [
        "oci", "iam", "compartment", "list",
        *oci_args,
        "--compartment-id", parent_id,
        "--lifecycle-state", "ACTIVE",
        "--all",
        "--output", "json",
    ]

    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        if result.returncode != 0:
            return jsonify({"error": result.stderr[:300]}), 500
        items = json.loads(result.stdout).get("data", [])
        compartments = [{"id": c["id"], "name": c["name"]} for c in items]
        compartments.sort(key=lambda x: x["name"])
        return jsonify({"parent_id": parent_id, "compartments": compartments})
    except subprocess.TimeoutExpired:
        return jsonify({"error": "OCI CLI timeout"}), 504
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/resources", methods=["POST"])
def api_resources():
    """List DB resource names in a compartment for the given namespace."""
    data = request.json or {}
    compartment_id = data.get("compartment_id", "")
    namespace = data.get("namespace", "")
    profile_id = data.get("oci_profile_id", "")
    oci_profile = data.get("oci_profile", "DEFAULT") or "DEFAULT"
    oci_config_file = data.get("oci_config_file", "~/.oci/config") or "~/.oci/config"
    region = data.get("region", "")

    if not compartment_id or not namespace:
        return jsonify({"error": "compartment_id and namespace required"}), 400

    # Build OCI CLI args
    oci_args = []
    if profile_id:
        config_path, _ = get_profile_oci_paths(profile_id)
        if config_path:
            oci_args += ["--config-file", config_path]
            oci_profile = "DEFAULT"
    else:
        if oci_config_file != "~/.oci/config":
            oci_args += ["--config-file", oci_config_file]
        if oci_profile != "DEFAULT":
            oci_args += ["--profile", oci_profile]
    if region:
        oci_args += ["--region", region]

    cmd = [
        "oci", "monitoring", "metric", "list",
        *oci_args,
        "--compartment-id", compartment_id,
        "--namespace", namespace,
        "--output", "json",
    ]

    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        if result.returncode != 0:
            return jsonify({"error": result.stderr[:300]}), 500

        metrics_data = json.loads(result.stdout).get("data", [])
        seen = set()
        resources = []
        for m in metrics_data:
            dims = m.get("dimensions", {})
            rname = dims.get("resourceName", "")
            rid = dims.get("resourceId", "")
            if rname and rname not in seen and "backup" not in rname.lower() and "backup" not in rid.lower():
                seen.add(rname)
                resources.append({"name": rname, "id": rid})

        resources.sort(key=lambda x: x["name"])
        return jsonify({"resources": resources})
    except subprocess.TimeoutExpired:
        return jsonify({"error": "OCI CLI timeout"}), 504
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/db-endpoint", methods=["POST"])
def api_db_endpoint():
    """Get DB system endpoint (host/port) from resource ID."""
    data = request.json or {}
    resource_id = data.get("resource_id", "")
    namespace = data.get("namespace", "")
    profile_id = data.get("oci_profile_id", "")
    oci_profile = data.get("oci_profile", "DEFAULT") or "DEFAULT"
    oci_config_file = data.get("oci_config_file", "~/.oci/config") or "~/.oci/config"

    if not resource_id or not namespace:
        return jsonify({"error": "resource_id and namespace required"}), 400

    # Build OCI CLI args
    oci_args = []
    if profile_id:
        config_path, _ = get_profile_oci_paths(profile_id)
        if config_path:
            oci_args += ["--config-file", config_path]
            oci_profile = "DEFAULT"
    else:
        if oci_config_file != "~/.oci/config":
            oci_args += ["--config-file", oci_config_file]
        if oci_profile != "DEFAULT":
            oci_args += ["--profile", oci_profile]

    try:
        if namespace == "oci_postgresql":
            cmd = ["oci", "psql", "db-system", "get", *oci_args,
                   "--db-system-id", resource_id, "--output", "json"]
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
            if result.returncode != 0:
                return jsonify({"error": result.stderr[:300]}), 500
            db_data = json.loads(result.stdout).get("data", {})
            # PostgreSQL: network-details.primary-db-endpoint-private-ip
            net = db_data.get("network-details", {})
            host = net.get("primary-db-endpoint-private-ip", "")
            port = db_data.get("instance-count", 5432)  # default port
            # Try to get port from db-configuration-params or default
            port = 5432
            return jsonify({"host": host, "port": port})

        elif namespace == "oci_mysql_database":
            cmd = ["oci", "mysql", "db-system", "get", *oci_args,
                   "--db-system-id", resource_id, "--output", "json"]
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
            if result.returncode != 0:
                return jsonify({"error": result.stderr[:300]}), 500
            db_data = json.loads(result.stdout).get("data", {})
            host = db_data.get("ip-address", "")
            port = db_data.get("port", 3306)
            endpoints = db_data.get("endpoints", [])
            if endpoints and not host:
                host = endpoints[0].get("ip-address", "")
                port = endpoints[0].get("port", 3306)
            return jsonify({"host": host, "port": port})

        else:
            return jsonify({"error": "Unsupported namespace"}), 400

    except subprocess.TimeoutExpired:
        return jsonify({"error": "OCI CLI timeout"}), 504
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/run", methods=["POST"])
def api_run():
    data = request.json
    required = ["compartment_id", "namespace", "start_time", "end_time"]
    for field in required:
        if not data.get(field):
            return jsonify({"error": f"Missing required field: {field}"}), 400

    # Normalize all time fields to full ISO 8601
    for tf in ["start_time", "end_time", "bench_start", "bench_end"]:
        if data.get(tf):
            data[tf] = normalize_iso_time(data[tf])

    # Clamp benchmark times within collection range
    if data.get("bench_start") and data.get("bench_end"):
        st = data["start_time"]
        et = data["end_time"]
        bs = data["bench_start"]
        be = data["bench_end"]
        if bs < st:
            data["bench_start"] = st
        if be > et:
            data["bench_end"] = et
        if data["bench_start"] > data["bench_end"]:
            data["bench_start"], data["bench_end"] = data["bench_end"], data["bench_start"]

    job_id = str(uuid.uuid4())[:8]
    with jobs_lock:
        jobs[job_id] = {
            "id": job_id,
            "status": "queued",
            "progress": 0,
            "config": data,
            "log": "",
            "error": None,
            "stats": None,
            "metrics_dir": None,
            "created_at": datetime.now(timezone.utc).isoformat(),
        }

    t = threading.Thread(target=run_job, args=(job_id, data), daemon=True)
    t.start()

    return jsonify({"job_id": job_id})


@app.route("/api/status/<job_id>")
def api_status(job_id):
    job = jobs.get(job_id)
    if not job:
        return jsonify({"error": "Job not found"}), 404
    return jsonify({
        "id": job["id"],
        "status": job["status"],
        "progress": job["progress"],
        "error": job["error"],
        "stats": job["stats"],
        "collecting_metric": job.get("collecting_metric", ""),
        "has_charts": job["metrics_dir"] is not None and os.path.exists(
            os.path.join(job["metrics_dir"], "chart_overview.png")
        ) if job["metrics_dir"] else False,
    })


@app.route("/api/log/<job_id>")
def api_log(job_id):
    job = jobs.get(job_id)
    if not job:
        return jsonify({"error": "Job not found"}), 404
    return jsonify({"log": job["log"]})


@app.route("/api/chart/<job_id>/<filename>")
def api_chart(job_id, filename):
    job = jobs.get(job_id)
    if not job or not job["metrics_dir"]:
        return jsonify({"error": "Not found"}), 404
    allowed = ["chart_overview.png", "chart_detail.png", "chart_zoom.png"]
    if filename not in allowed:
        return jsonify({"error": "Invalid file"}), 400
    return send_from_directory(job["metrics_dir"], filename)


@app.route("/api/report/<job_id>")
def api_report(job_id):
    job = jobs.get(job_id)
    if not job or not job["metrics_dir"]:
        return jsonify({"error": "Not found"}), 404
    report_path = os.path.join(job["metrics_dir"], "REPORT.md")
    if not os.path.exists(report_path):
        return jsonify({"error": "Report not generated"}), 404
    with open(report_path) as f:
        return jsonify({"markdown": f.read()})


@app.route("/api/analysis/<job_id>")
def api_analysis(job_id):
    job = jobs.get(job_id)
    if not job or not job["metrics_dir"]:
        return jsonify({"error": "Not found"}), 404
    analysis_path = os.path.join(job["metrics_dir"], "analysis.md")
    if not os.path.exists(analysis_path):
        return jsonify({"content": ""})
    with open(analysis_path) as f:
        return jsonify({"content": f.read()})


def _job_file_prefix(job):
    """Build descriptive filename prefix from job config."""
    cfg = job.get("config", {})
    ns_short = "PG" if "postgresql" in cfg.get("namespace", "") else "MySQL"
    rname = cfg.get("resource_name", "").replace(" ", "_") or "all"
    st = cfg.get("start_time", "")[:16].replace("-", "").replace("T", "_").replace(":", "")
    et = cfg.get("end_time", "")[:16].replace("-", "").replace("T", "_").replace(":", "")
    return f"{ns_short}_{rname}_{st}_{et}"


@app.route("/api/download/<job_id>/<filename>")
def api_download(job_id, filename):
    job = jobs.get(job_id)
    if not job or not job["metrics_dir"]:
        return jsonify({"error": "Not found"}), 404
    prefix = _job_file_prefix(job)
    name, ext = os.path.splitext(filename)
    dl_name = f"{name}_{prefix}{ext}"
    return send_from_directory(job["metrics_dir"], filename, as_attachment=True,
                               download_name=dl_name)


@app.route("/api/jobs/<job_id>", methods=["DELETE"])
def api_delete_job(job_id):
    job = jobs.get(job_id)
    if not job:
        return jsonify({"error": "Job not found"}), 404
    # Remove output directory
    if job.get("metrics_dir") and os.path.isdir(job["metrics_dir"]):
        import shutil
        # Remove the parent job dir (job_{id}/)
        job_dir = os.path.dirname(job["metrics_dir"])
        if os.path.isdir(job_dir) and "job_" in os.path.basename(job_dir):
            shutil.rmtree(job_dir, ignore_errors=True)
    with jobs_lock:
        del jobs[job_id]
    save_jobs()
    return jsonify({"ok": True})


@app.route("/api/pdf/<job_id>")
def api_pdf(job_id):
    """Generate PDF from REPORT.md + charts and return it."""
    job = jobs.get(job_id)
    if not job or not job.get("metrics_dir"):
        return jsonify({"error": "Not found"}), 404

    metrics_dir = job["metrics_dir"]
    report_path = os.path.join(metrics_dir, "REPORT.md")
    pdf_path = os.path.join(metrics_dir, "REPORT.pdf")

    # Generate PDF using Python
    try:
        result = subprocess.run(
            ["python3", os.path.join(SCRIPT_DIR, "generate_pdf.py"), metrics_dir],
            capture_output=True, text=True, timeout=60
        )
        if result.returncode != 0:
            return jsonify({"error": f"PDF generation failed: {result.stderr[:300]}"}), 500
    except Exception as e:
        return jsonify({"error": str(e)}), 500

    if not os.path.isfile(pdf_path):
        return jsonify({"error": "PDF file not created"}), 500

    pdf_name = f"REPORT_{_job_file_prefix(job)}.pdf"
    return send_from_directory(metrics_dir, "REPORT.pdf", as_attachment=True,
                               download_name=pdf_name)


@app.route("/api/rawdata/<job_id>")
def api_rawdata(job_id):
    """Download all CSV raw data as a ZIP file."""
    import zipfile
    import io

    job = jobs.get(job_id)
    if not job or not job.get("metrics_dir"):
        return jsonify({"error": "Not found"}), 404

    metrics_dir = job["metrics_dir"]
    csv_files = sorted(
        f for f in os.listdir(metrics_dir)
        if f.endswith(".csv") and not f.startswith("_")
    )
    if not csv_files:
        return jsonify({"error": "No CSV data found"}), 404

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for fname in csv_files:
            zf.write(os.path.join(metrics_dir, fname), fname)
        # Include metadata
        meta_path = os.path.join(metrics_dir, "_metadata.json")
        if os.path.isfile(meta_path):
            zf.write(meta_path, "_metadata.json")
    buf.seek(0)

    zip_name = f"RAWDATA_{_job_file_prefix(job)}.zip"

    return Response(
        buf.getvalue(),
        mimetype="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{zip_name}"'},
    )


@app.route("/api/jobs")
def api_jobs():
    return jsonify([
        {
            "id": j["id"],
            "status": j["status"],
            "progress": j["progress"],
            "namespace": j["config"].get("namespace", ""),
            "created_at": j["created_at"],
            "title": j["config"].get("report_title", ""),
            "start_time": j["config"].get("start_time", ""),
            "end_time": j["config"].get("end_time", ""),
        }
        for j in sorted(jobs.values(), key=lambda x: x["created_at"], reverse=True)
    ])


@app.route("/report/<job_id>")
def view_report(job_id):
    job = jobs.get(job_id)
    if not job:
        return redirect(url_for("index"))
    return render_template("report.html", job_id=job_id)


# ============================================================
# OCI Config File Parser - read profile sections
# ============================================================

def parse_oci_config(config_path):
    """Parse OCI config file and return list of profile sections with metadata."""
    config_path = os.path.expanduser(config_path)
    if not os.path.isfile(config_path):
        return []

    profiles = []
    current = None

    with open(config_path) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            # Section header: [DEFAULT], [WESANG_POC], etc.
            if line.startswith("[") and line.endswith("]"):
                if current:
                    profiles.append(current)
                section_name = line[1:-1]
                current = {"name": section_name, "region": "", "tenancy_short": "", "user_short": ""}
            elif current and "=" in line:
                key, _, val = line.partition("=")
                key = key.strip()
                val = val.strip()
                if key == "region":
                    current["region"] = val
                elif key == "tenancy":
                    current["tenancy_short"] = val[:30] + "..." if len(val) > 30 else val
                elif key == "user":
                    current["user_short"] = val[:30] + "..." if len(val) > 30 else val

    if current:
        profiles.append(current)

    return profiles


@app.route("/api/oci-config-profiles")
def api_oci_config_profiles():
    """Return profile sections from an OCI config file."""
    config_path = request.args.get("config_file", "~/.oci/config")

    # Also check saved profiles
    profile_id = request.args.get("profile_id", "")
    if profile_id:
        saved_config, _ = get_profile_oci_paths(profile_id)
        if saved_config:
            config_path = saved_config

    profiles = parse_oci_config(config_path)
    return jsonify(profiles)


# ============================================================
# OCI Profile Management API
# ============================================================

@app.route("/api/profiles", methods=["GET"])
def api_profiles():
    return jsonify(list_profiles())


@app.route("/api/profiles", methods=["POST"])
def api_create_profile():
    data = request.json
    name = data.get("name", "").strip()
    if not name:
        return jsonify({"error": "Profile name is required"}), 400

    # Generate safe directory name
    profile_id = name.lower().replace(" ", "_")[:30]
    profile_id = "".join(c for c in profile_id if c.isalnum() or c == "_")
    if not profile_id:
        profile_id = str(uuid.uuid4())[:8]

    pdir = os.path.join(get_profiles_dir(), profile_id)
    if os.path.exists(pdir):
        # Append random suffix if exists
        profile_id = f"{profile_id}_{str(uuid.uuid4())[:4]}"
        pdir = os.path.join(get_profiles_dir(), profile_id)

    os.makedirs(pdir, exist_ok=True)

    # Save OCI config file
    config_content = data.get("config_content", "").strip()
    key_content = data.get("key_content", "").strip()

    if not config_content:
        return jsonify({"error": "OCI config content is required"}), 400

    # Rewrite key_file path in config to point to our stored key
    key_path = os.path.join(pdir, "oci_api_key.pem")
    config_lines = []
    for line in config_content.splitlines():
        if line.strip().startswith("key_file"):
            config_lines.append(f"key_file={key_path}")
        else:
            config_lines.append(line)
    config_content_fixed = "\n".join(config_lines) + "\n"

    config_path = os.path.join(pdir, "config")
    with open(config_path, "w") as f:
        f.write(config_content_fixed)
    os.chmod(config_path, 0o600)

    # Save key file
    if key_content:
        with open(key_path, "w") as f:
            f.write(key_content)
        os.chmod(key_path, 0o600)

    # Save metadata
    meta = {
        "name": name,
        "description": data.get("description", ""),
        "region": data.get("region", ""),
        "tenancy": data.get("tenancy", ""),
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    with open(os.path.join(pdir, "meta.json"), "w") as f:
        json.dump(meta, f, indent=2)

    return jsonify({"id": profile_id, "name": name})


@app.route("/api/profiles/<profile_id>", methods=["GET"])
def api_get_profile(profile_id):
    pdir = os.path.join(get_profiles_dir(), profile_id)
    meta_path = os.path.join(pdir, "meta.json")
    if not os.path.isfile(meta_path):
        return jsonify({"error": "Profile not found"}), 404

    with open(meta_path) as f:
        meta = json.load(f)
    meta["id"] = profile_id
    meta["has_config"] = os.path.isfile(os.path.join(pdir, "config"))
    meta["has_key"] = os.path.isfile(os.path.join(pdir, "oci_api_key.pem"))

    # Read config (mask sensitive parts)
    config_path = os.path.join(pdir, "config")
    config_preview = ""
    if os.path.isfile(config_path):
        with open(config_path) as f:
            for line in f:
                if "key_file" in line:
                    config_preview += "key_file=<managed by app>\n"
                elif "fingerprint" in line:
                    parts = line.strip().split("=", 1)
                    if len(parts) == 2 and len(parts[1]) > 10:
                        config_preview += f"{parts[0]}={parts[1][:8]}...{parts[1][-4:]}\n"
                    else:
                        config_preview += line
                else:
                    config_preview += line
    meta["config_preview"] = config_preview
    return jsonify(meta)


@app.route("/api/profiles/<profile_id>", methods=["PUT"])
def api_update_profile(profile_id):
    pdir = os.path.join(get_profiles_dir(), profile_id)
    if not os.path.isdir(pdir):
        return jsonify({"error": "Profile not found"}), 404

    data = request.json

    # Update config if provided
    config_content = data.get("config_content", "").strip()
    if config_content:
        key_path = os.path.join(pdir, "oci_api_key.pem")
        config_lines = []
        for line in config_content.splitlines():
            if line.strip().startswith("key_file"):
                config_lines.append(f"key_file={key_path}")
            else:
                config_lines.append(line)
        with open(os.path.join(pdir, "config"), "w") as f:
            f.write("\n".join(config_lines) + "\n")

    # Update key if provided
    key_content = data.get("key_content", "").strip()
    if key_content:
        key_path = os.path.join(pdir, "oci_api_key.pem")
        with open(key_path, "w") as f:
            f.write(key_content)
        os.chmod(key_path, 0o600)

    # Update meta
    meta_path = os.path.join(pdir, "meta.json")
    with open(meta_path) as f:
        meta = json.load(f)
    for field in ["name", "description", "region", "tenancy"]:
        if data.get(field):
            meta[field] = data[field]
    meta["updated_at"] = datetime.now(timezone.utc).isoformat()
    with open(meta_path, "w") as f:
        json.dump(meta, f, indent=2)

    return jsonify({"ok": True})


@app.route("/api/profiles/<profile_id>", methods=["DELETE"])
def api_delete_profile(profile_id):
    pdir = os.path.join(get_profiles_dir(), profile_id)
    if not os.path.isdir(pdir):
        return jsonify({"error": "Profile not found"}), 404
    import shutil
    shutil.rmtree(pdir)
    return jsonify({"ok": True})


@app.route("/api/profiles/<profile_id>/test", methods=["POST"])
def api_test_profile(profile_id):
    """Test OCI CLI connectivity with this profile."""
    config_path, _ = get_profile_oci_paths(profile_id)
    if not config_path:
        return jsonify({"error": "Profile config not found"}), 404

    try:
        result = subprocess.run(
            ["oci", "--config-file", config_path, "iam", "region", "list", "--output", "json"],
            capture_output=True, text=True, timeout=30
        )
        if result.returncode == 0:
            regions = json.loads(result.stdout)
            count = len(regions.get("data", []))
            return jsonify({"ok": True, "message": f"Connected! ({count} regions available)"})
        else:
            return jsonify({"ok": False, "message": result.stderr.strip()[:300]})
    except subprocess.TimeoutExpired:
        return jsonify({"ok": False, "message": "Timeout connecting to OCI"})
    except Exception as e:
        return jsonify({"ok": False, "message": str(e)})


# ============================================================
# Recent configs (last 3 used configurations)
# ============================================================
RECENT_CONFIGS_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "output", "recent_configs.json")


def load_recent_configs():
    if os.path.isfile(RECENT_CONFIGS_FILE):
        try:
            with open(RECENT_CONFIGS_FILE) as f:
                return json.load(f)
        except Exception:
            pass
    return []


def save_recent_config(config):
    """Save config to recent list (max 3, newest first, no duplicates)."""
    recents = load_recent_configs()
    # Build a key for dedup (namespace + compartment + resource_name)
    key = f"{config.get('namespace', '')}|{config.get('compartment_id', '')}|{config.get('resource_name', '')}"
    # Remove existing entry with same key
    recents = [r for r in recents if f"{r['config'].get('namespace', '')}|{r['config'].get('compartment_id', '')}|{r['config'].get('resource_name', '')}" != key]
    # Build label
    ns_short = "PG" if "postgresql" in config.get("namespace", "") else "MySQL"
    rname = config.get("resource_name", "") or "(전체)"
    title = config.get("report_title", "")
    entry = {
        "label": f"{ns_short} - {rname}",
        "description": title,
        "saved_at": datetime.now(timezone.utc).isoformat(),
        "config": {k: v for k, v in config.items() if k not in ("log",)},
    }
    recents.insert(0, entry)
    recents = recents[:3]
    try:
        os.makedirs(os.path.dirname(RECENT_CONFIGS_FILE), exist_ok=True)
        with open(RECENT_CONFIGS_FILE, "w") as f:
            json.dump(recents, f, indent=2)
    except Exception:
        pass


@app.route("/api/recent-configs")
def api_recent_configs():
    recents = load_recent_configs()
    return jsonify([
        {"index": i, "label": r["label"], "description": r["description"], "saved_at": r.get("saved_at", "")}
        for i, r in enumerate(recents)
    ])


@app.route("/api/recent-configs/<int:index>")
def api_recent_config(index):
    recents = load_recent_configs()
    if index < 0 or index >= len(recents):
        return jsonify({"error": "Not found"}), 404
    return jsonify(recents[index]["config"])


# ============================================================
# GenAI Config API
# ============================================================

@app.route("/api/genai-config", methods=["GET"])
def api_get_genai_config():
    from genai_analysis import load_genai_config, CONFIG_PATH
    cfg = load_genai_config()
    if not cfg:
        # Return defaults if no config
        cfg = {"enabled": False, "api_key": "", "base_url": "", "model": ""}
    # Mask API key for display
    key = cfg.get("api_key", "")
    if key and len(key) > 10:
        cfg["api_key_masked"] = key[:6] + "..." + key[-4:]
    else:
        cfg["api_key_masked"] = ""
    cfg.pop("api_key", None)
    return jsonify(cfg)


@app.route("/api/genai-config", methods=["POST"])
def api_save_genai_config():
    from genai_analysis import load_genai_config, save_genai_config, CONFIG_PATH
    data = request.json
    # Load existing to preserve api_key if not provided
    existing = load_genai_config() or {}
    cfg = {
        "enabled": data.get("enabled", existing.get("enabled", True)),
        "api_key": data.get("api_key") or existing.get("api_key", ""),
        "base_url": data.get("base_url") or existing.get("base_url", ""),
        "model": data.get("model") or existing.get("model", ""),
    }
    save_genai_config(cfg)
    return jsonify({"ok": True})


@app.route("/api/genai-test", methods=["POST"])
def api_test_genai():
    from genai_analysis import load_genai_config
    cfg = load_genai_config()
    if not cfg:
        return jsonify({"ok": False, "message": "GenAI not configured"})
    try:
        from openai import OpenAI
        client = OpenAI(api_key=cfg["api_key"], base_url=cfg["base_url"])
        resp = client.chat.completions.create(
            model=cfg["model"],
            messages=[{"role": "user", "content": "Say OK"}],
            max_tokens=10,
        )
        text = resp.choices[0].message.content
        return jsonify({"ok": True, "message": f"Connected! Response: {text}"})
    except Exception as e:
        return jsonify({"ok": False, "message": str(e)[:300]})


# ============================================================
# Real-time Monitor
# ============================================================
monitors = {}
monitors_lock = threading.Lock()


def _build_oci_monitor_args(config):
    """Build OCI CLI args from monitor config."""
    oci_args = []
    profile_id = config.get("oci_profile_id", "")
    oci_profile = config.get("oci_profile", "DEFAULT") or "DEFAULT"
    oci_config_file = config.get("oci_config_file", "~/.oci/config") or "~/.oci/config"

    if profile_id:
        config_path, _ = get_profile_oci_paths(profile_id)
        if config_path:
            oci_args += ["--config-file", config_path]
            oci_profile = "DEFAULT"
    else:
        if oci_config_file != "~/.oci/config":
            oci_args += ["--config-file", oci_config_file]
        if oci_profile != "DEFAULT":
            oci_args += ["--profile", oci_profile]

    region = config.get("region", "")
    if region:
        oci_args += ["--region", region]

    return oci_args, oci_profile


def _fetch_oci_metrics(config, oci_args, oci_profile):
    """Fetch CPU/Memory from OCI Monitoring API."""
    namespace = config["namespace"]
    compartment_id = config["compartment_id"]
    resource_name = config.get("resource_name", "")
    now = datetime.now(timezone.utc)
    start = (now - timedelta(minutes=3)).strftime("%Y-%m-%dT%H:%M:%SZ")
    end = now.strftime("%Y-%m-%dT%H:%M:%SZ")

    if namespace == "oci_postgresql":
        metrics = [
            ("cpu", 'CpuUtilization[1m]{dbInstanceRole = "PRIMARY"}.mean()'),
            ("memory", 'MemoryUtilization[1m]{dbInstanceRole = "PRIMARY"}.mean()'),
        ]
    else:
        filt = '{resourceName = "' + resource_name + '"}' if resource_name else ""
        metrics = [
            ("cpu", f"CPUUtilization[1m]{filt}.mean()"),
            ("memory", f"MemoryUtilization[1m]{filt}.mean()"),
        ]

    results = {}

    def fetch_one(key, query):
        cmd = [
            "oci", "monitoring", "metric-data", "summarize-metrics-data",
            *oci_args,
            "--compartment-id", compartment_id,
            "--namespace", namespace,
            "--query-text", query,
            "--start-time", start, "--end-time", end,
            "--output", "json",
        ]
        try:
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
            if r.returncode == 0:
                data = json.loads(r.stdout).get("data", [])
                if data and data[0].get("aggregated-datapoints"):
                    pts = data[0]["aggregated-datapoints"]
                    latest = pts[-1]
                    results[key] = round(latest.get("value", 0), 2)
                    results[key + "_ts"] = latest.get("timestamp", "")
        except Exception:
            pass

    with ThreadPoolExecutor(max_workers=2) as ex:
        for k, q in metrics:
            ex.submit(fetch_one, k, q)

    return results


def _detect_advanced_mode(conn, namespace):
    """Detect if advanced metrics are available."""
    try:
        if namespace == "oci_postgresql":
            cur = conn.cursor()
            cur.execute("SELECT 1 FROM pg_extension WHERE extname = 'pg_stat_statements'")
            found = cur.fetchone() is not None
            cur.close()
            return found
        else:
            cur = conn.cursor()
            cur.execute("SHOW VARIABLES LIKE 'performance_schema'")
            row = cur.fetchone()
            cur.close()
            if row:
                val = row[1] if isinstance(row, (list, tuple)) else row.get("Value", "OFF")
                return str(val).upper() == "ON"
    except Exception:
        pass
    return False


def _fetch_db_metrics_pg(conn, prev):
    """Fetch basic DB metrics from PostgreSQL."""
    cur = conn.cursor()
    result = {}

    cur.execute("SELECT count(*) FROM pg_stat_activity WHERE state = 'active'")
    result["active_sessions"] = cur.fetchone()[0]

    cur.execute("SELECT count(*) FROM pg_stat_activity")
    result["total_connections"] = cur.fetchone()[0]

    cur.execute(
        "SELECT sum(xact_commit) + sum(xact_rollback), sum(blks_hit), sum(blks_read) "
        "FROM pg_stat_database"
    )
    row = cur.fetchone()
    total_xact = int(row[0] or 0)
    blks_hit = int(row[1] or 0)
    blks_read = int(row[2] or 0)

    now_ts = time.time()
    if prev.get("xact_total") is not None and prev.get("ts"):
        dt = now_ts - prev["ts"]
        if dt > 0:
            result["tps"] = round((total_xact - prev["xact_total"]) / dt, 1)
        else:
            result["tps"] = 0
    else:
        result["tps"] = 0

    total_blks = blks_hit + blks_read
    result["cache_hit_ratio"] = round((blks_hit / total_blks * 100) if total_blks > 0 else 100, 1)

    # Wait events
    cur.execute(
        "SELECT wait_event_type, count(*) FROM pg_stat_activity "
        "WHERE state = 'active' AND wait_event_type IS NOT NULL "
        "GROUP BY wait_event_type ORDER BY count(*) DESC"
    )
    result["wait_events"] = {r[0]: r[1] for r in cur.fetchall()}

    cur.close()

    prev["xact_total"] = total_xact
    prev["ts"] = now_ts

    return result


def _fetch_db_metrics_mysql(conn, prev):
    """Fetch basic DB metrics from MySQL."""
    cur = conn.cursor()
    result = {}

    def get_status(name):
        cur.execute(f"SHOW GLOBAL STATUS LIKE '{name}'")
        row = cur.fetchone()
        if row:
            val = row[1] if isinstance(row, (list, tuple)) else row.get("Value", "0")
            return int(val)
        return 0

    result["active_sessions"] = get_status("Threads_running")
    result["total_connections"] = get_status("Threads_connected")

    questions = get_status("Questions")
    now_ts = time.time()
    if prev.get("questions") is not None and prev.get("ts"):
        dt = now_ts - prev["ts"]
        if dt > 0:
            result["tps"] = round((questions - prev["questions"]) / dt, 1)
        else:
            result["tps"] = 0
    else:
        result["tps"] = 0

    read_requests = get_status("Innodb_buffer_pool_read_requests")
    reads = get_status("Innodb_buffer_pool_reads")
    total = read_requests + reads
    result["cache_hit_ratio"] = round((read_requests / total * 100) if total > 0 else 100, 1)

    # InnoDB row ops for wait event approximation
    result["wait_events"] = {}
    rows_read = get_status("Innodb_rows_read")
    rows_inserted = get_status("Innodb_rows_inserted")
    rows_updated = get_status("Innodb_rows_updated")
    rows_deleted = get_status("Innodb_rows_deleted")
    if prev.get("rows_read") is not None and prev.get("ts"):
        dt = now_ts - prev["ts"]
        if dt > 0:
            result["wait_events"]["Read"] = round((rows_read - prev["rows_read"]) / dt)
            result["wait_events"]["Insert"] = round((rows_inserted - prev["rows_inserted"]) / dt)
            result["wait_events"]["Update"] = round((rows_updated - prev["rows_updated"]) / dt)
            result["wait_events"]["Delete"] = round((rows_deleted - prev["rows_deleted"]) / dt)

    cur.close()

    prev["questions"] = questions
    prev["rows_read"] = rows_read
    prev["rows_inserted"] = rows_inserted
    prev["rows_updated"] = rows_updated
    prev["rows_deleted"] = rows_deleted
    prev["ts"] = now_ts

    return result


def _fetch_advanced_pg(conn):
    """Fetch advanced metrics from PostgreSQL pg_stat_statements."""
    cur = conn.cursor()
    results = {}

    try:
        cur.execute(
            "SELECT query, calls, total_exec_time, mean_exec_time "
            "FROM pg_stat_statements ORDER BY calls DESC LIMIT 5"
        )
        results["top_sql_by_calls"] = [
            {"query": r[0][:120], "calls": r[1], "avg_time_ms": round(r[3], 2)}
            for r in cur.fetchall()
        ]

        cur.execute(
            "SELECT query, calls, total_exec_time, mean_exec_time "
            "FROM pg_stat_statements ORDER BY total_exec_time DESC LIMIT 5"
        )
        results["top_sql_by_time"] = [
            {"query": r[0][:120], "calls": r[1], "total_time_ms": round(r[2], 2), "avg_time_ms": round(r[3], 2)}
            for r in cur.fetchall()
        ]
    except Exception:
        results["top_sql_by_calls"] = []
        results["top_sql_by_time"] = []

    cur.close()
    return results


def _fetch_advanced_mysql(conn):
    """Fetch advanced metrics from MySQL performance_schema."""
    cur = conn.cursor()
    results = {}

    try:
        cur.execute(
            "SELECT DIGEST_TEXT, COUNT_STAR, SUM_TIMER_WAIT/1000000000 as total_ms, "
            "AVG_TIMER_WAIT/1000000000 as avg_ms "
            "FROM performance_schema.events_statements_summary_by_digest "
            "WHERE DIGEST_TEXT IS NOT NULL ORDER BY COUNT_STAR DESC LIMIT 5"
        )
        results["top_sql_by_calls"] = [
            {"query": (r[0] or "")[:120], "calls": r[1], "avg_time_ms": round(r[3], 2)}
            for r in cur.fetchall()
        ]

        cur.execute(
            "SELECT DIGEST_TEXT, COUNT_STAR, SUM_TIMER_WAIT/1000000000 as total_ms, "
            "AVG_TIMER_WAIT/1000000000 as avg_ms "
            "FROM performance_schema.events_statements_summary_by_digest "
            "WHERE DIGEST_TEXT IS NOT NULL ORDER BY SUM_TIMER_WAIT DESC LIMIT 5"
        )
        results["top_sql_by_time"] = [
            {"query": (r[0] or "")[:120], "calls": r[1], "total_time_ms": round(r[2], 2), "avg_time_ms": round(r[3], 2)}
            for r in cur.fetchall()
        ]

        cur.execute(
            "SELECT EVENT_NAME, CURRENT_NUMBER_OF_BYTES_USED "
            "FROM performance_schema.memory_summary_global_by_event_name "
            "WHERE CURRENT_NUMBER_OF_BYTES_USED > 0 "
            "ORDER BY CURRENT_NUMBER_OF_BYTES_USED DESC LIMIT 10"
        )
        results["memory_by_component"] = [
            {"name": r[0].split("/")[-1], "bytes": r[1]}
            for r in cur.fetchall()
        ]
    except Exception:
        results["top_sql_by_calls"] = []
        results["top_sql_by_time"] = []
        results["memory_by_component"] = []

    cur.close()
    return results


def _connect_db(config):
    """Connect to database based on namespace."""
    namespace = config["namespace"]
    host = config["db_host"]
    port = int(config.get("db_port", 5432 if namespace == "oci_postgresql" else 3306))
    user = config["db_user"]
    password = config["db_password"]
    database = config.get("db_database", "postgres" if namespace == "oci_postgresql" else "mysql")

    if namespace == "oci_postgresql":
        import psycopg2
        return psycopg2.connect(host=host, port=port, user=user, password=password, dbname=database, connect_timeout=10)
    else:
        import mysql.connector
        return mysql.connector.connect(host=host, port=port, user=user, password=password, database=database, connect_timeout=10)


@app.route("/monitor")
def monitor_page():
    return render_template("monitor.html")


# ============================================================
# Recent monitor configs
# ============================================================
RECENT_MONITOR_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "output", "recent_monitor_configs.json")


def load_recent_monitor_configs():
    if os.path.isfile(RECENT_MONITOR_FILE):
        try:
            with open(RECENT_MONITOR_FILE) as f:
                return json.load(f)
        except Exception:
            pass
    return []


def save_recent_monitor_config(config):
    """Save monitor config to recent list (max 5, newest first, no duplicates)."""
    recents = load_recent_monitor_configs()
    key = f"{config.get('namespace', '')}|{config.get('compartment_id', '')}|{config.get('resource_name', '')}|{config.get('db_host', '')}"
    recents = [r for r in recents if f"{r['config'].get('namespace', '')}|{r['config'].get('compartment_id', '')}|{r['config'].get('resource_name', '')}|{r['config'].get('db_host', '')}" != key]
    ns_short = "PG" if "postgresql" in config.get("namespace", "") else "MySQL"
    rname = config.get("resource_name", "") or "(전체)"
    db_host = config.get("db_host", "")
    entry = {
        "label": f"{ns_short} - {rname}",
        "description": f"{db_host}:{config.get('db_port', '')}",
        "saved_at": datetime.now(timezone.utc).isoformat(),
        "config": {k: v for k, v in config.items() if k not in ("db_password",)},
    }
    recents.insert(0, entry)
    recents = recents[:5]
    try:
        os.makedirs(os.path.dirname(RECENT_MONITOR_FILE), exist_ok=True)
        with open(RECENT_MONITOR_FILE, "w") as f:
            json.dump(recents, f, indent=2)
    except Exception:
        pass


@app.route("/api/recent-monitor-configs")
def api_recent_monitor_configs():
    recents = load_recent_monitor_configs()
    return jsonify([
        {"index": i, "label": r["label"], "description": r.get("description", ""), "saved_at": r.get("saved_at", "")}
        for i, r in enumerate(recents)
    ])


@app.route("/api/recent-monitor-configs/<int:index>")
def api_recent_monitor_config(index):
    recents = load_recent_monitor_configs()
    if index < 0 or index >= len(recents):
        return jsonify({"error": "Not found"}), 404
    return jsonify(recents[index]["config"])


@app.route("/api/monitor/start", methods=["POST"])
def api_monitor_start():
    """Start a monitoring session."""
    data = request.json or {}
    required = ["compartment_id", "namespace", "db_host", "db_user", "db_password"]
    for f in required:
        if not data.get(f):
            return jsonify({"error": f"Missing required field: {f}"}), 400

    session_id = str(uuid.uuid4())[:8]

    # Test DB connection and detect mode
    try:
        conn = _connect_db(data)
        advanced = _detect_advanced_mode(conn, data["namespace"])
        conn.close()
    except Exception as e:
        return jsonify({"error": f"DB connection failed: {e}"}), 400

    interval = int(data.get("interval", 15))
    duration = int(data.get("duration", 300))

    # Save to recent monitor configs on success
    save_recent_monitor_config(data)

    with monitors_lock:
        monitors[session_id] = {
            "config": data,
            "interval": interval,
            "duration": duration,
            "advanced": advanced,
            "running": True,
            "queue": Queue(),
        }

    return jsonify({
        "session_id": session_id,
        "advanced": advanced,
        "namespace": data["namespace"],
        "interval": interval,
        "duration": duration,
    })


@app.route("/api/monitor/stream/<session_id>")
def api_monitor_stream(session_id):
    """SSE stream for real-time monitoring data."""
    session = monitors.get(session_id)
    if not session:
        return jsonify({"error": "Session not found"}), 404

    def generate():
        config = session["config"]
        namespace = config["namespace"]
        interval = session["interval"]
        advanced = session["advanced"]

        oci_args, oci_profile = _build_oci_monitor_args(config)

        # Init message
        if advanced:
            if namespace == "oci_postgresql":
                msg = "pg_stat_statements detected — Advanced mode enabled"
            else:
                msg = "performance_schema ON — Advanced mode enabled"
        else:
            if namespace == "oci_postgresql":
                msg = "Basic mode (enable pg_stat_statements for Top SQL)"
            else:
                msg = "Basic mode (enable performance_schema for Top SQL)"

        def _json(obj):
            return json.dumps(obj, default=lambda o: float(o) if isinstance(o, Decimal) else str(o))

        init_data = _json({
            "type": "init",
            "mode": "advanced" if advanced else "basic",
            "namespace": namespace,
            "interval": interval,
            "duration": session["duration"],
            "message": msg,
        })
        yield f"data: {init_data}\n\n"

        # Connect to DB
        try:
            conn = _connect_db(config)
        except Exception as e:
            err = _json({"type": "error", "message": f"DB connection failed: {e}"})
            yield f"data: {err}\n\n"
            return

        prev = {}
        loop_count = 0

        try:
            while session.get("running", False):
                loop_count += 1

                # Fetch OCI + DB metrics in parallel
                oci_result = {}
                db_result = {}

                def fetch_oci():
                    nonlocal oci_result
                    oci_result = _fetch_oci_metrics(config, oci_args, oci_profile)

                def fetch_db():
                    nonlocal db_result
                    if namespace == "oci_postgresql":
                        db_result = _fetch_db_metrics_pg(conn, prev)
                    else:
                        db_result = _fetch_db_metrics_mysql(conn, prev)

                with ThreadPoolExecutor(max_workers=2) as ex:
                    ex.submit(fetch_oci)
                    ex.submit(fetch_db)

                now_kst = (datetime.now(timezone.utc) + timedelta(hours=9)).strftime("%Y-%m-%dT%H:%M:%S+09:00")

                basic_data = _json({
                    "type": "basic",
                    "timestamp": now_kst,
                    "oci": {
                        "cpu": oci_result.get("cpu", None),
                        "memory": oci_result.get("memory", None),
                        "updated": bool(oci_result.get("cpu") is not None),
                    },
                    "db": db_result,
                })
                yield f"data: {basic_data}\n\n"

                # Advanced data every 2nd loop
                if advanced and loop_count % 2 == 0:
                    try:
                        if namespace == "oci_postgresql":
                            adv = _fetch_advanced_pg(conn)
                        else:
                            adv = _fetch_advanced_mysql(conn)
                        adv["type"] = "advanced"
                        yield f"data: {_json(adv)}\n\n"
                    except Exception:
                        pass

                time.sleep(interval)

        except GeneratorExit:
            pass
        finally:
            try:
                conn.close()
            except Exception:
                pass

    return Response(generate(), mimetype="text/event-stream",
                    headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@app.route("/api/monitor/stop/<session_id>", methods=["POST"])
def api_monitor_stop(session_id):
    """Stop a monitoring session."""
    session = monitors.get(session_id)
    if not session:
        return jsonify({"error": "Session not found"}), 404
    session["running"] = False
    with monitors_lock:
        monitors.pop(session_id, None)
    return jsonify({"ok": True})


@app.after_request
def add_no_cache(response):
    """Prevent browser caching of HTML/JS to ensure latest code runs."""
    if response.content_type and ("text/html" in response.content_type or "javascript" in response.content_type):
        response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
        response.headers["Pragma"] = "no-cache"
    return response


if __name__ == "__main__":
    os.makedirs(app.config["OUTPUT_BASE"], exist_ok=True)
    load_jobs()
    app.run(host="0.0.0.0", port=5050, debug=False, threaded=True)

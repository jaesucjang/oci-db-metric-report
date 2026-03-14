#!/usr/bin/env python3
"""
OCI DB Metric Report - Web Service
Flask app for collecting OCI DB metrics and generating visual reports.
"""

import json
import os
import subprocess
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path

from flask import Flask, render_template, request, jsonify, send_from_directory, redirect, url_for

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


def load_jobs():
    """Load jobs from disk on startup."""
    global jobs
    if os.path.isfile(JOBS_FILE):
        try:
            with open(JOBS_FILE) as f:
                jobs = json.load(f)
        except Exception:
            jobs = {}


def save_jobs():
    """Persist jobs to disk (excludes log to keep file small)."""
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

        # --- Step 2: Fetch metrics ---
        jobs[job_id]["log"] += "=== Step 1: Collecting metrics ===\n"
        result = subprocess.run(
            ["bash", os.path.join(SCRIPT_DIR, "fetch_metrics.sh"), config_path],
            capture_output=True, text=True, timeout=300
        )
        jobs[job_id]["log"] += result.stdout + result.stderr
        if result.returncode != 0:
            jobs[job_id]["status"] = "error"
            # Parse specific error from output
            output = result.stdout + result.stderr
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

        jobs[job_id]["status"] = "charting"
        jobs[job_id]["progress"] = 50

        # --- Step 3: Generate charts ---
        jobs[job_id]["log"] += "\n=== Step 2: Generating charts ===\n"
        result = subprocess.run(
            ["python3", os.path.join(SCRIPT_DIR, "generate_charts.py"), metrics_dir],
            capture_output=True, text=True, timeout=120
        )
        jobs[job_id]["log"] += result.stdout + result.stderr
        if result.returncode != 0:
            jobs[job_id]["status"] = "error"
            jobs[job_id]["error"] = "Chart generation failed."
            return

        jobs[job_id]["progress"] = 80

        # --- Step 4: Generate markdown report ---
        jobs[job_id]["status"] = "reporting"
        jobs[job_id]["log"] += "\n=== Step 3: Generating report ===\n"
        result = subprocess.run(
            ["bash", os.path.join(SCRIPT_DIR, "generate_report.sh"), metrics_dir],
            capture_output=True, text=True, timeout=60
        )
        jobs[job_id]["log"] += result.stdout + result.stderr

        jobs[job_id]["progress"] = 100
        jobs[job_id]["status"] = "done"
        jobs[job_id]["metrics_dir"] = metrics_dir

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


@app.route("/api/resources", methods=["POST"])
def api_resources():
    """List DB resource names in a compartment/namespace via OCI Monitoring API."""
    data = request.json or {}
    compartment_id = data.get("compartment_id", "")
    namespace = data.get("namespace", "")
    profile_id = data.get("oci_profile_id", "")

    if not compartment_id or not namespace:
        return jsonify({"error": "compartment_id and namespace required"}), 400

    # Build OCI CLI args
    oci_args = []
    if profile_id:
        config_path, _ = get_profile_oci_paths(profile_id)
        if config_path:
            oci_args += ["--config-file", config_path]

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

        import json as _json
        metrics_data = _json.loads(result.stdout).get("data", [])

        # Extract unique resourceName values, excluding backups
        resources = set()
        for m in metrics_data:
            dims = m.get("dimensions", {})
            rname = dims.get("resourceName", "")
            rid = dims.get("resourceId", "")
            if rname and "backup" not in rname.lower() and "backup" not in rid.lower():
                resources.add(rname)

        return jsonify({"resources": sorted(resources)})
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


@app.route("/api/download/<job_id>/<filename>")
def api_download(job_id, filename):
    job = jobs.get(job_id)
    if not job or not job["metrics_dir"]:
        return jsonify({"error": "Not found"}), 404
    return send_from_directory(job["metrics_dir"], filename, as_attachment=True)


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

    # Build descriptive filename: REPORT_MySQL_20260313_1500_1600.pdf
    cfg = job.get("config", {})
    ns_short = "PG" if "postgresql" in cfg.get("namespace", "") else "MySQL"
    st = cfg.get("start_time", "")[:16].replace("-", "").replace("T", "_").replace(":", "")
    et = cfg.get("end_time", "")[:16].replace("-", "").replace("T", "_").replace(":", "")
    pdf_name = f"REPORT_{ns_short}_{st}_{et}.pdf"

    return send_from_directory(metrics_dir, "REPORT.pdf", as_attachment=True,
                               download_name=pdf_name)


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
# Sample configs API
# ============================================================
SAMPLE_CONFIGS = {
    "mysql_benchmark": {
        "name": "MySQL Benchmark Sample",
        "description": "OCI MySQL HeatWave - mysqlslap benchmark metrics (1 OCPU/16GB)",
        "config": {
            "oci_config_file": "~/.oci/config",
            "oci_profile": "DEFAULT",
            "compartment_id": "ocid1.compartment.oc1..aaaaaaaacnzhu2qnecid46t3nhmptegxzrvbv753r4fwffpa23bd5kbqzaua",
            "namespace": "oci_mysql_database",
            "interval": "1m",
            "start_time": "2026-03-12T06:00:00Z",
            "end_time": "2026-03-12T07:00:00Z",
            "bench_start": "2026-03-12T06:29:00Z",
            "bench_end": "2026-03-12T06:33:00Z",
            "report_title": "OCI MySQL Benchmark Report",
        },
    },
    "pg_benchmark": {
        "name": "PostgreSQL Benchmark Sample",
        "description": "OCI PostgreSQL HA - PGBench benchmark metrics (2 OCPU/32GB)",
        "config": {
            "oci_config_file": "~/.oci/config",
            "oci_profile": "DEFAULT",
            "compartment_id": "ocid1.compartment.oc1..aaaaaaaacnzhu2qnecid46t3nhmptegxzrvbv753r4fwffpa23bd5kbqzaua",
            "namespace": "oci_postgresql",
            "interval": "1m",
            "start_time": "2026-03-10T09:00:00Z",
            "end_time": "2026-03-10T10:00:00Z",
            "bench_start": "2026-03-10T09:15:00Z",
            "bench_end": "2026-03-10T09:45:00Z",
            "report_title": "OCI PostgreSQL PGBench Report",
        },
    },
    "mysql_loadtest": {
        "name": "MySQL Load Test Template",
        "description": "Template for MySQL sysbench load test (edit times)",
        "config": {
            "oci_config_file": "~/.oci/config",
            "oci_profile": "DEFAULT",
            "compartment_id": "",
            "namespace": "oci_mysql_database",
            "interval": "1m",
            "start_time": "",
            "end_time": "",
            "bench_start": "",
            "bench_end": "",
            "report_title": "OCI MySQL Load Test Report",
        },
    },
}


@app.route("/api/samples")
def api_samples():
    return jsonify([
        {"id": k, "name": v["name"], "description": v["description"]}
        for k, v in SAMPLE_CONFIGS.items()
    ])


@app.route("/api/samples/<sample_id>")
def api_sample(sample_id):
    sample = SAMPLE_CONFIGS.get(sample_id)
    if not sample:
        return jsonify({"error": "Not found"}), 404
    return jsonify(sample["config"])


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
    app.run(host="0.0.0.0", port=5050, debug=False)

#!/usr/bin/env python3
"""
OCI DB Metric Report - Chart Generator

Reads collected CSV metrics + _metadata.json, generates:
  1. Overview chart (all metrics by category)
  2. Detail chart (individual metrics grid)
  3. Benchmark zoom chart (key metrics, peak annotation)

Usage:
  python3 generate_charts.py <metrics_dir> [output_dir]
"""

import argparse
import json
import os
import sys
import glob

import pandas as pd
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
from datetime import timedelta

KST_OFFSET = timedelta(hours=9)

# ============================================================
# Category definitions
# ============================================================
MYSQL_CATEGORIES = {
    "Performance (%)": {
        "metrics": ["CPUUtilization", "MemoryUtilization", "OCPUsUsed"],
        "colors": ["#e74c3c", "#3498db", "#2ecc71"],
        "desc": ["CPU Usage", "Memory Usage", "OCPU Usage"],
    },
    "Connections": {
        "metrics": ["ActiveConnections", "CurrentConnections"],
        "colors": ["#e67e22", "#9b59b6"],
        "desc": ["Active", "Current"],
    },
    "Query Performance": {
        "metrics": ["Statements", "StatementLatency"],
        "colors": ["#1abc9c", "#e74c3c"],
        "desc": ["SQL Statements (cumul.)", "Avg Latency (us)"],
    },
    "Disk IOPS": {
        "metrics": ["DbVolumeReadOperations", "DbVolumeWriteOperations"],
        "colors": ["#3498db", "#e74c3c"],
        "desc": ["Read IOPS", "Write IOPS"],
    },
    "Disk Throughput": {
        "metrics": ["DbVolumeReadBytes", "DbVolumeWriteBytes"],
        "colors": ["#3498db", "#e74c3c"],
        "desc": ["Read Bytes", "Write Bytes"],
    },
    "Network": {
        "metrics": ["NetworkReceiveBytes", "NetworkTransmitBytes"],
        "colors": ["#2ecc71", "#e67e22"],
        "desc": ["Receive", "Transmit"],
    },
    "Storage": {
        "metrics": ["StorageUsed", "StorageAllocated", "DbVolumeUtilization"],
        "colors": ["#e74c3c", "#95a5a6", "#f39c12"],
        "desc": ["Used", "Allocated", "Vol. Util.(%)"],
    },
    "Memory Detail": {
        "metrics": ["MemoryUsed", "MemoryAllocated"],
        "colors": ["#3498db", "#95a5a6"],
        "desc": ["Used (GB)", "Allocated (GB)"],
    },
}

PG_CATEGORIES = {
    "Performance (%)": {
        "metrics": ["CpuUtilization", "MemoryUtilization", "BufferCacheHitRatio"],
        "colors": ["#e74c3c", "#3498db", "#2ecc71"],
        "desc": ["CPU", "Memory", "Cache Hit Ratio"],
    },
    "Connections": {
        "metrics": ["Connections"],
        "colors": ["#e67e22"],
        "desc": ["Connections"],
    },
    "IOPS": {
        "metrics": ["ReadIops", "WriteIops"],
        "colors": ["#3498db", "#e74c3c"],
        "desc": ["Read IOPS", "Write IOPS"],
    },
    "Latency (ms)": {
        "metrics": ["ReadLatency", "WriteLatency"],
        "colors": ["#3498db", "#e74c3c"],
        "desc": ["Read Latency", "Write Latency"],
    },
    "Throughput (bytes/s)": {
        "metrics": ["ReadThroughput", "WriteThroughput"],
        "colors": ["#3498db", "#e74c3c"],
        "desc": ["Read", "Write"],
    },
    "Storage (bytes)": {
        "metrics": ["DataUsedStorage", "UsedStorage", "WalUsedStorage"],
        "colors": ["#e74c3c", "#3498db", "#f39c12"],
        "desc": ["Data Used", "Total Used", "WAL Used"],
    },
    "Safety": {
        "metrics": ["Deadlocks", "TxidWrapLimit"],
        "colors": ["#e74c3c", "#95a5a6"],
        "desc": ["Deadlocks", "TxID Wrap Limit"],
    },
}

MYSQL_KEY_METRICS = [
    ("CPUUtilization", "CPU Utilization (%)", "#e74c3c"),
    ("Statements", "SQL Statements (cumulative)", "#1abc9c"),
    ("DbVolumeWriteOperations", "Write IOPS", "#e67e22"),
    ("StatementLatency", "Statement Latency (us)", "#9b59b6"),
]

PG_KEY_METRICS = [
    ("CpuUtilization", "CPU Utilization (%)", "#e74c3c"),
    ("BufferCacheHitRatio", "Buffer Cache Hit Ratio (%)", "#2ecc71"),
    ("ReadIops", "Read IOPS", "#3498db"),
    ("WriteLatency", "Write Latency (ms)", "#e67e22"),
]


def fmt(val):
    if abs(val) >= 1e9:
        return f"{val/1e9:.1f}G"
    elif abs(val) >= 1e6:
        return f"{val/1e6:.1f}M"
    elif abs(val) >= 1e3:
        return f"{val/1e3:.1f}K"
    return f"{val:.1f}"


def load_metrics(metrics_dir):
    metrics = {}
    for f in sorted(glob.glob(os.path.join(metrics_dir, "*.csv"))):
        if os.path.getsize(f) == 0:
            continue
        name = os.path.basename(f).replace(".csv", "")
        if name.startswith("_"):
            continue
        df = pd.read_csv(f, names=["timestamp", "value"], parse_dates=["timestamp"])
        if not df.empty:
            df["timestamp"] = df["timestamp"] + KST_OFFSET  # Convert UTC → KST
            metrics[name] = df.set_index("timestamp").sort_index()["value"]
    return metrics


def load_metadata(metrics_dir):
    meta_path = os.path.join(metrics_dir, "_metadata.json")
    if os.path.exists(meta_path):
        with open(meta_path) as f:
            return json.load(f)
    return {}


def parse_bench_kst(meta, key):
    """Parse bench time from metadata and convert UTC → KST."""
    val = meta.get(key)
    if not val:
        return None
    return pd.Timestamp(val) + KST_OFFSET


def chart_overview(metrics, categories, meta, output_path):
    bench_start = parse_bench_kst(meta, "bench_start")
    bench_end = parse_bench_kst(meta, "bench_end")
    title = meta.get("report_title", "OCI DB Metric Report")

    active_cats = {k: v for k, v in categories.items()
                   if any(m in metrics for m in v["metrics"])}
    n = len(active_cats)
    if n == 0:
        return

    fig, axes = plt.subplots(n, 1, figsize=(16, 3.5 * n), sharex=True)
    if n == 1:
        axes = [axes]

    for ax, (cat_name, cat) in zip(axes, active_cats.items()):
        if bench_start and bench_end:
            ax.axvspan(bench_start, bench_end, alpha=0.15, color='red', label='Benchmark')
        for m, color, desc in zip(cat["metrics"], cat["colors"], cat["desc"]):
            if m in metrics:
                s = metrics[m]
                ax.plot(s.index, s.values, color=color, linewidth=1.5,
                        label=f"{m} ({desc})", marker='.', markersize=2)
        ax.set_ylabel(cat_name, fontsize=10, fontweight='bold')
        ax.legend(loc="upper right", fontsize=8, framealpha=0.9)
        ax.grid(True, alpha=0.3, linestyle='--')
        ax.xaxis.set_major_formatter(mdates.DateFormatter("%H:%M"))
        ax.xaxis.set_major_locator(mdates.MinuteLocator(interval=5))

    axes[-1].set_xlabel("Time (KST, UTC+9)", fontsize=11)
    period = f"{meta.get('start_time','')} ~ {meta.get('end_time','')}"
    fig.suptitle(f"{title} - Overview ({period})", fontsize=14, fontweight="bold", y=1.01)
    plt.tight_layout()
    plt.savefig(output_path, dpi=150, bbox_inches="tight")
    plt.close()
    print(f"  Saved: {output_path}")


def chart_detail(metrics, meta, output_path):
    available = [m for m in metrics if not m.startswith("_")]
    n = len(available)
    if n == 0:
        return
    cols = 3
    rows = (n + cols - 1) // cols

    bench_start = parse_bench_kst(meta, "bench_start")
    bench_end = parse_bench_kst(meta, "bench_end")

    fig, axes = plt.subplots(rows, cols, figsize=(18, 3.5 * rows))
    axes = axes.flatten()

    for i, m in enumerate(available):
        ax = axes[i]
        s = metrics[m]
        if bench_start and bench_end:
            ax.axvspan(bench_start, bench_end, alpha=0.15, color='red')
        ax.plot(s.index, s.values, color='#2980b9', linewidth=1.2, marker='.', markersize=3)
        ax.fill_between(s.index, s.values, alpha=0.1, color='#2980b9')
        stats = f"Mean: {fmt(s.mean())}\nMax: {fmt(s.max())}\nMin: {fmt(s.min())}"
        ax.text(0.02, 0.95, stats, transform=ax.transAxes, fontsize=7,
                va='top', bbox=dict(boxstyle='round', facecolor='wheat', alpha=0.8))
        ax.set_title(m, fontsize=10, fontweight='bold')
        ax.grid(True, alpha=0.3, linestyle='--')
        ax.xaxis.set_major_formatter(mdates.DateFormatter("%H:%M"))
        ax.xaxis.set_major_locator(mdates.MinuteLocator(interval=10))
        ax.tick_params(axis='both', labelsize=7)

    for j in range(n, len(axes)):
        axes[j].set_visible(False)

    title = meta.get("report_title", "OCI DB Metric Report")
    fig.suptitle(f"{title} - Individual Metrics (Red = Benchmark)",
                 fontsize=14, fontweight="bold", y=1.01)
    plt.tight_layout()
    plt.savefig(output_path, dpi=150, bbox_inches="tight")
    plt.close()
    print(f"  Saved: {output_path}")


def chart_zoom(metrics, key_metrics, meta, output_path):
    bench_start = parse_bench_kst(meta, "bench_start")
    bench_end = parse_bench_kst(meta, "bench_end")
    if not bench_start or not bench_end:
        print("  Skipped zoom chart (no benchmark window)")
        return

    # Zoom window: 5 min before bench_start ~ 10 min after bench_end
    zoom_start = bench_start - pd.Timedelta(minutes=5)
    zoom_end = bench_end + pd.Timedelta(minutes=10)

    valid = [(m, d, c) for m, d, c in key_metrics if m in metrics]
    if not valid:
        return

    fig, axes = plt.subplots(len(valid), 1, figsize=(16, 3.5 * len(valid)), sharex=True)
    if len(valid) == 1:
        axes = [axes]

    for ax, (m, desc, color) in zip(axes, valid):
        s = metrics[m]
        zoomed = s[(s.index >= zoom_start) & (s.index <= zoom_end)]
        if zoomed.empty:
            continue
        ax.axvspan(bench_start, bench_end, alpha=0.15, color='red', label='Benchmark Window')
        ax.plot(zoomed.index, zoomed.values, color=color, linewidth=2, marker='o', markersize=5)
        ax.fill_between(zoomed.index, zoomed.values, alpha=0.15, color=color)

        max_idx = zoomed.idxmax()
        max_val = zoomed.max()
        ax.annotate(f'Peak: {fmt(max_val)}',
                    xy=(max_idx, max_val), xytext=(10, 10),
                    textcoords='offset points', fontsize=9, fontweight='bold',
                    arrowprops=dict(arrowstyle='->', color=color),
                    bbox=dict(boxstyle='round,pad=0.3', facecolor='yellow', alpha=0.8))

        ax.set_ylabel(desc, fontsize=10, fontweight='bold')
        ax.legend(loc="upper right", fontsize=8)
        ax.grid(True, alpha=0.3, linestyle='--')
        ax.xaxis.set_major_formatter(mdates.DateFormatter("%H:%M"))
        ax.xaxis.set_major_locator(mdates.MinuteLocator(interval=1))

    axes[-1].set_xlabel("Time (KST, UTC+9)", fontsize=11)
    title = meta.get("report_title", "OCI DB Metric Report")
    fig.suptitle(f"{title} - Benchmark Zoom-in", fontsize=14, fontweight="bold")
    plt.tight_layout()
    plt.savefig(output_path, dpi=150, bbox_inches="tight")
    plt.close()
    print(f"  Saved: {output_path}")


def main():
    parser = argparse.ArgumentParser(description="OCI DB Metric Chart Generator")
    parser.add_argument("metrics_dir", help="Directory with CSV metric files")
    parser.add_argument("output_dir", nargs="?", default=None, help="Output dir (default: metrics_dir)")
    args = parser.parse_args()

    if not os.path.isdir(args.metrics_dir):
        print(f"Error: {args.metrics_dir} not found")
        sys.exit(1)

    out = args.output_dir or args.metrics_dir
    os.makedirs(out, exist_ok=True)

    print(f"Loading from: {args.metrics_dir}")
    metrics = load_metrics(args.metrics_dir)
    meta = load_metadata(args.metrics_dir)
    ns = meta.get("namespace", "")
    print(f"  Namespace: {ns}")
    print(f"  Metrics loaded: {len(metrics)}")

    if "mysql" in ns:
        categories = MYSQL_CATEGORIES
        key_metrics = MYSQL_KEY_METRICS
    else:
        categories = PG_CATEGORIES
        key_metrics = PG_KEY_METRICS

    print("\nGenerating charts...")
    chart_overview(metrics, categories, meta, os.path.join(out, "chart_overview.png"))
    chart_detail(metrics, meta, os.path.join(out, "chart_detail.png"))
    chart_zoom(metrics, key_metrics, meta, os.path.join(out, "chart_zoom.png"))

    # --- Stats summary as CSV ---
    stats_rows = []
    for name in sorted(metrics.keys()):
        s = metrics[name]
        stats_rows.append({
            "metric": name,
            "mean": round(s.mean(), 2),
            "max": round(s.max(), 2),
            "min": round(s.min(), 2),
            "p95": round(s.quantile(0.95), 2),
            "std": round(s.std(), 2),
            "count": len(s),
        })
    stats_df = pd.DataFrame(stats_rows)
    stats_path = os.path.join(out, "stats_summary.csv")
    stats_df.to_csv(stats_path, index=False)
    print(f"  Saved: {stats_path}")

    # --- Print summary ---
    print(f"\n{'='*80}")
    print(f"{'Metric':<30} {'Mean':>10} {'Max':>10} {'Min':>10} {'P95':>10} {'Std':>10}")
    print(f"{'='*80}")
    for _, r in stats_df.iterrows():
        print(f"{r['metric']:<30} {fmt(r['mean']):>10} {fmt(r['max']):>10} "
              f"{fmt(r['min']):>10} {fmt(r['p95']):>10} {fmt(r['std']):>10}")
    print(f"{'='*80}")


if __name__ == "__main__":
    main()

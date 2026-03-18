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


def utc_to_kst_str(iso_str):
    """Convert UTC ISO string to KST display string."""
    if not iso_str:
        return ""
    ts = pd.Timestamp(iso_str) + KST_OFFSET
    return ts.strftime("%Y-%m-%d %H:%M KST")


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
    "Replica - Performance (%)": {
        "metrics": ["REPLICA_CPUUtilization", "REPLICA_MemoryUtilization"],
        "colors": ["#c0392b", "#2980b9"],
        "desc": ["Replica CPU", "Replica Memory"],
    },
    "Replica - Replication": {
        "metrics": ["REPLICA_ChannelLag", "REPLICA_ChannelFailure"],
        "colors": ["#e67e22", "#e74c3c"],
        "desc": ["Channel Lag (s)", "Channel Failure"],
    },
    "Replica - Connections": {
        "metrics": ["REPLICA_ActiveConnections", "REPLICA_CurrentConnections"],
        "colors": ["#e67e22", "#9b59b6"],
        "desc": ["Active", "Current"],
    },
    "Replica - Disk IOPS": {
        "metrics": ["REPLICA_DbVolumeReadOperations", "REPLICA_DbVolumeWriteOperations"],
        "colors": ["#3498db", "#e74c3c"],
        "desc": ["Read IOPS", "Write IOPS"],
    },
}

PG_CATEGORIES = {
    "Performance (%) - PRIMARY": {
        "metrics": ["PRIMARY_CpuUtilization", "PRIMARY_MemoryUtilization", "PRIMARY_BufferCacheHitRatio"],
        "colors": ["#e74c3c", "#3498db", "#2ecc71"],
        "desc": ["CPU", "Memory", "Cache Hit Ratio"],
    },
    "Performance (%) - READ_REPLICA": {
        "metrics": ["READ_REPLICA_CpuUtilization", "READ_REPLICA_MemoryUtilization", "READ_REPLICA_BufferCacheHitRatio"],
        "colors": ["#e74c3c", "#3498db", "#2ecc71"],
        "desc": ["CPU", "Memory", "Cache Hit Ratio"],
    },
    "Connections": {
        "metrics": ["PRIMARY_Connections", "READ_REPLICA_Connections"],
        "colors": ["#e67e22", "#9b59b6"],
        "desc": ["PRIMARY", "READ_REPLICA"],
    },
    "IOPS - PRIMARY": {
        "metrics": ["PRIMARY_ReadIops", "PRIMARY_WriteIops"],
        "colors": ["#3498db", "#e74c3c"],
        "desc": ["Read IOPS", "Write IOPS"],
    },
    "IOPS - READ_REPLICA": {
        "metrics": ["READ_REPLICA_ReadIops", "READ_REPLICA_WriteIops"],
        "colors": ["#3498db", "#e74c3c"],
        "desc": ["Read IOPS", "Write IOPS"],
    },
    "Latency (ms)": {
        "metrics": ["PRIMARY_ReadLatency", "PRIMARY_WriteLatency", "READ_REPLICA_ReadLatency", "READ_REPLICA_WriteLatency"],
        "colors": ["#3498db", "#e74c3c", "#1abc9c", "#e67e22"],
        "desc": ["P-Read", "P-Write", "RR-Read", "RR-Write"],
    },
    "Throughput (bytes/s)": {
        "metrics": ["PRIMARY_ReadThroughput", "PRIMARY_WriteThroughput", "READ_REPLICA_ReadThroughput", "READ_REPLICA_WriteThroughput"],
        "colors": ["#3498db", "#e74c3c", "#1abc9c", "#e67e22"],
        "desc": ["P-Read", "P-Write", "RR-Read", "RR-Write"],
    },
    "Storage (bytes)": {
        "metrics": ["PRIMARY_DataUsedStorage", "PRIMARY_UsedStorage", "PRIMARY_WalUsedStorage"],
        "colors": ["#e74c3c", "#3498db", "#f39c12"],
        "desc": ["Data Used", "Total Used", "WAL Used"],
    },
    "Safety": {
        "metrics": ["PRIMARY_Deadlocks", "PRIMARY_TxidWrapLimit"],
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
    ("PRIMARY_CpuUtilization", "PRIMARY CPU Utilization (%)", "#e74c3c"),
    ("PRIMARY_BufferCacheHitRatio", "PRIMARY Buffer Cache Hit (%)", "#2ecc71"),
    ("PRIMARY_ReadIops", "PRIMARY Read IOPS", "#3498db"),
    ("PRIMARY_WriteLatency", "PRIMARY Write Latency (ms)", "#e67e22"),
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
        if name.startswith("_") or name.startswith("stats"):
            continue
        df = pd.read_csv(f, names=["timestamp", "value"])
        if not df.empty:
            df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True)
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


def auto_xaxis(ax, hours_span):
    """Set x-axis locator and formatter based on time span."""
    if hours_span <= 2:
        ax.xaxis.set_major_locator(mdates.MinuteLocator(interval=5))
        ax.xaxis.set_major_formatter(mdates.DateFormatter("%H:%M"))
    elif hours_span <= 6:
        ax.xaxis.set_major_locator(mdates.MinuteLocator(interval=15))
        ax.xaxis.set_major_formatter(mdates.DateFormatter("%H:%M"))
    elif hours_span <= 12:
        ax.xaxis.set_major_locator(mdates.HourLocator(interval=1))
        ax.xaxis.set_major_formatter(mdates.DateFormatter("%H:%M"))
    elif hours_span <= 24:
        ax.xaxis.set_major_locator(mdates.HourLocator(interval=2))
        ax.xaxis.set_major_formatter(mdates.DateFormatter("%m/%d %H:%M"))
    else:
        ax.xaxis.set_major_locator(mdates.HourLocator(interval=6))
        ax.xaxis.set_major_formatter(mdates.DateFormatter("%m/%d %H:%M"))


def get_hours_span(meta):
    """Calculate time span in hours from metadata."""
    try:
        s = pd.Timestamp(meta.get("start_time", ""))
        e = pd.Timestamp(meta.get("end_time", ""))
        return (e - s).total_seconds() / 3600
    except Exception:
        return 1


def get_data_hours_span(metrics):
    """Calculate actual data time span from loaded metrics."""
    all_min, all_max = None, None
    for s in metrics.values():
        if len(s) > 0:
            smin, smax = s.index.min(), s.index.max()
            if all_min is None or smin < all_min:
                all_min = smin
            if all_max is None or smax > all_max:
                all_max = smax
    if all_min and all_max:
        return (all_max - all_min).total_seconds() / 3600
    return 1


def chart_overview(metrics, categories, meta, output_path):
    bench_start = parse_bench_kst(meta, "bench_start")
    bench_end = parse_bench_kst(meta, "bench_end")
    title = meta.get("report_title", "OCI DB Metric Report")

    hours = get_data_hours_span(metrics) or get_hours_span(meta)

    active_cats = {k: v for k, v in categories.items()
                   if any(m in metrics for m in v["metrics"])}
    n = len(active_cats)
    if n == 0:
        return

    fig, axes = plt.subplots(n, 1, figsize=(16, 3.5 * n))
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
        auto_xaxis(ax, hours)
        ax.tick_params(axis='x', labelsize=8, rotation=30 if hours > 6 else 0)
        ax.set_xlabel("Time (KST)", fontsize=9, color='#656d76')
    period = f"{utc_to_kst_str(meta.get('start_time',''))} ~ {utc_to_kst_str(meta.get('end_time',''))}"
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

    hours = get_hours_span(meta)

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
        auto_xaxis(ax, hours)
        ax.tick_params(axis='both', labelsize=7, rotation=30 if hours > 6 else 0)

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

    # --- Generate analysis ---
    print("\nGenerating bottleneck analysis...")
    analysis = analyze_metrics(metrics, ns)
    analysis_path = os.path.join(out, "analysis.md")
    with open(analysis_path, "w") as f:
        f.write(analysis)
    print(f"  Saved: {analysis_path}")


# ============================================================
# Bottleneck Analysis Engine
# ============================================================

def get_stat(metrics, name):
    """Get stats for a metric (case-insensitive match)."""
    for k, s in metrics.items():
        if k.lower() == name.lower():
            return {
                "mean": s.mean(), "max": s.max(), "min": s.min(),
                "p95": s.quantile(0.95), "std": s.std(), "count": len(s),
                "series": s,
            }
    return None


def severity_icon(level):
    if level == "critical":
        return "[CRITICAL]"
    elif level == "warning":
        return "[WARNING]"
    elif level == "good":
        return "[GOOD]"
    return "[INFO]"


def analyze_metrics(metrics, namespace):
    """Analyze collected metrics and return markdown analysis."""
    lines = []
    lines.append("## Bottleneck Analysis & Recommendations\n")

    if not metrics:
        lines.append("No metrics data available for analysis.\n")
        return "\n".join(lines)

    findings = []
    recommendations = []

    is_mysql = "mysql" in namespace.lower()

    # ---- 1. CPU Analysis ----
    cpu_name = "CPUUtilization" if is_mysql else "PRIMARY_CpuUtilization"
    cpu = get_stat(metrics, cpu_name)
    if cpu:
        lines.append("### 1. CPU Utilization\n")
        lines.append(f"| Metric | Mean | Max | P95 | Std |")
        lines.append(f"|--------|------|-----|-----|-----|")
        lines.append(f"| {cpu_name} | {cpu['mean']:.1f}% | {cpu['max']:.1f}% | {cpu['p95']:.1f}% | {cpu['std']:.1f} |")
        lines.append("")

        if cpu["max"] >= 90:
            findings.append(f"{severity_icon('critical')} CPU 최대 {cpu['max']:.1f}% — CPU 포화 상태 감지")
            recommendations.append("- **CPU Scale-up 필요**: OCPU/ECPU 수를 증가시키거나 Shape 업그레이드 검토")
            recommendations.append("- 슬로우 쿼리 분석: `EXPLAIN`으로 풀스캔 쿼리 최적화")
            recommendations.append("- 인덱스 최적화: 자주 사용되는 WHERE/JOIN 컬럼에 인덱스 추가")
        elif cpu["p95"] >= 70:
            findings.append(f"{severity_icon('warning')} CPU P95 {cpu['p95']:.1f}% — 피크 시간대 부하 높음")
            recommendations.append("- 피크 시간대 워크로드 분산 또는 Read Replica 활용 검토")
            recommendations.append("- 쿼리 캐싱 및 커넥션 풀링 최적화")
        elif cpu["mean"] < 10:
            findings.append(f"{severity_icon('good')} CPU 평균 {cpu['mean']:.1f}% — 여유 충분 (오버프로비저닝 가능성)")
            recommendations.append("- Shape 다운사이징으로 비용 절감 가능성 검토")
        else:
            findings.append(f"{severity_icon('good')} CPU 평균 {cpu['mean']:.1f}%, 최대 {cpu['max']:.1f}% — 정상 범위")
        lines.append("")

    # ---- 2. Memory Analysis ----
    mem_name = "MemoryUtilization" if is_mysql else "PRIMARY_MemoryUtilization"
    mem = get_stat(metrics, mem_name)
    if mem:
        lines.append("### 2. Memory Utilization\n")
        lines.append(f"| Metric | Mean | Max | P95 |")
        lines.append(f"|--------|------|-----|-----|")
        lines.append(f"| MemoryUtilization | {mem['mean']:.1f}% | {mem['max']:.1f}% | {mem['p95']:.1f}% |")

        if is_mysql:
            mem_used = get_stat(metrics, "MemoryUsed")
            mem_alloc = get_stat(metrics, "MemoryAllocated")
            if mem_used and mem_alloc:
                lines.append(f"| MemoryUsed | {fmt(mem_used['mean'])} | {fmt(mem_used['max'])} | {fmt(mem_used['p95'])} |")
                lines.append(f"| MemoryAllocated | {fmt(mem_alloc['mean'])} | {fmt(mem_alloc['max'])} | - |")
        lines.append("")

        if mem["max"] >= 95:
            findings.append(f"{severity_icon('critical')} Memory 최대 {mem['max']:.1f}% — OOM 위험")
            recommendations.append("- **메모리 증설 필요**: 더 큰 Shape으로 업그레이드")
            recommendations.append("- Buffer Pool 크기 점검 및 불필요한 세션 정리")
        elif mem["p95"] >= 80:
            findings.append(f"{severity_icon('warning')} Memory P95 {mem['p95']:.1f}% — 메모리 압박")
            recommendations.append("- 메모리 사용 패턴 모니터링 강화")
            recommendations.append("- 대용량 쿼리의 `sort_buffer_size`, `join_buffer_size` 점검")
        else:
            findings.append(f"{severity_icon('good')} Memory 평균 {mem['mean']:.1f}% — 정상 범위")
        lines.append("")

    # ---- 3. I/O Analysis ----
    if is_mysql:
        read_iops = get_stat(metrics, "DbVolumeReadOperations")
        write_iops = get_stat(metrics, "DbVolumeWriteOperations")
        read_bytes = get_stat(metrics, "DbVolumeReadBytes")
        write_bytes = get_stat(metrics, "DbVolumeWriteBytes")
        vol_util = get_stat(metrics, "DbVolumeUtilization")
    else:
        read_iops = get_stat(metrics, "PRIMARY_ReadIops")
        write_iops = get_stat(metrics, "PRIMARY_WriteIops")
        read_bytes = get_stat(metrics, "PRIMARY_ReadThroughput")
        write_bytes = get_stat(metrics, "PRIMARY_WriteThroughput")
        vol_util = None

    if read_iops or write_iops:
        lines.append("### 3. Disk I/O\n")
        lines.append(f"| Metric | Mean | Max | P95 |")
        lines.append(f"|--------|------|-----|-----|")
        if read_iops:
            lines.append(f"| Read IOPS | {fmt(read_iops['mean'])} | {fmt(read_iops['max'])} | {fmt(read_iops['p95'])} |")
        if write_iops:
            lines.append(f"| Write IOPS | {fmt(write_iops['mean'])} | {fmt(write_iops['max'])} | {fmt(write_iops['p95'])} |")
        if read_bytes:
            lines.append(f"| Read Throughput | {fmt(read_bytes['mean'])} B/s | {fmt(read_bytes['max'])} B/s | {fmt(read_bytes['p95'])} B/s |")
        if write_bytes:
            lines.append(f"| Write Throughput | {fmt(write_bytes['mean'])} B/s | {fmt(write_bytes['max'])} B/s | {fmt(write_bytes['p95'])} B/s |")
        if vol_util:
            lines.append(f"| Volume Utilization | {vol_util['mean']:.1f}% | {vol_util['max']:.1f}% | {vol_util['p95']:.1f}% |")
        lines.append("")

        # I/O ratio analysis
        if read_iops and write_iops and read_iops["mean"] > 0:
            rw_ratio = read_iops["mean"] / max(write_iops["mean"], 0.01)
            lines.append(f"**Read/Write Ratio**: {rw_ratio:.1f}:1 ({'Read-heavy' if rw_ratio > 3 else 'Write-heavy' if rw_ratio < 0.3 else 'Balanced'})\n")

        total_max_iops = (read_iops["max"] if read_iops else 0) + (write_iops["max"] if write_iops else 0)
        if total_max_iops > 50000:
            findings.append(f"{severity_icon('critical')} I/O 최대 {fmt(total_max_iops)} IOPS — 디스크 병목 가능성")
            recommendations.append("- **스토리지 성능 업그레이드**: Higher Performance 볼륨 또는 Shape 변경")
            recommendations.append("- 읽기 부하가 높으면 Read Replica 추가")
        elif total_max_iops > 20000:
            findings.append(f"{severity_icon('warning')} I/O 최대 {fmt(total_max_iops)} IOPS — 피크 시 디스크 부하 높음")
            recommendations.append("- 인덱스 최적화로 불필요한 디스크 I/O 감소")
        else:
            findings.append(f"{severity_icon('good')} I/O 최대 {fmt(total_max_iops)} IOPS — 정상 범위")

        if vol_util and vol_util["max"] >= 80:
            findings.append(f"{severity_icon('warning')} Volume Utilization 최대 {vol_util['max']:.1f}% — 디스크 포화 주의")
            recommendations.append("- 스토리지 확장 또는 오래된 데이터 아카이빙 검토")
        lines.append("")

    # ---- 4. Connections ----
    if is_mysql:
        active_conn = get_stat(metrics, "ActiveConnections")
        current_conn = get_stat(metrics, "CurrentConnections")
    else:
        active_conn = get_stat(metrics, "PRIMARY_Connections")
        current_conn = None

    if active_conn:
        lines.append("### 4. Connections\n")
        lines.append(f"| Metric | Mean | Max | P95 |")
        lines.append(f"|--------|------|-----|-----|")
        lines.append(f"| Active Connections | {fmt(active_conn['mean'])} | {fmt(active_conn['max'])} | {fmt(active_conn['p95'])} |")
        if current_conn:
            lines.append(f"| Current Connections | {fmt(current_conn['mean'])} | {fmt(current_conn['max'])} | {fmt(current_conn['p95'])} |")
        lines.append("")

        if active_conn["max"] > 500:
            findings.append(f"{severity_icon('warning')} Active Connection 최대 {active_conn['max']:.0f} — 커넥션 풀 부족 가능성")
            recommendations.append("- 커넥션 풀링 (ProxySQL, PgBouncer) 도입 검토")
            recommendations.append("- `max_connections` 파라미터 확인 및 조정")
        elif active_conn["max"] > 200:
            findings.append(f"{severity_icon('info')} Active Connection 최대 {active_conn['max']:.0f} — 중간 수준")
        else:
            findings.append(f"{severity_icon('good')} Active Connection 최대 {active_conn['max']:.0f} — 정상 범위")
        lines.append("")

    # ---- 5. Query Performance (MySQL) ----
    if is_mysql:
        stmts = get_stat(metrics, "Statements")
        latency = get_stat(metrics, "StatementLatency")
        if stmts or latency:
            lines.append("### 5. Query Performance\n")
            lines.append(f"| Metric | Mean | Max | P95 |")
            lines.append(f"|--------|------|-----|-----|")
            if stmts:
                lines.append(f"| Statements (cumul.) | {fmt(stmts['mean'])} | {fmt(stmts['max'])} | {fmt(stmts['p95'])} |")
            if latency:
                lines.append(f"| Statement Latency (us) | {fmt(latency['mean'])} | {fmt(latency['max'])} | {fmt(latency['p95'])} |")
            lines.append("")

            if latency and latency["p95"] > 100000:  # > 100ms
                findings.append(f"{severity_icon('critical')} 쿼리 레이턴시 P95 {fmt(latency['p95'])}us — 슬로우 쿼리 다수 존재")
                recommendations.append("- Slow Query Log 활성화 후 상위 쿼리 분석")
                recommendations.append("- `EXPLAIN ANALYZE`로 실행계획 확인")
                recommendations.append("- 적절한 인덱스 추가 및 쿼리 리팩터링")
            elif latency and latency["p95"] > 10000:  # > 10ms
                findings.append(f"{severity_icon('warning')} 쿼리 레이턴시 P95 {fmt(latency['p95'])}us — 개선 여지 있음")
                recommendations.append("- 쿼리 실행계획 주기적 점검 권장")
            elif latency:
                findings.append(f"{severity_icon('good')} 쿼리 레이턴시 P95 {fmt(latency['p95'])}us — 양호")
            lines.append("")

    # ---- 6. PostgreSQL Safety ----
    if not is_mysql:
        deadlocks = get_stat(metrics, "PRIMARY_Deadlocks")
        cache_hit = get_stat(metrics, "PRIMARY_BufferCacheHitRatio")
        read_lat = get_stat(metrics, "PRIMARY_ReadLatency")
        write_lat = get_stat(metrics, "PRIMARY_WriteLatency")

        if cache_hit:
            lines.append("### 5. Buffer Cache\n")
            lines.append(f"| Metric | Mean | Min | P95 |")
            lines.append(f"|--------|------|-----|-----|")
            lines.append(f"| BufferCacheHitRatio | {cache_hit['mean']:.2f}% | {cache_hit['min']:.2f}% | {cache_hit['p95']:.2f}% |")
            lines.append("")
            if cache_hit["min"] < 90:
                findings.append(f"{severity_icon('warning')} Buffer Cache Hit Ratio 최저 {cache_hit['min']:.1f}% — 캐시 미스 빈번")
                recommendations.append("- `shared_buffers` 증가 검토")
                recommendations.append("- 워킹셋이 메모리보다 큰 경우 Shape 업그레이드")
            else:
                findings.append(f"{severity_icon('good')} Buffer Cache Hit Ratio 최저 {cache_hit['min']:.1f}% — 양호")
            lines.append("")

        if read_lat or write_lat:
            lines.append("### 6. Latency\n")
            lines.append(f"| Metric | Mean | Max | P95 |")
            lines.append(f"|--------|------|-----|-----|")
            if read_lat:
                lines.append(f"| Read Latency (ms) | {read_lat['mean']:.2f} | {read_lat['max']:.2f} | {read_lat['p95']:.2f} |")
            if write_lat:
                lines.append(f"| Write Latency (ms) | {write_lat['mean']:.2f} | {write_lat['max']:.2f} | {write_lat['p95']:.2f} |")
            lines.append("")
            if write_lat and write_lat["p95"] > 10:
                findings.append(f"{severity_icon('warning')} Write Latency P95 {write_lat['p95']:.1f}ms — 쓰기 지연")
                recommendations.append("- WAL 설정 최적화 (`wal_buffers`, `checkpoint_completion_target`)")
            if read_lat and read_lat["p95"] > 5:
                findings.append(f"{severity_icon('warning')} Read Latency P95 {read_lat['p95']:.1f}ms — 읽기 지연")
                recommendations.append("- 캐시 히트율 개선 또는 스토리지 업그레이드")
            lines.append("")

        if deadlocks and deadlocks["max"] > 0:
            findings.append(f"{severity_icon('critical')} Deadlock 감지: 최대 {deadlocks['max']:.0f}회")
            recommendations.append("- 트랜잭션 순서 재설계")
            recommendations.append("- Lock timeout 및 deadlock 로그 분석")

    # ---- Replica Analysis (MySQL) ----
    if is_mysql:
        replica_lag = get_stat(metrics, "REPLICA_ChannelLag")
        replica_fail = get_stat(metrics, "REPLICA_ChannelFailure")
        replica_cpu = get_stat(metrics, "REPLICA_CPUUtilization")
        if replica_lag or replica_fail or replica_cpu:
            lines.append("### 6. Read Replica\n")
            lines.append(f"| Metric | Mean | Max | P95 |")
            lines.append(f"|--------|------|-----|-----|")
            if replica_cpu:
                lines.append(f"| Replica CPU | {replica_cpu['mean']:.1f}% | {replica_cpu['max']:.1f}% | {replica_cpu['p95']:.1f}% |")
            if replica_lag:
                lines.append(f"| Channel Lag (s) | {replica_lag['mean']:.2f} | {replica_lag['max']:.2f} | {replica_lag['p95']:.2f} |")
            if replica_fail:
                lines.append(f"| Channel Failure | {replica_fail['mean']:.0f} | {replica_fail['max']:.0f} | - |")
            lines.append("")

            if replica_fail and replica_fail["max"] > 0:
                findings.append(f"{severity_icon('critical')} Replica 복제 실패 감지 — Channel Failure = {replica_fail['max']:.0f}")
                recommendations.append("- **즉시 복제 상태 점검**: `SHOW REPLICA STATUS` 확인")
                recommendations.append("- 복제 채널 재시작 또는 Replica 재생성 검토")
            if replica_lag and replica_lag["p95"] > 10:
                findings.append(f"{severity_icon('warning')} Replica 복제 지연 P95 {replica_lag['p95']:.1f}s — Source 대비 데이터 불일치 가능")
                recommendations.append("- Source 쓰기 부하 분산 또는 Replica Shape 업그레이드")
            elif replica_lag and replica_lag["max"] > 5:
                findings.append(f"{severity_icon('warning')} Replica 복제 지연 최대 {replica_lag['max']:.1f}s")
            elif replica_lag:
                findings.append(f"{severity_icon('good')} Replica 복제 지연 평균 {replica_lag['mean']:.2f}s — 정상")
            lines.append("")

    # ---- 7. Storage ----
    if is_mysql:
        stor_used = get_stat(metrics, "StorageUsed")
        stor_alloc = get_stat(metrics, "StorageAllocated")
    else:
        stor_used = get_stat(metrics, "PRIMARY_UsedStorage")
        stor_alloc = get_stat(metrics, "PRIMARY_DataUsedStorage")

    if stor_used:
        lines.append(f"### {'7' if is_mysql else '7'}. Storage\n")
        lines.append(f"| Metric | Mean | Max |")
        lines.append(f"|--------|------|-----|")
        lines.append(f"| Storage Used | {fmt(stor_used['mean'])} B | {fmt(stor_used['max'])} B |")
        if stor_alloc:
            lines.append(f"| Storage Allocated | {fmt(stor_alloc['mean'])} B | {fmt(stor_alloc['max'])} B |")
            if stor_alloc["mean"] > 0:
                usage_pct = stor_used["mean"] / stor_alloc["mean"] * 100
                lines.append(f"\n**Storage Usage**: {usage_pct:.1f}%\n")
                if usage_pct > 80:
                    findings.append(f"{severity_icon('warning')} Storage 사용률 {usage_pct:.1f}% — 디스크 공간 부족 주의")
                    recommendations.append("- 스토리지 확장 또는 데이터 아카이빙/파티셔닝 검토")
        lines.append("")

    # ---- 8. Network ----
    net_rx = get_stat(metrics, "NetworkReceiveBytes")
    net_tx = get_stat(metrics, "NetworkTransmitBytes")
    if net_rx or net_tx:
        lines.append("### 8. Network\n")
        lines.append(f"| Metric | Mean | Max | P95 |")
        lines.append(f"|--------|------|-----|-----|")
        if net_rx:
            lines.append(f"| Network Receive | {fmt(net_rx['mean'])} B/s | {fmt(net_rx['max'])} B/s | {fmt(net_rx['p95'])} B/s |")
        if net_tx:
            lines.append(f"| Network Transmit | {fmt(net_tx['mean'])} B/s | {fmt(net_tx['max'])} B/s | {fmt(net_tx['p95'])} B/s |")
        lines.append("")

    # ========== Summary ==========
    lines.append("---\n")
    lines.append("### Findings Summary\n")
    if findings:
        for f in findings:
            lines.append(f"- {f}")
    else:
        lines.append("- No significant issues detected.")
    lines.append("")

    lines.append("### Recommendations\n")
    if recommendations:
        seen = set()
        for r in recommendations:
            if r not in seen:
                lines.append(r)
                seen.add(r)
    else:
        lines.append("- Current configuration appears appropriate for the observed workload.")
    lines.append("")

    # ---- Overall Assessment ----
    critical_count = sum(1 for f in findings if "[CRITICAL]" in f)
    warning_count = sum(1 for f in findings if "[WARNING]" in f)

    lines.append("### Overall Assessment\n")
    if critical_count > 0:
        lines.append(f"**Status: Attention Required** — {critical_count} critical issue(s) detected.")
        lines.append("즉각적인 조치가 필요한 병목 지점이 발견되었습니다. 위 권장사항을 우선 검토하세요.")
    elif warning_count > 0:
        lines.append(f"**Status: Monitor Closely** — {warning_count} warning(s) detected.")
        lines.append("현재 운영에 문제는 없으나 피크 시간대에 성능 저하 가능성이 있습니다.")
    else:
        lines.append("**Status: Healthy** — No significant issues found.")
        lines.append("현재 워크로드 대비 리소스 구성이 적절합니다.")
    lines.append("")

    return "\n".join(lines)


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""
Generate PDF report from metrics directory.
Uses fpdf2 with Unicode font (Noto Sans KR) for Korean support.
"""

import json
import os
import sys
import urllib.request
from datetime import timedelta


FONT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fonts")
FONT_URL = "https://github.com/google/fonts/raw/main/ofl/notosanskr/NotoSansKR%5Bwght%5D.ttf"
FONT_FILE = os.path.join(FONT_DIR, "NotoSansKR.ttf")


def ensure_font():
    """Download Noto Sans KR if not present."""
    if os.path.isfile(FONT_FILE):
        return
    os.makedirs(FONT_DIR, exist_ok=True)
    print(f"Downloading Korean font to {FONT_FILE}...")
    urllib.request.urlretrieve(FONT_URL, FONT_FILE)
    print("  Font downloaded.")


def generate_pdf(metrics_dir):
    """Generate REPORT.pdf from metrics data, charts, and analysis."""
    pdf_path = os.path.join(metrics_dir, "REPORT.pdf")

    # Read metadata
    meta_path = os.path.join(metrics_dir, "_metadata.json")
    meta = {}
    if os.path.isfile(meta_path):
        with open(meta_path) as f:
            meta = json.load(f)

    # Read analysis
    analysis = ""
    analysis_path = os.path.join(metrics_dir, "analysis.md")
    if os.path.isfile(analysis_path):
        with open(analysis_path) as f:
            analysis = f.read()

    # Read stats
    stats_lines = []
    stats_path = os.path.join(metrics_dir, "stats_summary.csv")
    if os.path.isfile(stats_path):
        with open(stats_path) as f:
            stats_lines = f.read().strip().split("\n")

    # Chart images
    charts = []
    for name in ["chart_overview.png", "chart_detail.png", "chart_zoom.png"]:
        p = os.path.join(metrics_dir, name)
        if os.path.isfile(p):
            charts.append((name.replace("chart_", "").replace(".png", "").title(), p))

    try:
        from fpdf import FPDF
    except ImportError:
        import subprocess
        subprocess.check_call([sys.executable, "-m", "pip", "install", "fpdf2", "--quiet"])
        from fpdf import FPDF

    ensure_font()

    KST_OFFSET = timedelta(hours=9)

    def utc_to_kst(iso_str):
        if not iso_str:
            return ""
        from datetime import datetime
        try:
            dt = datetime.strptime(iso_str.rstrip("Z"), "%Y-%m-%dT%H:%M:%S")
            kst = dt + KST_OFFSET
            return kst.strftime("%Y-%m-%d %H:%M KST")
        except Exception:
            return iso_str

    pdf = FPDF()
    pdf.set_auto_page_break(auto=True, margin=15)

    # Register Unicode font
    pdf.add_font("NotoKR", "", FONT_FILE)
    pdf.add_font("NotoKR", "B", FONT_FILE)
    pdf.add_font("NotoKR", "I", FONT_FILE)

    def set_font(style="", size=10):
        pdf.set_font("NotoKR", style, size)

    # --- Title page ---
    pdf.add_page()
    set_font("B", 22)
    pdf.ln(30)
    title = meta.get("report_title", "OCI DB Metric Report")
    pdf.cell(0, 15, title, new_x="LMARGIN", new_y="NEXT", align="C")
    pdf.ln(10)

    set_font("", 11)
    ns = meta.get("namespace", "")
    start_kst = utc_to_kst(meta.get("start_time", ""))
    end_kst = utc_to_kst(meta.get("end_time", ""))
    collected = meta.get("collected_at", "")
    interval = meta.get("interval", "1m")
    profile = meta.get("oci_profile", "DEFAULT")

    info_lines = [
        f"Namespace: {ns}",
        f"Period: {start_kst} ~ {end_kst}",
        f"Interval: {interval}",
        f"OCI Profile: {profile}",
        f"Generated: {collected}",
    ]
    for line in info_lines:
        pdf.cell(0, 8, line, new_x="LMARGIN", new_y="NEXT", align="C")

    pdf.ln(10)
    pdf.set_draw_color(200, 200, 200)
    pdf.line(20, pdf.get_y(), 190, pdf.get_y())

    # --- Charts ---
    for label, img_path in charts:
        pdf.add_page("L")  # Landscape for charts
        set_font("B", 13)
        pdf.cell(0, 10, f"Chart: {label}", new_x="LMARGIN", new_y="NEXT")
        pdf.ln(2)
        try:
            img_w = 267  # landscape A4 width minus margins
            pdf.image(img_path, x=15, w=img_w)
        except Exception as e:
            set_font("", 10)
            pdf.cell(0, 8, f"(Image load error: {e})", new_x="LMARGIN", new_y="NEXT")

    # --- Stats table ---
    if len(stats_lines) > 1:
        pdf.add_page()
        set_font("B", 14)
        pdf.cell(0, 10, "Statistics Summary", new_x="LMARGIN", new_y="NEXT")
        pdf.ln(4)

        headers = stats_lines[0].split(",")
        col_widths = [50, 25, 25, 25, 25, 25, 15]
        if len(col_widths) < len(headers):
            col_widths = [190 // len(headers)] * len(headers)

        set_font("B", 8)
        pdf.set_fill_color(240, 240, 240)
        for i, h in enumerate(headers):
            w = col_widths[i] if i < len(col_widths) else 25
            pdf.cell(w, 7, h, border=1, fill=True, align="C")
        pdf.ln()

        set_font("", 7)
        for row_line in stats_lines[1:]:
            cols = row_line.split(",")
            for i, val in enumerate(cols):
                w = col_widths[i] if i < len(col_widths) else 25
                pdf.cell(w, 6, val, border=1, align="C" if i > 0 else "L")
            pdf.ln()

    # --- Analysis ---
    if analysis:
        pdf.add_page()
        set_font("B", 14)
        pdf.cell(0, 10, "Bottleneck Analysis & Recommendations", new_x="LMARGIN", new_y="NEXT")
        pdf.ln(4)

        set_font("", 9)
        for line in analysis.split("\n"):
            stripped = line.strip()
            if stripped.startswith("### "):
                pdf.ln(4)
                set_font("B", 11)
                pdf.cell(0, 7, stripped.replace("### ", ""), new_x="LMARGIN", new_y="NEXT")
                set_font("", 9)
            elif stripped.startswith("## "):
                pdf.ln(6)
                set_font("B", 13)
                pdf.cell(0, 8, stripped.replace("## ", ""), new_x="LMARGIN", new_y="NEXT")
                set_font("", 9)
            elif stripped.startswith("|") and "---" not in stripped:
                cells = [c.strip() for c in stripped.split("|")[1:-1]]
                if cells:
                    cell_w = 190 // max(len(cells), 1)
                    for c in cells:
                        text = c.replace("**", "")
                        pdf.cell(cell_w, 6, text, border=1, align="C")
                    pdf.ln()
            elif stripped.startswith("- "):
                text = stripped[2:].replace("**", "")
                pdf.cell(5, 6, "")
                pdf.multi_cell(180, 6, f"  * {text}")
            elif stripped.startswith("---"):
                pdf.ln(2)
                pdf.line(20, pdf.get_y(), 190, pdf.get_y())
                pdf.ln(2)
            elif stripped:
                text = stripped.replace("**", "")
                pdf.multi_cell(0, 6, text)
            else:
                pdf.ln(2)

    # --- Footer ---
    pdf.add_page()
    set_font("I", 10)
    pdf.ln(20)
    pdf.cell(0, 8, "Generated by oci-db-metric-report", new_x="LMARGIN", new_y="NEXT", align="C")
    pdf.cell(0, 8, "https://github.com/jaesucjang/oci-db-metric-report", new_x="LMARGIN", new_y="NEXT", align="C")

    pdf.output(pdf_path)
    print(f"PDF saved: {pdf_path}")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python3 generate_pdf.py <metrics_dir>")
        sys.exit(1)
    generate_pdf(sys.argv[1])

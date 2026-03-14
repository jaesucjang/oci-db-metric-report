#!/usr/bin/env python3
"""
Generate PDF report from metrics directory.
Uses fpdf2 with Noto Sans KR for Korean support.
All pages portrait A4, consistent margins.
"""

import json
import os
import sys
import urllib.request
from datetime import timedelta

FONT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fonts")
FONT_URL = "https://github.com/google/fonts/raw/main/ofl/notosanskr/NotoSansKR%5Bwght%5D.ttf"
FONT_FILE = os.path.join(FONT_DIR, "NotoSansKR.ttf")

# Layout constants (A4 portrait: 210 x 297 mm)
MARGIN = 20
PAGE_W = 210
CONTENT_W = PAGE_W - MARGIN * 2  # 170mm usable


def ensure_font():
    if os.path.isfile(FONT_FILE):
        return
    os.makedirs(FONT_DIR, exist_ok=True)
    print(f"Downloading Korean font to {FONT_FILE}...")
    urllib.request.urlretrieve(FONT_URL, FONT_FILE)
    print("  Font downloaded.")


def generate_pdf(metrics_dir):
    pdf_path = os.path.join(metrics_dir, "REPORT.pdf")

    # Read metadata
    meta = {}
    meta_path = os.path.join(metrics_dir, "_metadata.json")
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
            charts.append((name.replace("chart_", "").replace(".png", "").replace("_", " ").title(), p))

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
    pdf.set_margins(MARGIN, MARGIN, MARGIN)
    pdf.set_auto_page_break(auto=True, margin=20)

    # Register Unicode font
    pdf.add_font("NotoKR", "", FONT_FILE)
    pdf.add_font("NotoKR", "B", FONT_FILE)
    pdf.add_font("NotoKR", "I", FONT_FILE)

    def font(style="", size=10):
        pdf.set_font("NotoKR", style, size)

    def heading(text, size=14):
        pdf.ln(6)
        font("B", size)
        pdf.cell(CONTENT_W, 10, text, new_x="LMARGIN", new_y="NEXT")
        pdf.set_draw_color(60, 60, 60)
        pdf.line(MARGIN, pdf.get_y(), PAGE_W - MARGIN, pdf.get_y())
        pdf.ln(4)

    def subheading(text, size=11):
        pdf.ln(4)
        font("B", size)
        pdf.cell(CONTENT_W, 8, text, new_x="LMARGIN", new_y="NEXT")
        pdf.ln(2)

    def body(text):
        font("", 9)
        pdf.multi_cell(CONTENT_W, 5.5, text)

    def bullet(text):
        font("", 9)
        pdf.set_x(MARGIN)
        pdf.cell(8, 5.5, "  \u2022")
        pdf.multi_cell(CONTENT_W - 10, 5.5, text)
        pdf.set_x(MARGIN)

    def separator():
        pdf.ln(3)
        pdf.set_draw_color(200, 200, 200)
        pdf.line(MARGIN, pdf.get_y(), PAGE_W - MARGIN, pdf.get_y())
        pdf.ln(3)

    # Read DB info
    db_info = {}
    db_info_path = os.path.join(metrics_dir, "_db_info.json")
    if os.path.isfile(db_info_path):
        with open(db_info_path) as f:
            db_info = json.load(f)

    # ================================================================
    # 1. Title Page
    # ================================================================
    pdf.add_page()
    pdf.ln(40)
    font("B", 24)
    title = meta.get("report_title", "OCI DB Metric Report")
    pdf.cell(CONTENT_W, 14, title, new_x="LMARGIN", new_y="NEXT", align="C")
    pdf.ln(4)

    # DB System name subtitle
    db_name = db_info.get("display_name", "")
    if db_name:
        font("", 14)
        pdf.set_text_color(80, 80, 80)
        pdf.cell(CONTENT_W, 10, db_name, new_x="LMARGIN", new_y="NEXT", align="C")
        pdf.set_text_color(0, 0, 0)

    separator()
    pdf.ln(6)

    font("", 11)
    ns = meta.get("namespace", "")
    start_kst = utc_to_kst(meta.get("start_time", ""))
    end_kst = utc_to_kst(meta.get("end_time", ""))
    collected = meta.get("collected_at", "")
    interval_val = meta.get("interval", "1m")

    # Build info table with DB system details
    db_type = db_info.get("db_type", "MySQL" if "mysql" in ns.lower() else "PostgreSQL")
    shape = db_info.get("shape", "")
    ocpu = db_info.get("ocpu_count", "")
    mem_gb = db_info.get("memory_gb", "")
    db_ver = db_info.get("db_version", "")
    ha = "Yes" if db_info.get("ha_enabled") else "No"
    storage = db_info.get("storage_gb", "")

    info = [
        ("DB System", db_name or "-"),
        ("DB Type", f"{db_type} {db_ver}" if db_ver else db_type),
        ("Shape", f"{shape} ({ocpu} OCPU / {mem_gb} GB)" if shape else "-"),
        ("Storage", f"{storage} GB" if storage else "-"),
        ("HA", ha),
        ("Monitoring", f"{start_kst}  ~  {end_kst}"),
        ("Interval", interval_val),
        ("Generated", collected),
    ]
    for label, val in info:
        if val and val != "-":
            font("B", 10)
            pdf.cell(40, 8, label, align="R")
            font("", 10)
            pdf.cell(5, 8, ":")
            pdf.cell(CONTENT_W - 45, 8, f"  {val}", new_x="LMARGIN", new_y="NEXT")

    # ================================================================
    # 1.5 Environment - Configuration Parameters
    # ================================================================
    params = db_info.get("parameters", {})
    if params:
        pdf.add_page()
        heading("Environment - Key Configuration Parameters")

        cfg_name = db_info.get("configuration_name", "")
        if cfg_name:
            font("", 9)
            pdf.cell(CONTENT_W, 7, f"Configuration: {cfg_name}", new_x="LMARGIN", new_y="NEXT")
            pdf.ln(4)

        # Render parameters as table
        param_rows = [["Parameter", "Value"]]
        for k in sorted(params.keys()):
            v = params[k]
            # Format large numbers
            try:
                num = int(v)
                if num >= 1073741824:
                    v = f"{v} ({num / 1073741824:.1f} GB)"
                elif num >= 1048576:
                    v = f"{v} ({num / 1048576:.0f} MB)"
                elif num >= 1024:
                    v = f"{v} ({num / 1024:.0f} KB)"
            except (ValueError, TypeError):
                pass
            param_rows.append([k, v])

        # Render as table
        col_w = [CONTENT_W * 0.45, CONTENT_W * 0.55]
        for row_idx, cells in enumerate(param_rows):
            if row_idx == 0:
                font("B", 9)
                pdf.set_fill_color(45, 55, 72)
                pdf.set_text_color(255, 255, 255)
                for i, c in enumerate(cells):
                    pdf.cell(col_w[i], 7, c, border=1, fill=True, align="C")
                pdf.ln()
                pdf.set_text_color(0, 0, 0)
            else:
                font("", 8.5)
                fill = row_idx % 2 == 0
                if fill:
                    pdf.set_fill_color(245, 247, 250)
                for i, c in enumerate(cells):
                    pdf.cell(col_w[i], 6.5, c, border=1, fill=fill, align="L")
                pdf.ln()

    # ================================================================
    # 2. Charts (portrait, split tall images across pages)
    # ================================================================
    for label, img_path in charts:
        try:
            from PIL import Image as PILImage
            img = PILImage.open(img_path)
            img_w_px, img_h_px = img.size

            # Scale to content width
            scale = CONTENT_W / img_w_px
            img_h_mm = img_h_px * scale
            page_avail = 297 - MARGIN * 2 - 25  # A4 height minus margins and heading

            if img_h_mm <= page_avail:
                # Fits on one page
                pdf.add_page()
                heading(f"Chart: {label}", 13)
                pdf.image(img_path, x=MARGIN, w=CONTENT_W)
            else:
                # Split image into page-sized chunks
                chunk_h_px = int(page_avail / scale)
                page_num = 1
                y_offset = 0
                while y_offset < img_h_px:
                    pdf.add_page()
                    remaining = img_h_px - y_offset
                    this_chunk_h = min(chunk_h_px, remaining)

                    if page_num == 1:
                        heading(f"Chart: {label}", 13)
                    else:
                        heading(f"Chart: {label} (cont.)", 13)

                    # Crop this chunk
                    crop_box = (0, y_offset, img_w_px, y_offset + this_chunk_h)
                    chunk = img.crop(crop_box)
                    chunk_path = img_path.replace(".png", f"_chunk{page_num}.png")
                    chunk.save(chunk_path)
                    pdf.image(chunk_path, x=MARGIN, w=CONTENT_W)
                    os.remove(chunk_path)

                    y_offset += this_chunk_h
                    page_num += 1
        except Exception as e:
            pdf.add_page()
            heading(f"Chart: {label}", 13)
            font("", 9)
            pdf.cell(CONTENT_W, 8, f"(Image error: {e})", new_x="LMARGIN", new_y="NEXT")

    # ================================================================
    # 3. Statistics Summary
    # ================================================================
    if len(stats_lines) > 1:
        pdf.add_page()
        heading("Statistics Summary")

        headers = stats_lines[0].split(",")
        n_cols = len(headers)
        # Proportional widths: first col wider for metric name
        first_w = 52
        rest_w = (CONTENT_W - first_w) / max(n_cols - 1, 1)
        col_w = [first_w] + [rest_w] * (n_cols - 1)

        # Header row
        font("B", 8)
        pdf.set_fill_color(45, 55, 72)
        pdf.set_text_color(255, 255, 255)
        for i, h in enumerate(headers):
            w = col_w[i] if i < len(col_w) else rest_w
            pdf.cell(w, 7, h, border=1, fill=True, align="C")
        pdf.ln()
        pdf.set_text_color(0, 0, 0)

        # Data rows (alternating bg)
        font("", 7.5)
        for row_idx, row_line in enumerate(stats_lines[1:]):
            cols = row_line.split(",")
            if row_idx % 2 == 1:
                pdf.set_fill_color(245, 247, 250)
                fill = True
            else:
                fill = False
            for i, val in enumerate(cols):
                w = col_w[i] if i < len(col_w) else rest_w
                pdf.cell(w, 6, val, border=1, fill=fill, align="C" if i > 0 else "L")
            pdf.ln()

    # ================================================================
    # 4. Bottleneck Analysis & Recommendations
    # ================================================================
    if analysis:
        pdf.add_page()
        heading("Bottleneck Analysis & Recommendations")

        # Track if we're inside a table
        in_table = False
        table_rows = []

        for line in analysis.split("\n"):
            stripped = line.strip()

            # Flush table if line is not a table row
            if not stripped.startswith("|") and table_rows:
                _render_table(pdf, table_rows, CONTENT_W, MARGIN)
                table_rows = []
                in_table = False

            if stripped.startswith("### "):
                subheading(stripped.replace("### ", ""))
            elif stripped.startswith("## "):
                heading(stripped.replace("## ", ""), 13)
            elif stripped.startswith("|"):
                if "---" in stripped:
                    continue  # skip separator row
                table_rows.append(stripped)
            elif stripped.startswith("- "):
                text = stripped[2:].replace("**", "")
                bullet(text)
            elif stripped.startswith("---"):
                separator()
            elif stripped:
                text = stripped.replace("**", "")
                body(text)
                pdf.ln(1)
            else:
                pdf.ln(3)

        # Flush remaining table
        if table_rows:
            _render_table(pdf, table_rows, CONTENT_W, MARGIN)

    # ================================================================
    # MySQL HA note (at bottom)
    # ================================================================
    if "mysql" in ns.lower():
        pdf.ln(10)
        font("I", 8)
        pdf.set_text_color(80, 80, 80)
        pdf.multi_cell(CONTENT_W, 5,
            "[참고] MySQL HA Standby는 DB System 내부 Active-Standby로, "
            "메트릭이 노드별 분리되지 않고 DB System 단위(resourceName)로 수집됩니다. "
            "Read Replica는 별도 리소스로 독립적인 resourceName으로 메트릭이 별도 수집됩니다.")
        pdf.set_text_color(0, 0, 0)

    # ================================================================
    # Footer
    # ================================================================
    pdf.ln(15)
    separator()
    font("I", 9)
    pdf.set_text_color(120, 120, 120)
    pdf.cell(CONTENT_W, 7, "Generated by oci-db-metric-report", new_x="LMARGIN", new_y="NEXT", align="C")
    pdf.cell(CONTENT_W, 7, "https://github.com/jaesucjang/oci-db-metric-report", new_x="LMARGIN", new_y="NEXT", align="C")
    pdf.set_text_color(0, 0, 0)

    pdf.output(pdf_path)
    print(f"PDF saved: {pdf_path}")


def _render_table(pdf, rows, content_w, margin):
    """Render markdown table rows as a formatted PDF table."""
    if not rows:
        return

    parsed = []
    for r in rows:
        cells = [c.strip().replace("**", "") for c in r.split("|")[1:-1]]
        parsed.append(cells)

    if not parsed:
        return

    n_cols = max(len(r) for r in parsed)
    if n_cols == 0:
        return

    # Column widths: first col wider
    first_w = min(content_w * 0.35, 60)
    rest_w = (content_w - first_w) / max(n_cols - 1, 1)
    col_w = [first_w] + [rest_w] * (n_cols - 1)

    for row_idx, cells in enumerate(parsed):
        if row_idx == 0:
            # Header
            pdf.set_font("NotoKR", "B", 8)
            pdf.set_fill_color(240, 240, 240)
            for i, c in enumerate(cells):
                w = col_w[i] if i < len(col_w) else rest_w
                pdf.cell(w, 6.5, c, border=1, fill=True, align="C")
            pdf.ln()
        else:
            # Data
            pdf.set_font("NotoKR", "", 8)
            for i, c in enumerate(cells):
                w = col_w[i] if i < len(col_w) else rest_w
                pdf.cell(w, 6, c, border=1, align="C" if i > 0 else "L")
            pdf.ln()

    pdf.ln(2)


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python3 generate_pdf.py <metrics_dir>")
        sys.exit(1)
    generate_pdf(sys.argv[1])

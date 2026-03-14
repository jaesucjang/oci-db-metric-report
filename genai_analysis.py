#!/usr/bin/env python3
"""
OCI GenAI-powered metric analysis using OpenAI-compatible API.
Reads stats + rule-based analysis, calls LLM for deeper insights.
"""

import json
import os

CONFIG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "genai_config.json")


def load_genai_config():
    """Load GenAI config from file."""
    if not os.path.isfile(CONFIG_PATH):
        return None
    with open(CONFIG_PATH) as f:
        cfg = json.load(f)
    if not cfg.get("enabled") or not cfg.get("api_key"):
        return None
    return cfg


def save_genai_config(cfg):
    """Save GenAI config to file."""
    with open(CONFIG_PATH, "w") as f:
        json.dump(cfg, f, indent=2)


def generate_ai_analysis(metrics_dir, namespace=""):
    """Call OCI GenAI to analyze metrics. Returns AI analysis text or None."""
    cfg = load_genai_config()
    if not cfg:
        return None

    # Read stats summary
    stats_text = ""
    stats_path = os.path.join(metrics_dir, "stats_summary.csv")
    if os.path.isfile(stats_path):
        with open(stats_path) as f:
            stats_text = f.read()

    # Read rule-based analysis
    analysis_text = ""
    analysis_path = os.path.join(metrics_dir, "analysis.md")
    if os.path.isfile(analysis_path):
        with open(analysis_path) as f:
            analysis_text = f.read()

    # Read metadata
    meta = {}
    meta_path = os.path.join(metrics_dir, "_metadata.json")
    if os.path.isfile(meta_path):
        with open(meta_path) as f:
            meta = json.load(f)

    if not stats_text and not analysis_text:
        return None

    ns = namespace or meta.get("namespace", "")
    db_type = "MySQL" if "mysql" in ns.lower() else "PostgreSQL"

    prompt = f"""You are an OCI Database performance expert. Analyze the following {db_type} monitoring metrics and provide actionable insights in Korean.

## DB Info
- Type: {db_type}
- Namespace: {ns}
- Period: {meta.get('start_time', '')} ~ {meta.get('end_time', '')}

## Statistics Summary (CSV)
{stats_text}

## Rule-based Analysis
{analysis_text}

Based on the above data, provide:
1. Key performance patterns and correlations between metrics
2. Potential root causes for any anomalies
3. Specific tuning recommendations for OCI {db_type}
4. Capacity planning suggestions

Write in Korean. Be concise and specific. Use markdown formatting.
Do NOT repeat the rule-based analysis above. Focus on NEW insights only."""

    try:
        from openai import OpenAI
    except ImportError:
        import subprocess, sys
        subprocess.check_call([sys.executable, "-m", "pip", "install", "openai", "--quiet"])
        from openai import OpenAI

    try:
        client = OpenAI(
            api_key=cfg["api_key"],
            base_url=cfg["base_url"],
        )
        response = client.chat.completions.create(
            model=cfg["model"],
            messages=[
                {"role": "system", "content": "You are an expert Oracle Cloud Infrastructure database performance analyst."},
                {"role": "user", "content": prompt},
            ],
            temperature=0.3,
            max_tokens=2000,
        )
        return response.choices[0].message.content
    except Exception as e:
        return f"[GenAI Error] {e}"


if __name__ == "__main__":
    import sys
    if len(sys.argv) < 2:
        print("Usage: python3 genai_analysis.py <metrics_dir>")
        sys.exit(1)
    result = generate_ai_analysis(sys.argv[1])
    if result:
        print(result)
    else:
        print("No analysis generated (config missing or no data)")

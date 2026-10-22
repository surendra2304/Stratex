#!/usr/bin/env python3
"""
scripts/analyze_advisory_log.py — Advisory Log Quality Analyzer CLI & Report Generator.

Analyzes advisory_log.jsonl to verify:
1. Total consultations (lifetime, last 24h, last 7d).
2. Verdict distribution (SHADOW_LOG_ONLY vs APPLY vs REJECT).
3. Grouped rejection reasons for REJECT verdicts.
4. Parameter change distribution for SHADOW_LOG_ONLY (strategy, parameter, avg delta %).
5. AI confidence distribution histogram.
6. Consultation latency percentiles (p50, p95, p99).
7. Flagged "Contested Advisories" (REJECT with AI Confidence > 0.70).

Usage:
  python scripts/analyze_advisory_log.py [--log advisory_log.jsonl] [--output advisory_quality_report.json]
"""

import argparse
import datetime
import json
import math
import os
from typing import Any


def load_advisory_log(filepath: str) -> list[dict[str, Any]]:
    """Loads and parses all records from the advisory JSONL log."""
    if not os.path.exists(filepath):
        return []
    records = []
    with open(filepath, "r", encoding="utf-8") as f:
        for line in f:
            line_str = line.strip()
            if not line_str:
                continue
            try:
                records.append(json.loads(line_str))
            except Exception:
                continue
    return records


def calculate_percentile(data: list[float], percentile: float) -> float:
    """Calculates percentile for a list of floats."""
    if not data:
        return 0.0
    sorted_data = sorted(data)
    k = (len(sorted_data) - 1) * (percentile / 100.0)
    f = math.floor(k)
    c = math.ceil(k)
    if f == c:
        return round(sorted_data[int(k)], 2)
    d0 = sorted_data[int(f)] * (c - k)
    d1 = sorted_data[int(c)] * (k - f)
    return round(d0 + d1, 2)


def generate_advisory_quality_report(
    records: list[dict[str, Any]],
    now_dt: datetime.datetime | None = None
) -> dict[str, Any]:
    """Generates the structured statistical quality report from parsed records."""
    now = now_dt or datetime.datetime.utcnow()
    one_day_ago = now - datetime.timedelta(days=1)
    seven_days_ago = now - datetime.timedelta(days=7)

    total_count = len(records)
    count_24h = 0
    count_7d = 0

    verdict_dist: dict[str, int] = {"SHADOW_LOG_ONLY": 0, "APPLY": 0, "REJECT": 0}
    rejection_reasons: dict[str, int] = {}
    param_changes_summary: dict[str, dict[str, Any]] = {}
    confidences: list[float] = []
    latencies: list[float] = []
    contested_advisories: list[dict[str, Any]] = []

    confidence_buckets: dict[str, int] = {
        "0.00-0.20": 0,
        "0.21-0.40": 0,
        "0.41-0.60": 0,
        "0.61-0.80": 0,
        "0.81-1.00": 0
    }

    for rec in records:
        ts_str = rec.get("timestamp", "")
        rec_dt = None
        if ts_str:
            try:
                rec_dt = datetime.datetime.fromisoformat(ts_str.replace("Z", "+00:00")).replace(tzinfo=None)
            except Exception:
                rec_dt = None

        if rec_dt:
            if rec_dt >= one_day_ago:
                count_24h += 1
            if rec_dt >= seven_days_ago:
                count_7d += 1

        verdict = str(rec.get("verdict", "UNKNOWN")).upper()
        verdict_dist[verdict] = verdict_dist.get(verdict, 0) + 1

        conf = float(rec.get("confidence", 0.0))
        confidences.append(conf)

        if conf <= 0.20:
            confidence_buckets["0.00-0.20"] += 1
        elif conf <= 0.40:
            confidence_buckets["0.21-0.40"] += 1
        elif conf <= 0.60:
            confidence_buckets["0.41-0.60"] += 1
        elif conf <= 0.80:
            confidence_buckets["0.61-0.80"] += 1
        else:
            confidence_buckets["0.81-1.00"] += 1

        lat = float(rec.get("latency_ms", 0.0))
        if lat > 0:
            latencies.append(lat)

        # Rejection analysis
        if verdict == "REJECT":
            for r in rec.get("rejected_changes", []):
                reason = r.get("reason", "Unknown rejection reason")
                # Normalize reason category
                if "FORBIDDEN_PARAMS" in reason:
                    cat = "Forbidden Risk/Credential Parameter"
                elif "exceeds maximum allowed" in reason:
                    cat = "Parameter Delta Bound Violation (>20%)"
                elif "outside allowed bounds" in reason:
                    cat = "Position Sizing Multiplier Violation"
                elif "Leverage increase rejected" in reason:
                    cat = "Leverage Increase Invariant Violation"
                elif "Cooldown" in reason:
                    cat = "Cooldown Invariant Violation (<4h)"
                elif "exceeding maximum limit" in reason:
                    cat = "Max Changes Per Decision Cap (>2)"
                else:
                    cat = reason[:50]
                rejection_reasons[cat] = rejection_reasons.get(cat, 0) + 1

            # Check for Contested Advisory (REJECT + Confidence > 0.70)
            if conf > 0.70:
                contested_advisories.append({
                    "decision_id": rec.get("decision_id"),
                    "timestamp": rec.get("timestamp"),
                    "confidence": conf,
                    "reason": rec.get("consultation_reason"),
                    "rejected_changes": rec.get("rejected_changes", []),
                    "debate_summary": rec.get("ai_debate_summary", "")
                })

        # Parameter changes analysis (from applied/shadowed changes)
        changes_to_analyze = rec.get("applied_changes", []) if (verdict in ["SHADOW_LOG_ONLY", "APPLY"]) else []
        for ch in changes_to_analyze:
            strat = ch.get("strategy", "global")
            param = ch.get("parameter", "unknown")
            key = f"{strat}.{param}"
            curr_v = float(ch.get("current_value", 0) or 0)
            new_v = float(ch.get("new_value", 0) or 0)
            pct = abs(new_v - curr_v) / curr_v * 100.0 if curr_v != 0 else 0.0

            if key not in param_changes_summary:
                param_changes_summary[key] = {
                    "strategy": strat,
                    "parameter": param,
                    "count": 0,
                    "deltas_pct": []
                }
            param_changes_summary[key]["count"] += 1
            param_changes_summary[key]["deltas_pct"].append(pct)

    # Format parameter changes summary
    formatted_param_changes = {}
    for k, v in param_changes_summary.items():
        deltas = v["deltas_pct"]
        avg_delta = sum(deltas) / len(deltas) if deltas else 0.0
        formatted_param_changes[k] = {
            "strategy": v["strategy"],
            "parameter": v["parameter"],
            "proposal_count": v["count"],
            "avg_change_pct": round(avg_delta, 2),
            "max_change_pct": round(max(deltas), 2) if deltas else 0.0
        }

    report = {
        "generated_at": now.isoformat() + "Z",
        "consultations_summary": {
            "total_lifetime": total_count,
            "last_24h": count_24h,
            "last_7d": count_7d
        },
        "verdict_distribution": verdict_dist,
        "rejection_reasons_breakdown": rejection_reasons,
        "parameter_changes_distribution": formatted_param_changes,
        "confidence_distribution_histogram": confidence_buckets,
        "latency_percentiles_ms": {
            "p50": calculate_percentile(latencies, 50),
            "p95": calculate_percentile(latencies, 95),
            "p99": calculate_percentile(latencies, 99),
            "sample_count": len(latencies)
        },
        "contested_advisories_count": len(contested_advisories),
        "contested_advisories": contested_advisories[:10]  # Limit to 10 in report
    }
    return report


def print_human_readable_report(report: dict[str, Any]) -> None:
    """Prints a clean, institutional ASCII summary of the advisory quality report."""
    print("=" * 80)
    print(" [AI-UNIVERSE] ADVISORY LOG QUALITY & SAFETY AUDIT REPORT")
    print(f" Generated: {report['generated_at']}")
    print("=" * 80)

    summary = report["consultations_summary"]
    print("\n[1] CONSULTATIONS SUMMARY")
    print(f"  - Total Lifetime Consultations: {summary['total_lifetime']}")
    print(f"  - Last 24 Hours:               {summary['last_24h']}")
    print(f"  - Last 7 Days:                 {summary['last_7d']}")

    print("\n[2] VERDICT DISTRIBUTION")
    for k, v in report["verdict_distribution"].items():
        pct = (v / max(1, summary["total_lifetime"])) * 100.0
        print(f"  - {k:<18}: {v:>4} ({pct:>5.1f}%)")

    print("\n[3] REJECTION REASONS BREAKDOWN")
    rejections = report["rejection_reasons_breakdown"]
    if not rejections:
        print("  - No rejected proposals found in log.")
    else:
        for r_reason, r_count in rejections.items():
            print(f"  - {r_reason:<45}: {r_count:>3}")

    print("\n[4] PARAMETER MODIFICATIONS (SHADOW / LIVE)")
    params = report["parameter_changes_distribution"]
    if not params:
        print("  - No parameter modifications proposed.")
    else:
        for p_key, p_info in params.items():
            print(f"  - {p_key:<35} | Count: {p_info['proposal_count']:>2} | Avg Delta: {p_info['avg_change_pct']:>5.2f}% | Max Delta: {p_info['max_change_pct']:>5.2f}%")

    print("\n[5] AI CONFIDENCE HISTOGRAM")
    for b_range, b_cnt in report["confidence_distribution_histogram"].items():
        bar = "#" * int(b_cnt * 20 / max(1, summary["total_lifetime"]))
        print(f"  - [{b_range}]: {b_cnt:>4} {bar}")

    lat = report["latency_percentiles_ms"]
    print("\n[6] CONSULTATION LATENCY")
    print(f"  - p50: {lat['p50']} ms | p95: {lat['p95']} ms | p99: {lat['p99']} ms (samples: {lat['sample_count']})")

    contested = report["contested_advisories"]
    print(f"\n[7] CONTESTED ADVISORIES (REJECT with Confidence > 0.70): {report['contested_advisories_count']}")
    for c in contested[:3]:
        print(f"  - [{c['timestamp'][:19]}] Decision: {c['decision_id']} (Conf: {c['confidence']:.2f}) - {c['reason']}")
    print("=" * 80)


def main():
    parser = argparse.ArgumentParser(description="Analyze AI Advisory Log Quality & Safety Integrity.")
    parser.add_argument("--log", default="advisory_log.jsonl", help="Path to advisory_log.jsonl")
    parser.add_argument("--output", default="advisory_quality_report.json", help="Path to output JSON report")
    args = parser.parse_args()

    records = load_advisory_log(args.log)
    report = generate_advisory_quality_report(records)

    # Print summary
    print_human_readable_report(report)

    # Save JSON report
    with open(args.output, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2)
    print(f"\n[SAVED] Full JSON report written to: {args.output}\n")


if __name__ == "__main__":
    main()

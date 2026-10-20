"""
tools/tickets.py - Deterministic pandas data tools for support ticket evidence.
Zero LLM calls. Fully JSON-serializable outputs. Supports lifecycle replay analysis.
"""
from __future__ import annotations
import re
from pathlib import Path
from typing import Any
import pandas as pd

CSV_PATH = Path("data/ps03_support_tickets.csv")
REPLAY_CUTOFF = "2026-05-01"

_CACHED_DF: pd.DataFrame | None = None


def _load_raw_df() -> pd.DataFrame:
    """Load and enrich dataframe once with cluster_id, parsed fix_id, and summary product."""
    global _CACHED_DF
    if _CACHED_DF is not None:
        return _CACHED_DF.copy()

    if not CSV_PATH.exists():
        raise FileNotFoundError(f"Ticket dataset not found at {CSV_PATH.resolve()}")

    df = pd.read_csv(CSV_PATH)
    df["category"] = df["category"].astype(str).str.strip()
    df["subcategory"] = df["subcategory"].astype(str).str.strip()
    df["cluster_id"] = df["category"] + "|" + df["subcategory"]
    df["is_repetitive"] = df["is_repetitive"].fillna(False).astype(bool)
    df["sla_met"] = df["sla_met"].fillna(False).astype(bool)
    df["resolution_time_hrs"] = pd.to_numeric(df["resolution_time_hrs"], errors="coerce").fillna(0.0)

    # Parse fix ID integer from resolution_notes (e.g. 'standard fix #11' -> 11)
    def parse_fix(note: Any) -> int | None:
        m = re.search(r"fix\s*#?(\d+)", str(note), re.IGNORECASE)
        return int(m.group(1)) if m else None

    df["fix_id"] = df["resolution_notes"].apply(parse_fix)

    # Extract product name mentioned in the summary template
    def parse_summary_product(summary: Any) -> str | None:
        m = re.search(r"while using ([A-Za-z0-9_]+)\.?", str(summary))
        return m.group(1) if m else None

    df["summary_product"] = df["summary"].apply(parse_summary_product)
    _CACHED_DF = df
    return _CACHED_DF.copy()


def load_tickets(window: str = "all", as_df: bool = False) -> list[dict] | pd.DataFrame | dict:
    """
    Load support tickets filtered by temporal window: 'baseline' (< 2026-05-01),
    'replay' (>= 2026-05-01), or 'all'. Returns JSON-serializable records or DataFrame.
    """
    valid_windows = {"baseline", "replay", "all"}
    if window not in valid_windows:
        return {"error": f"Invalid window '{window}'. Must be one of: {sorted(list(valid_windows))}"}

    df = _load_raw_df()
    if window == "baseline":
        filtered = df[df["created_date"] < REPLAY_CUTOFF]
    elif window == "replay":
        filtered = df[df["created_date"] >= REPLAY_CUTOFF]
    else:
        filtered = df

    if as_df:
        return filtered.copy()

    records = filtered.to_dict(orient="records")
    for r in records:
        for k, v in r.items():
            if pd.isna(v):
                r[k] = None
    return records


def data_quality_report() -> dict:
    """
    Audit data quality issues across the 300 support tickets without mutating raw data.
    Reports summary vs product column mismatches, same-day >24hr anomalies, and SLA quirks.
    """
    df = _load_raw_df()
    total = len(df)

    # 1. Product in summary vs product column
    mismatches = df[df["summary_product"].notna() & (df["summary_product"] != df["product"])]
    mismatch_count = len(mismatches)
    sample_mismatches = [
        {
            "ticket_id": str(row["ticket_id"]),
            "summary_product": row["summary_product"],
            "product_column": row["product"],
            "summary": row["summary"],
        }
        for _, row in mismatches.head(5).iterrows()
    ]

    # 2. Same-day resolutions where resolution_time_hrs > 24
    same_day = df[df["created_date"] == df["resolved_date"]]
    same_day_gt_24 = same_day[same_day["resolution_time_hrs"] > 24.0]
    sample_same_day = [
        {
            "ticket_id": str(row["ticket_id"]),
            "created_date": str(row["created_date"]),
            "resolved_date": str(row["resolved_date"]),
            "resolution_time_hrs": float(row["resolution_time_hrs"]),
        }
        for _, row in same_day_gt_24.head(5).iterrows()
    ]

    # 3. SLA quirks
    sla_met_high_hrs = int((df["sla_met"] & (df["resolution_time_hrs"] > 48.0)).sum())
    sla_failed_low_hrs = int((~df["sla_met"] & (df["resolution_time_hrs"] < 24.0)).sum())

    # 4. Repetitive ticket statistics
    repetitive_count = int(df["is_repetitive"].sum())

    return {
        "total_tickets": total,
        "summary_product_mismatch": {
            "count": mismatch_count,
            "rate": round(mismatch_count / total, 3) if total else 0.0,
            "description": "Product mentioned in summary differs from product column in 242/300 rows.",
            "samples": sample_mismatches,
        },
        "same_day_resolution_gt_24h": {
            "same_day_total": len(same_day),
            "gt_24h_count": len(same_day_gt_24),
            "rate": round(len(same_day_gt_24) / len(same_day), 3) if len(same_day) else 0.0,
            "description": "32 of 44 same-day resolutions (created_date == resolved_date) show resolution_time_hrs > 24.",
            "samples": sample_same_day,
        },
        "sla_tracking_inconsistency": {
            "sla_met_over_48h": sla_met_high_hrs,
            "sla_failed_under_24h": sla_failed_low_hrs,
            "description": "sla_met boolean does not consistently correlate with resolution_time_hrs.",
        },
        "repetitive_summary": {
            "repetitive_count": repetitive_count,
            "repetitive_rate": round(repetitive_count / total, 3) if total else 0.0,
        },
        "distinct_fix_ids": sorted([int(x) for x in df["fix_id"].dropna().unique()]),
    }


def cluster_stats(window: str = "all") -> dict:
    """
    Compute cluster metrics per 'category|subcategory' cluster for the given window.
    Returns ticket_count, repetitive_rate, avg_resolution_hrs, distinct_fix_ids,
    top_fix_id, top_fix_share, severity_mix, and monthly_counts.
    """
    valid_windows = {"baseline", "replay", "all"}
    if window not in valid_windows:
        return {"error": f"Invalid window '{window}'. Must be one of: {sorted(list(valid_windows))}"}

    all_df = _load_raw_df()
    if window == "baseline":
        df = all_df[all_df["created_date"] < REPLAY_CUTOFF]
    elif window == "replay":
        df = all_df[all_df["created_date"] >= REPLAY_CUTOFF]
    else:
        df = all_df

    clusters_dict: dict[str, dict] = {}
    grouped = df.groupby("cluster_id")

    # Ensure all 24 known clusters exist in output (with 0 count if empty in this window)
    known_clusters = sorted(all_df["cluster_id"].unique())

    for cid in known_clusters:
        if cid not in grouped.groups:
            cat, sub = cid.split("|", 1)
            clusters_dict[cid] = {
                "cluster_id": cid,
                "category": cat,
                "subcategory": sub,
                "ticket_count": 0,
                "repetitive_count": 0,
                "repetitive_rate": 0.0,
                "avg_resolution_hrs": 0.0,
                "distinct_fix_ids": [],
                "distinct_fix_count": 0,
                "top_fix_id": None,
                "top_fix_share": 0.0,
                "severity_mix": {},
                "monthly_counts": {},
            }
            continue

        cdf = grouped.get_group(cid)
        cat = cdf["category"].iloc[0]
        sub = cdf["subcategory"].iloc[0]
        t_count = len(cdf)
        rep_count = int(cdf["is_repetitive"].sum())
        rep_rate = round(rep_count / t_count, 3) if t_count else 0.0
        avg_hrs = round(float(cdf["resolution_time_hrs"].mean()), 1) if t_count else 0.0

        fixes = [int(f) for f in cdf["fix_id"].dropna()]
        fix_series = pd.Series(fixes, dtype="int64") if fixes else pd.Series(dtype="int64")
        distinct_fixes = sorted(list(set(fixes)))

        if not fix_series.empty:
            top_fix = int(fix_series.mode()[0])
            top_fix_share = round(float((fix_series == top_fix).mean()), 3)
        else:
            top_fix = None
            top_fix_share = 0.0

        sev_mix = {str(k): int(v) for k, v in cdf["severity"].value_counts().to_dict().items()}

        # Monthly counts e.g. '2026-03': 4
        months = cdf["created_date"].str.slice(0, 7)
        m_counts = {str(k): int(v) for k, v in months.value_counts().sort_index().to_dict().items()}

        clusters_dict[cid] = {
            "cluster_id": cid,
            "category": cat,
            "subcategory": sub,
            "ticket_count": t_count,
            "repetitive_count": rep_count,
            "repetitive_rate": rep_rate,
            "avg_resolution_hrs": avg_hrs,
            "distinct_fix_ids": distinct_fixes,
            "distinct_fix_count": len(distinct_fixes),
            "top_fix_id": top_fix,
            "top_fix_share": top_fix_share,
            "severity_mix": sev_mix,
            "monthly_counts": m_counts,
        }

    return {
        "window": window,
        "total_tickets": len(df),
        "total_clusters": len(clusters_dict),
        "clusters": clusters_dict,
    }


def fix_usage_map() -> dict:
    """
    Map each standard fix ID (#1-20) across all support tickets,
    showing total usage count and which categories and clusters it appears in.
    """
    df = _load_raw_df()
    fix_map: dict[str, dict] = {}

    for fix_num in range(1, 21):
        fdf = df[df["fix_id"] == fix_num]
        total_uses = len(fdf)
        cat_counts = {str(k): int(v) for k, v in fdf["category"].value_counts().to_dict().items()}
        clusters = sorted([str(c) for c in fdf["cluster_id"].unique()])

        fix_map[str(fix_num)] = {
            "fix_id": fix_num,
            "total_occurrences": total_uses,
            "category_count": len(cat_counts),
            "categories": cat_counts,
            "clusters": clusters,
        }

    return {
        "total_fixes": 20,
        "fixes": fix_map,
    }


def get_cluster_tickets(cluster_id: str, window: str = "all", limit: int = 50) -> dict:
    """
    Retrieve individual support ticket rows for a specific cluster_id ('<category>|<subcategory>'),
    optionally filtered by window ('baseline', 'replay', 'all') up to limit.
    """
    all_df = _load_raw_df()
    known_clusters = set(all_df["cluster_id"].unique())

    if cluster_id not in known_clusters:
        return {
            "error": f"Unknown cluster_id '{cluster_id}'. Must be one of the 24 known clusters in '<category>|<subcategory>' format."
        }

    valid_windows = {"baseline", "replay", "all"}
    if window not in valid_windows:
        return {"error": f"Invalid window '{window}'. Must be one of: {sorted(list(valid_windows))}"}

    if window == "baseline":
        df = all_df[all_df["created_date"] < REPLAY_CUTOFF]
    elif window == "replay":
        df = all_df[all_df["created_date"] >= REPLAY_CUTOFF]
    else:
        df = all_df

    cdf = df[df["cluster_id"] == cluster_id]
    total_found = len(cdf)
    sliced = cdf.head(limit)

    tickets: list[dict] = []
    for _, row in sliced.iterrows():
        tickets.append({
            "ticket_id": str(row["ticket_id"]),
            "created_date": str(row["created_date"]),
            "resolved_date": str(row["resolved_date"]),
            "category": str(row["category"]),
            "subcategory": str(row["subcategory"]),
            "product": str(row["product"]),
            "severity": str(row["severity"]),
            "summary": str(row["summary"]),
            "resolution_notes": str(row["resolution_notes"]),
            "fix_id": int(row["fix_id"]) if pd.notna(row["fix_id"]) else None,
            "resolution_time_hrs": float(row["resolution_time_hrs"]),
            "sla_met": bool(row["sla_met"]),
            "is_repetitive": bool(row["is_repetitive"]),
        })

    return {
        "cluster_id": cluster_id,
        "window": window,
        "total_found": total_found,
        "returned_count": len(tickets),
        "tickets": tickets,
    }


def compute_cluster_signals(cluster_id: str) -> dict:
    """
    Compare baseline (< 2026-05-01) vs replay (>= 2026-05-01) metrics for a cluster.
    Computes volume change, repetitive rate shift, resolution time delta, and newly observed fix IDs.
    """
    all_df = _load_raw_df()
    if cluster_id not in set(all_df["cluster_id"].unique()):
        return {
            "error": f"Unknown cluster_id '{cluster_id}'. Must be one of the 24 known clusters in '<category>|<subcategory>' format."
        }

    base_stats = cluster_stats(window="baseline")["clusters"].get(cluster_id, {})
    replay_stats = cluster_stats(window="replay")["clusters"].get(cluster_id, {})

    b_vol = base_stats.get("ticket_count", 0)
    r_vol = replay_stats.get("ticket_count", 0)
    vol_change = r_vol - b_vol
    vol_change_pct = round((vol_change / b_vol) * 100.0, 1) if b_vol > 0 else None

    b_rep = base_stats.get("repetitive_rate", 0.0)
    r_rep = replay_stats.get("repetitive_rate", 0.0)
    rep_change = round(r_rep - b_rep, 3)

    b_hrs = base_stats.get("avg_resolution_hrs", 0.0)
    r_hrs = replay_stats.get("avg_resolution_hrs", 0.0)
    hrs_change = round(r_hrs - b_hrs, 1)

    b_fixes = set(base_stats.get("distinct_fix_ids", []))
    r_fixes = set(replay_stats.get("distinct_fix_ids", []))
    new_fixes = sorted(list(r_fixes - b_fixes))
    retired_fixes = sorted(list(b_fixes - r_fixes))

    b_top = base_stats.get("top_fix_id")
    r_top = replay_stats.get("top_fix_id")
    top_shifted = (b_top != r_top) if (b_top is not None and r_top is not None) else False

    # Lifecycle Signal heuristic
    if r_vol == 0:
        signal = "RETIRE"
        reason = "Zero tickets observed in replay window; issue appears resolved or obsolete."
    elif new_fixes or top_shifted or abs(rep_change) >= 0.2:
        signal = "UPDATE"
        reasons = []
        if new_fixes:
            reasons.append(f"new fix IDs observed: {new_fixes}")
        if top_shifted:
            reasons.append(f"dominant fix shifted from #{b_top} to #{r_top}")
        if abs(rep_change) >= 0.2:
            reasons.append(f"repetitive rate changed by {rep_change:+.1%}")
        reason = "Drift detected: " + "; ".join(reasons)
    else:
        signal = "KEEP"
        reason = "Stable pattern: ticket volume and resolution fixes remain consistent with baseline."

    return {
        "cluster_id": cluster_id,
        "baseline_volume": b_vol,
        "replay_volume": r_vol,
        "volume_change": vol_change,
        "volume_change_pct": vol_change_pct,
        "baseline_repetitive_rate": b_rep,
        "replay_repetitive_rate": r_rep,
        "repetitive_rate_change": rep_change,
        "baseline_avg_resolution_hrs": b_hrs,
        "replay_avg_resolution_hrs": r_hrs,
        "avg_resolution_change_hrs": hrs_change,
        "baseline_fix_ids": sorted(list(b_fixes)),
        "replay_fix_ids": sorted(list(r_fixes)),
        "new_fix_ids_in_replay": new_fixes,
        "retired_fix_ids_in_replay": retired_fixes,
        "baseline_top_fix_id": b_top,
        "replay_top_fix_id": r_top,
        "top_fix_shifted": top_shifted,
        "lifecycle_signal": signal,
        "signal_reason": reason,
    }

"""
Data processing helpers — aggregation, formatting, trend computation.
No framework dependencies.

Optimizations:
- build_kpi_trend uses pre-parsed _parsed_date from sheets.read_kpi_history()
- notes_for uses pre-parsed _parsed_ts from sheets.read_notes()
- compute_all_progress builds progress dict in one pass (avoids N+1)
"""

import pandas as pd


def kpi_achievement(row) -> float:
    target = float(row.get("target_value", 0))
    current = float(row.get("current_value", 0))
    baseline = float(row.get("baseline_value", 0))
    direction = str(row.get("direction", "increase")).lower()

    if direction == "decrease":
        span = baseline - target
        if span == 0:
            return 0.0
        progress = baseline - current
        return round((progress / span) * 100, 1)
    else:
        span = target - baseline
        if span == 0:
            return 0.0
        progress = current - baseline
        return round((progress / span) * 100, 1)


def okr_progress_from_krs(okr_id: str, kpis_df: pd.DataFrame) -> float:
    if kpis_df.empty:
        return 0.0
    krs = kpis_df[kpis_df["okr_id"] == str(okr_id)]
    if krs.empty:
        return 0.0
    achievements = krs.apply(kpi_achievement, axis=1)
    return round(achievements.mean(), 1)


def compute_all_progress(okr_ids: list[str], kpis_df: pd.DataFrame) -> dict[str, float]:
    """Compute progress for all OKRs in one pass — avoids N+1 pattern."""
    result = {}
    if kpis_df.empty:
        return {oid: 0.0 for oid in okr_ids}
    # Pre-compute achievements for all KPIs once
    kpis_df = kpis_df.copy()
    kpis_df["_achievement"] = kpis_df.apply(kpi_achievement, axis=1)
    for okr_id in okr_ids:
        krs = kpis_df[kpis_df["okr_id"] == str(okr_id)]
        if krs.empty:
            result[okr_id] = 0.0
        else:
            result[okr_id] = round(krs["_achievement"].mean(), 1)
    return result


def krs_for_okr(okr_id: str, kpis_df: pd.DataFrame) -> pd.DataFrame:
    if kpis_df.empty:
        return kpis_df
    return kpis_df[kpis_df["okr_id"] == str(okr_id)]


def okr_summary_stats_from_progress(progress_map: dict[str, float]) -> dict:
    """Build stats from pre-computed progress dict."""
    if not progress_map:
        return {"total": 0, "avg_progress": 0, "completed": 0, "at_risk": 0}
    values = list(progress_map.values())
    total = len(values)
    return {
        "total": total,
        "avg_progress": int(round(sum(values) / total)),
        "completed": sum(1 for v in values if v >= 100),
        "at_risk": sum(1 for v in values if v < 25),
    }


def build_kpi_trend(history_df: pd.DataFrame, kpi_id: str) -> list[dict]:
    """Build trend data using pre-parsed _parsed_date column."""
    if history_df.empty:
        return []
    subset = history_df[history_df["kpi_id"] == str(kpi_id)]
    if subset.empty:
        return []
    # Use pre-parsed date if available, otherwise parse
    if "_parsed_date" in subset.columns:
        sorted_df = subset.dropna(subset=["_parsed_date"]).sort_values("_parsed_date")
        return [{"date": r["_parsed_date"].strftime("%Y-%m-%d"), "value": r["value"]}
                for _, r in sorted_df.iterrows()]
    else:
        subset = subset.copy()
        subset["_d"] = pd.to_datetime(subset["date"], format="mixed", dayfirst=False, errors="coerce")
        sorted_df = subset.dropna(subset=["_d"]).sort_values("_d")
        return [{"date": r["_d"].strftime("%Y-%m-%d"), "value": r["value"]}
                for _, r in sorted_df.iterrows()]


def notes_for(notes_df: pd.DataFrame, parent_type: str, parent_id: str) -> list[dict]:
    """Return notes as list of dicts, sorted by timestamp descending.
    Uses pre-parsed _parsed_ts column for fast sorting."""
    if notes_df.empty:
        return []
    mask = (notes_df["parent_type"] == parent_type) & (
        notes_df["parent_id"] == str(parent_id)
    )
    subset = notes_df[mask]
    if subset.empty:
        return []
    if "_parsed_ts" in subset.columns:
        sorted_df = subset.sort_values("_parsed_ts", ascending=False)
    else:
        sorted_df = subset
    return [
        {"author": r.get("author", ""), "timestamp": r.get("timestamp", ""), "text": r.get("text", "")}
        for _, r in sorted_df.iterrows()
    ]


PREFIX_UNITS = {"$", "£", "€", "¥", "₹", "₩", "R$", "CHF"}

CATEGORY_COLORS = {
    "Corporate": "#6366f1",
    "Growth": "#22c55e",
    "Operations": "#f59e0b",
    "Development": "#3b82f6",
    "Finance": "#ef4444",
}


def category_color(category: str) -> str:
    return CATEGORY_COLORS.get(category, "#94a3b8")


def format_value(value, unit: str) -> str:
    unit = str(unit).strip()
    try:
        value = int(round(float(value)))
    except (ValueError, TypeError):
        value = 0
    if unit in PREFIX_UNITS:
        return f"{unit}{value}"
    return f"{value} {unit}".strip()


def progress_color(pct: float) -> str:
    if pct >= 75:
        return "#22c55e"
    if pct >= 40:
        return "#f59e0b"
    return "#ef4444"

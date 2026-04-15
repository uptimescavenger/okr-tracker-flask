"""
Data processing helpers — aggregation, formatting, trend computation.
No framework dependencies.
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


def krs_for_okr(okr_id: str, kpis_df: pd.DataFrame) -> pd.DataFrame:
    if kpis_df.empty:
        return kpis_df
    return kpis_df[kpis_df["okr_id"] == str(okr_id)]


def okr_summary_stats(okrs_df: pd.DataFrame, kpis_df: pd.DataFrame) -> dict:
    if okrs_df.empty:
        return {"total": 0, "avg_progress": 0, "completed": 0, "at_risk": 0}
    progresses = okrs_df["id"].apply(lambda oid: okr_progress_from_krs(oid, kpis_df))
    return {
        "total": len(okrs_df),
        "avg_progress": round(progresses.mean(), 1),
        "completed": int((progresses >= 100).sum()),
        "at_risk": int((progresses < 25).sum()),
    }


def build_kpi_trend(history_df: pd.DataFrame, kpi_id: str) -> list[dict]:
    if history_df.empty:
        return []
    subset = history_df[history_df["kpi_id"] == str(kpi_id)].copy()
    if subset.empty:
        return []
    subset["date"] = pd.to_datetime(subset["date"], format="mixed", dayfirst=False, errors="coerce")
    subset = subset.dropna(subset=["date"]).sort_values("date")
    return [{"date": r["date"].strftime("%Y-%m-%d"), "value": r["value"]}
            for _, r in subset.iterrows()]


def notes_for(notes_df: pd.DataFrame, parent_type: str, parent_id: str) -> pd.DataFrame:
    if notes_df.empty:
        return notes_df
    mask = (notes_df["parent_type"] == parent_type) & (
        notes_df["parent_id"] == str(parent_id)
    )
    subset = notes_df[mask].copy()
    subset["timestamp_sort"] = pd.to_datetime(
        subset["timestamp"], format="mixed", dayfirst=False, errors="coerce"
    )
    return subset.sort_values("timestamp_sort", ascending=False).drop(columns=["timestamp_sort"])


PREFIX_UNITS = {"$", "£", "€", "¥", "₹", "₩", "R$", "CHF"}


def format_value(value, unit: str) -> str:
    unit = str(unit).strip()
    if unit in PREFIX_UNITS:
        return f"{unit}{value}"
    return f"{value} {unit}".strip()


CATEGORY_COLORS = {
    "Corporate": "#6366f1",
    "Growth": "#22c55e",
    "Operations": "#f59e0b",
    "Development": "#3b82f6",
    "Finance": "#ef4444",
}


def category_color(category: str) -> str:
    return CATEGORY_COLORS.get(category, "#94a3b8")


def progress_color(pct: float) -> str:
    if pct >= 75:
        return "#22c55e"
    if pct >= 40:
        return "#f59e0b"
    return "#ef4444"

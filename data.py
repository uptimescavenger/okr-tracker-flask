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


def _ts_sort_key(s, parsed=None):
    """Best-effort timestamp parse for sorting activity entries."""
    from datetime import datetime
    if parsed is not None and pd.notna(parsed):
        return parsed
    s = str(s).strip()
    for fmt in ("%m/%d/%Y %H:%M", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M",
                "%m/%d/%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(s, fmt)
        except (ValueError, TypeError):
            continue
    return datetime.min


def recent_activity(
    notes_df: pd.DataFrame,
    history_df: pd.DataFrame,
    okrs_df: pd.DataFrame,
    kpis_df: pd.DataFrame,
    limit: int = 60,
) -> list[dict]:
    """Build a unified, newest-first feed of who-did-what across notes and KR value updates.

    Inputs are expected to already be filtered to what the current user is allowed to view
    (the caller in app.py applies role-based category filtering before passing them in).

    KR value-update rows in KPI History have no author; we join on (kr_id, timestamp) to
    the Notes sheet to recover the author when the user left a companion note. The Flask
    update flow writes both with the same timestamp.
    """
    okr_lookup = {}
    if not okrs_df.empty:
        for _, r in okrs_df.iterrows():
            okr_lookup[str(r["id"])] = {
                "title": str(r.get("title", "") or ""),
                "category": str(r.get("category", "") or ""),
            }
    kr_lookup = {}
    if not kpis_df.empty:
        for _, r in kpis_df.iterrows():
            kr_lookup[str(r["id"])] = {
                "name": str(r.get("name", "") or ""),
                "unit": str(r.get("unit", "") or ""),
                "okr_id": str(r.get("okr_id", "") or ""),
            }

    # Author lookup for KR updates: (kr_id, timestamp) -> author
    note_authors: dict[tuple[str, str], str] = {}
    if not notes_df.empty:
        for _, n in notes_df.iterrows():
            if str(n.get("parent_type", "")) == "KR":
                note_authors[(str(n["parent_id"]), str(n["timestamp"]))] = str(
                    n.get("author", "") or ""
                )

    activities: list[dict] = []

    if not notes_df.empty:
        for _, n in notes_df.iterrows():
            ptype = str(n.get("parent_type", ""))
            pid = str(n.get("parent_id", ""))
            if ptype == "KR":
                kr = kr_lookup.get(pid)
                if kr is None:
                    # Note refers to a KR not visible to this user — skip.
                    continue
                target_name = kr["name"] or f"KR {pid}"
                target_label = "Key Result"
            elif ptype == "OKR":
                okr = okr_lookup.get(pid)
                if okr is None:
                    continue
                target_name = okr["title"] or f"OKR {pid}"
                target_label = "Objective"
            else:
                continue
            ts = str(n.get("timestamp", ""))
            parsed = n.get("_parsed_ts") if "_parsed_ts" in notes_df.columns else None
            activities.append({
                "timestamp": ts,
                "_sort": _ts_sort_key(ts, parsed),
                "author": str(n.get("author", "") or "") or "—",
                "verb": "noted on",
                "target_label": target_label,
                "target_name": target_name,
                "detail": str(n.get("text", "") or ""),
            })

    if not history_df.empty:
        has_author_col = "author" in history_df.columns
        for _, h in history_df.iterrows():
            kr_id = str(h.get("kpi_id", ""))
            kr = kr_lookup.get(kr_id)
            if kr is None:
                continue
            ts = str(h.get("date", ""))
            parsed = h.get("_parsed_date") if "_parsed_date" in history_df.columns else None
            # Prefer the history row's own author; fall back to a companion note
            # at the same timestamp; finally em-dash for legacy rows with neither.
            author = ""
            if has_author_col:
                author = str(h.get("author", "") or "").strip()
            if not author:
                author = note_authors.get((kr_id, ts), "")
            if not author:
                author = "—"
            activities.append({
                "timestamp": ts,
                "_sort": _ts_sort_key(ts, parsed),
                "author": author or "—",
                "verb": "updated",
                "target_label": "Key Result",
                "target_name": kr["name"] or f"KR {kr_id}",
                "detail": f"value → {format_value(h.get('value', 0), kr['unit'])}",
            })

    activities.sort(key=lambda a: a["_sort"], reverse=True)
    for a in activities:
        a.pop("_sort", None)
    return activities[:limit]


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

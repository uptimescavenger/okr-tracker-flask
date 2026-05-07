"""
Data processing helpers — aggregation, formatting, trend computation.
No framework dependencies.

Optimizations (v2):
- Vectorized KR achievement computation (numpy/pandas, no .apply)
- Pre-grouping helpers eliminate N*M filter scans in tracker() loop
- iterrows() replaced with .to_dict('records') where possible (~5x faster)
- recent_activity() built in a single pass over notes/history (was 2 passes)
- _ts_sort_key prefers pre-parsed timestamps; format-guessing is the rare fallback
"""

import numpy as np
import pandas as pd


# ---------- Achievement / progress (vectorized) ----------

def kpi_achievement(row) -> float:
    """Per-row achievement — kept for any single-row callers (e.g. inline edits)."""
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


def _compute_achievements_vec(kpis_df: pd.DataFrame) -> pd.Series:
    """Vectorized achievement for an entire KPIs DataFrame.

    Returns a Series of achievement % aligned with kpis_df.index.
    Handles direction (increase/decrease) and division-by-zero in one pass.
    """
    if kpis_df.empty:
        return pd.Series([], dtype=float)
    target = pd.to_numeric(kpis_df["target_value"], errors="coerce").fillna(0)
    current = pd.to_numeric(kpis_df["current_value"], errors="coerce").fillna(0)
    baseline = pd.to_numeric(kpis_df["baseline_value"], errors="coerce").fillna(0)
    direction = kpis_df["direction"].astype(str).str.lower()

    # increase: span = target - baseline, progress = current - baseline
    inc_span = target - baseline
    inc_progress = current - baseline
    # decrease: span = baseline - target, progress = baseline - current
    dec_span = baseline - target
    dec_progress = baseline - current

    is_decrease = (direction == "decrease")
    span = np.where(is_decrease, dec_span, inc_span)
    progress = np.where(is_decrease, dec_progress, inc_progress)

    # Safe divide with zero-span → 0
    with np.errstate(divide="ignore", invalid="ignore"):
        pct = np.where(span == 0, 0.0, (progress / span) * 100.0)
    return pd.Series(np.round(pct, 1), index=kpis_df.index)


def okr_progress_from_krs(okr_id: str, kpis_df: pd.DataFrame) -> float:
    if kpis_df.empty:
        return 0.0
    krs = kpis_df[kpis_df["okr_id"].astype(str) == str(okr_id)]
    if krs.empty:
        return 0.0
    achievements = _compute_achievements_vec(krs)
    return round(achievements.mean(), 1)


def compute_all_progress(okr_ids: list[str], kpis_df: pd.DataFrame) -> dict[str, float]:
    """Compute progress for all OKRs in one vectorized pass."""
    if kpis_df.empty or not okr_ids:
        return {oid: 0.0 for oid in okr_ids}
    df = kpis_df.copy()
    df["_achievement"] = _compute_achievements_vec(df)
    df["_okr_id"] = df["okr_id"].astype(str)
    means = df.groupby("_okr_id")["_achievement"].mean().round(1).to_dict()
    return {oid: float(means.get(str(oid), 0.0)) for oid in okr_ids}


# ---------- Pre-grouping helpers (called once per request, used in nested loops) ----------

def group_kpis_by_okr(kpis_df: pd.DataFrame) -> dict[str, list[dict]]:
    """Return {okr_id: [kr_dict, kr_dict, ...]} for O(1) lookup in render loop."""
    if kpis_df.empty:
        return {}
    result: dict[str, list[dict]] = {}
    for r in kpis_df.to_dict("records"):
        oid = str(r.get("okr_id", ""))
        result.setdefault(oid, []).append(r)
    return result


def group_notes_by_parent(notes_df: pd.DataFrame) -> dict[tuple[str, str], list[dict]]:
    """Return {(parent_type, parent_id): [note_dict, ...]} sorted newest first."""
    if notes_df.empty:
        return {}
    # Sort once globally by parsed timestamp desc; per-group order is preserved
    if "_parsed_ts" in notes_df.columns:
        sorted_df = notes_df.sort_values("_parsed_ts", ascending=False)
    else:
        sorted_df = notes_df
    result: dict[tuple[str, str], list[dict]] = {}
    for r in sorted_df.to_dict("records"):
        key = (str(r.get("parent_type", "")), str(r.get("parent_id", "")))
        result.setdefault(key, []).append({
            "author": r.get("author", "") or "",
            "timestamp": r.get("timestamp", "") or "",
            "text": r.get("text", "") or "",
        })
    return result


def group_history_by_kpi(history_df: pd.DataFrame) -> dict[str, list[dict]]:
    """Return {kpi_id: [{date, value}, ...]} sorted oldest→newest for trend rendering."""
    if history_df.empty:
        return {}
    if "_parsed_date" in history_df.columns:
        valid = history_df.dropna(subset=["_parsed_date"]).sort_values("_parsed_date")
    else:
        # Fallback: parse on the fly
        tmp = history_df.copy()
        tmp["_d"] = pd.to_datetime(tmp["date"], format="mixed", dayfirst=False, errors="coerce")
        valid = tmp.dropna(subset=["_d"]).sort_values("_d")
    result: dict[str, list[dict]] = {}
    for r in valid.to_dict("records"):
        kpi_id = str(r.get("kpi_id", ""))
        # Use whichever parsed-date column exists
        d = r.get("_parsed_date") if "_parsed_date" in valid.columns else r.get("_d")
        if d is None or pd.isna(d):
            continue
        result.setdefault(kpi_id, []).append({
            "date": d.strftime("%Y-%m-%d"),
            "value": r.get("value"),
        })
    return result


# ---------- Backward-compat single-OKR / single-KR accessors ----------

def krs_for_okr(okr_id: str, kpis_df: pd.DataFrame) -> pd.DataFrame:
    if kpis_df.empty:
        return kpis_df
    return kpis_df[kpis_df["okr_id"].astype(str) == str(okr_id)]


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
    subset = history_df[history_df["kpi_id"].astype(str) == str(kpi_id)]
    if subset.empty:
        return []
    if "_parsed_date" in subset.columns:
        sorted_df = subset.dropna(subset=["_parsed_date"]).sort_values("_parsed_date")
        return [{"date": r["_parsed_date"].strftime("%Y-%m-%d"), "value": r["value"]}
                for r in sorted_df.to_dict("records")]
    else:
        subset = subset.copy()
        subset["_d"] = pd.to_datetime(subset["date"], format="mixed", dayfirst=False, errors="coerce")
        sorted_df = subset.dropna(subset=["_d"]).sort_values("_d")
        return [{"date": r["_d"].strftime("%Y-%m-%d"), "value": r["value"]}
                for r in sorted_df.to_dict("records")]


def notes_for(notes_df: pd.DataFrame, parent_type: str, parent_id: str) -> list[dict]:
    """Return notes as list of dicts, sorted by timestamp descending.
    Uses pre-parsed _parsed_ts column for fast sorting."""
    if notes_df.empty:
        return []
    mask = (notes_df["parent_type"] == parent_type) & (
        notes_df["parent_id"].astype(str) == str(parent_id)
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
        for r in sorted_df.to_dict("records")
    ]


# ---------- Activity feed ----------

def _ts_sort_key(s, parsed=None):
    """Best-effort timestamp parse for sorting activity entries.
    Prefers the pre-parsed value; format-guessing is the rare fallback."""
    if parsed is not None and pd.notna(parsed):
        return parsed
    from datetime import datetime
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
    """Build a unified, newest-first feed of notes + KR value updates.

    Single pass over notes_df builds both note_authors lookup AND note activities
    (was previously two iterations).
    """
    # Lookups — use to_dict('records') (faster than iterrows)
    okr_lookup = {}
    if not okrs_df.empty:
        for r in okrs_df.to_dict("records"):
            okr_lookup[str(r["id"])] = {
                "title": str(r.get("title", "") or ""),
                "category": str(r.get("category", "") or ""),
            }
    kr_lookup = {}
    if not kpis_df.empty:
        for r in kpis_df.to_dict("records"):
            kr_lookup[str(r["id"])] = {
                "name": str(r.get("name", "") or ""),
                "unit": str(r.get("unit", "") or ""),
                "okr_id": str(r.get("okr_id", "") or ""),
            }

    activities: list[dict] = []
    note_authors: dict[tuple[str, str], str] = {}

    # Single pass over notes: build BOTH the (kr_id, ts) -> author lookup
    # AND the note activity entries.
    if not notes_df.empty:
        has_parsed_ts = "_parsed_ts" in notes_df.columns
        for n in notes_df.to_dict("records"):
            ptype = str(n.get("parent_type", ""))
            pid = str(n.get("parent_id", ""))
            ts = str(n.get("timestamp", ""))
            author_raw = str(n.get("author", "") or "")
            if ptype == "KR":
                # populate author lookup for matching KR-update rows
                note_authors[(pid, ts)] = author_raw
                kr = kr_lookup.get(pid)
                if kr is None:
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
            parsed = n.get("_parsed_ts") if has_parsed_ts else None
            activities.append({
                "timestamp": ts,
                "_sort": _ts_sort_key(ts, parsed),
                "author": author_raw or "—",
                "verb": "noted on",
                "target_label": target_label,
                "target_name": target_name,
                "detail": str(n.get("text", "") or ""),
            })

    # KR value updates from history
    if not history_df.empty:
        has_author_col = "author" in history_df.columns
        has_parsed_date = "_parsed_date" in history_df.columns
        for h in history_df.to_dict("records"):
            kr_id = str(h.get("kpi_id", ""))
            kr = kr_lookup.get(kr_id)
            if kr is None:
                continue
            ts = str(h.get("date", ""))
            parsed = h.get("_parsed_date") if has_parsed_date else None
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


# ---------- Display helpers ----------

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
    formatted = f"{value:,}"
    if unit in PREFIX_UNITS:
        return f"{unit}{formatted}"
    return f"{formatted} {unit}".strip()


def progress_color(pct: float) -> str:
    if pct >= 75:
        return "#22c55e"
    if pct >= 40:
        return "#f59e0b"
    return "#ef4444"

"""Assembles the tracker page's view model.

Kept out of app.py so the shaping of objectives, key results, notes, history
and the activity feed can be exercised without a Flask request context — and
so the route stays a route.
"""

import config
import data


def visible_okrs(okrs_df, category: str, role: str, user_categories: list[str]):
    """Restrict objectives to what this user may see.

    A named category filters to exactly that category. "All" means "everything
    I'm allowed to see": Managers get Corporate plus their own (and
    uncategorised) objectives, Team Members only their own categories.
    """
    if okrs_df.empty:
        return okrs_df
    if category != "All":
        return okrs_df[okrs_df["category"] == category]
    if role == "Manager":
        allowed = ["Corporate"] + list(user_categories)
        return okrs_df[okrs_df["category"].isin(allowed) | (okrs_df["category"] == "")]
    if role == "Team Member":
        return okrs_df[okrs_df["category"].isin(list(user_categories))]
    return okrs_df


def _build_kr(kr_row, okr_id, trend, notes, elapsed):
    achievement = float(kr_row.get("_achievement", 0) or 0)
    return {
        "id": str(kr_row["id"]),
        "okr_id": okr_id,
        "name": kr_row.get("name", ""),
        "owner": kr_row.get("owner", ""),
        "current_value": int(round(float(kr_row.get("current_value", 0) or 0))),
        "target_value": int(round(float(kr_row.get("target_value", 0) or 0))),
        "baseline_value": int(round(float(kr_row.get("baseline_value", 0) or 0))),
        "direction": kr_row.get("direction", "increase"),
        "unit": kr_row.get("unit", ""),
        "last_updated": kr_row.get("last_updated", ""),
        "description": kr_row.get("description", ""),
        "achievement": int(round(achievement)),
        "color": data.progress_color(achievement),
        "health": data.health_for(achievement, elapsed),
        "current_display": data.format_value(kr_row.get("current_value", 0), kr_row.get("unit", "")),
        "target_display": data.format_value(kr_row.get("target_value", 0), kr_row.get("unit", "")),
        "notes": notes,
        "has_trend": len(trend) > 1,
    }


def _build_history(krs, trend_by_kpi, limit=40):
    """Flat, newest-first update log for an objective's History tab.

    Only the tail of each key result's trend can survive the limit, so slice
    before formatting rather than formatting hundreds of points and discarding
    most of them.
    """
    rows = []
    for kr_row in krs:
        unit = kr_row.get("unit", "")
        points = trend_by_kpi.get(str(kr_row["id"]), [])
        # One extra point so the oldest surviving row still has a delta.
        tail = points[-(limit + 1):]
        prev = None
        for i, pt in enumerate(tail):
            try:
                val = float(pt.get("value") or 0)
            except (TypeError, ValueError):
                val = 0.0
            # The extra leading point exists only to seed `prev`.
            if not (i == 0 and len(tail) > limit):
                rows.append({
                    "kr_name": kr_row.get("name", ""),
                    "date": pt.get("date", ""),
                    "value": data.format_value(val, unit),
                    "delta": None if prev is None else int(round(val - prev)),
                })
            prev = val
    rows.sort(key=lambda h: h["date"], reverse=True)
    return rows[:limit]


def build_tracker_view(okrs_df, kpis_df, history_df, notes_df, quarter):
    """Everything tracker.html needs, given already-filtered frames."""
    pace = config.quarter_pace(quarter)
    elapsed = pace["elapsed"]

    okr_ids = list(okrs_df["id"].astype(str))
    progress_map = data.progress_from_achievements(okr_ids, kpis_df)
    stats = data.okr_summary_stats_from_progress(progress_map)

    krs_by_okr = data.group_kpis_by_okr(kpis_df)
    notes_by_parent = data.group_notes_by_parent(notes_df)
    trend_by_kpi = data.group_history_by_kpi(history_df)

    okr_list, chart_data = [], []
    for okr_row in okrs_df.to_dict("records"):
        okr_id = str(okr_row["id"])
        pct = progress_map.get(okr_id, 0.0)
        krs = krs_by_okr.get(okr_id, [])

        kr_list = []
        for kr_row in krs:
            kr_id = str(kr_row["id"])
            trend = trend_by_kpi.get(kr_id, [])
            if len(trend) > 1:
                chart_data.append({"id": kr_id, "trend": trend})
            kr_list.append(_build_kr(
                kr_row, okr_id, trend,
                notes_by_parent.get(("KR", kr_id), []), elapsed,
            ))

        okr_list.append({
            "id": okr_id,
            "title": okr_row.get("title", ""),
            "description": okr_row.get("description", ""),
            "owner": okr_row.get("owner", ""),
            "target_date": okr_row.get("target_date", ""),
            "category": okr_row.get("category", ""),
            "last_updated": okr_row.get("last_updated", ""),
            "progress": int(round(pct)),
            "color": data.progress_color(pct),
            "health": data.health_for(pct, elapsed),
            "cat_color": data.category_color(okr_row.get("category", "")),
            "krs": kr_list,
            "notes": notes_by_parent.get(("OKR", okr_id), []),
            "history": _build_history(krs, trend_by_kpi),
        })

    return {"okrs": okr_list, "chart_data": chart_data, "stats": stats, "pace": pace}

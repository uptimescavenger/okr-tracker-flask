"""
OKR Tracker — Flask application (deployed on Render).

Optimizations:
- compute_all_progress eliminates N+1 pattern on OKR progress
- notes_for returns list[dict] directly (no DataFrame conversion)
- Minimal chart_data payload (only id + trend, not full OKR tree)
- flask-compress for gzip responses
"""

import uuid
from datetime import datetime
from functools import wraps

from flask import (
    Flask, render_template, request, redirect, url_for,
    session, flash, jsonify, make_response,
)

import config
import sheets
import data
import auth_service as auth
import email_service

app = Flask(__name__)
app.secret_key = config.SECRET_KEY
app.config["SESSION_COOKIE_HTTPONLY"] = True
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"

# Enable gzip compression if flask-compress is available
try:
    from flask_compress import Compress
    Compress(app)
except ImportError:
    pass


# ---------- Startup ----------

with app.app_context():
    if config.SPREADSHEET_ID != "YOUR_SPREADSHEET_ID_HERE":
        try:
            auth.seed_admin()
        except Exception as e:
            print(f"Warning: Could not seed admin: {e}")


# ---------- Auth decorator ----------

def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not auth.is_logged_in():
            token = request.cookies.get("okr_remember")
            if token and auth.auto_login_from_cookie(token):
                return f(*args, **kwargs)
            return redirect(url_for("login_page"))
        return f(*args, **kwargs)
    return decorated


def admin_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not auth.is_admin():
            flash("Admin access required.", "error")
            return redirect(url_for("tracker"))
        return f(*args, **kwargs)
    return decorated


# ---------- Context processor ----------

@app.context_processor
def inject_globals():
    return {
        "auth": auth,
        "config": config,
        "current_quarter": config.current_quarter(),
        "quarter_list": config.quarter_list(),
        "now": datetime.now,
    }


# ---------- Login / Logout ----------

@app.route("/login", methods=["GET", "POST"])
def login_page():
    if auth.is_logged_in():
        return redirect(url_for("tracker"))

    if request.method == "GET":
        token = request.cookies.get("okr_remember")
        if token and auth.auto_login_from_cookie(token):
            return redirect(url_for("tracker"))

    if request.method == "POST":
        email = request.form.get("email", "").strip()
        password = request.form.get("password", "")
        remember = request.form.get("remember") == "on"

        if email and password:
            user = auth.login(email, password)
            if user:
                session["_current_user"] = user
                resp = make_response(redirect(url_for("tracker")))
                if remember:
                    token = auth.make_remember_token(email)
                    resp.set_cookie(
                        "okr_remember", token,
                        max_age=30 * 24 * 3600,
                        httponly=True, samesite="Lax",
                    )
                return resp
            else:
                flash("Invalid email or password.", "error")
        else:
            flash("Please enter both email and password.", "error")

    return render_template("login.html")


@app.route("/logout")
def logout():
    session.pop("_current_user", None)
    resp = make_response(redirect(url_for("login_page")))
    resp.delete_cookie("okr_remember")
    return resp


# ---------- Main tracker ----------

@app.route("/")
@login_required
def tracker():
    quarter = request.args.get("quarter", config.current_quarter())
    category = request.args.get("category", "All")
    allowed = auth.allowed_filter_options()

    if category not in allowed:
        category = allowed[0] if allowed else "All"

    okrs_df = sheets.read_okrs(quarter)
    kpis_df = sheets.read_kpis(quarter)
    history_df = sheets.read_kpi_history(quarter)
    notes_df = sheets.read_notes()

    # Category filtering
    if category != "All":
        okrs_df = okrs_df[okrs_df["category"] == category]
    else:
        role = auth.user_role()
        if role == "Manager":
            user_cats = auth.user_categories()
            visible = ["Corporate"] + user_cats
            okrs_df = okrs_df[okrs_df["category"].isin(visible) | (okrs_df["category"] == "")]
        elif role == "Team Member":
            user_cats = auth.user_categories()
            okrs_df = okrs_df[okrs_df["category"].isin(user_cats)]

    # Filter KPIs to visible OKRs
    visible_okr_ids = list(okrs_df["id"].astype(str))
    visible_okr_id_set = set(visible_okr_ids)
    kpis_df = kpis_df[kpis_df["okr_id"].astype(str).isin(visible_okr_id_set)]
    visible_kpi_ids = set(kpis_df["id"].astype(str))
    if not history_df.empty:
        history_df = history_df[history_df["kpi_id"].astype(str).isin(visible_kpi_ids)]

    # Compute all OKR progress in one pass (avoids N+1)
    progress_map = data.compute_all_progress(visible_okr_ids, kpis_df)
    stats = data.okr_summary_stats_from_progress(progress_map)

    # Build OKR data for template + minimal chart payload
    okr_list = []
    chart_data = []  # Minimal: only kr_id + trend arrays
    has_any_trend = False

    for _, okr_row in okrs_df.iterrows():
        okr_id = str(okr_row["id"])
        pct = progress_map.get(okr_id, 0.0)
        color = data.progress_color(pct)
        krs = data.krs_for_okr(okr_id, kpis_df)

        kr_list = []
        kr_charts = []
        for _, kr_row in krs.iterrows():
            kr_id = str(kr_row["id"])
            achievement = data.kpi_achievement(kr_row)
            kr_notes = data.notes_for(notes_df, "KR", kr_id)
            trend = data.build_kpi_trend(history_df, kr_id)
            if len(trend) > 1:
                has_any_trend = True
                kr_charts.append({"id": kr_id, "trend": trend})
            kr_list.append({
                "id": kr_id,
                "okr_id": okr_id,
                "name": kr_row.get("name", ""),
                "owner": kr_row.get("owner", ""),
                "current_value": int(round(float(kr_row.get("current_value", 0) or 0))),
                "target_value": int(round(float(kr_row.get("target_value", 0) or 0))),
                "baseline_value": int(round(float(kr_row.get("baseline_value", 0) or 0))),
                "direction": kr_row.get("direction", "increase"),
                "unit": kr_row.get("unit", ""),
                "last_updated": kr_row.get("last_updated", ""),
                "achievement": int(round(achievement)),
                "color": data.progress_color(achievement),
                "current_display": data.format_value(kr_row.get("current_value", 0), kr_row.get("unit", "")),
                "target_display": data.format_value(kr_row.get("target_value", 0), kr_row.get("unit", "")),
                "notes": kr_notes,
                "has_trend": len(trend) > 1,
            })

        okr_notes = data.notes_for(notes_df, "OKR", okr_id)
        okr_list.append({
            "id": okr_id,
            "title": okr_row.get("title", ""),
            "description": okr_row.get("description", ""),
            "owner": okr_row.get("owner", ""),
            "target_date": okr_row.get("target_date", ""),
            "category": okr_row.get("category", ""),
            "last_updated": okr_row.get("last_updated", ""),
            "progress": int(round(pct)),
            "color": color,
            "cat_color": data.category_color(okr_row.get("category", "")),
            "krs": kr_list,
            "notes": okr_notes,
        })
        if kr_charts:
            chart_data.extend(kr_charts)

    return render_template(
        "tracker.html",
        quarter=quarter,
        category=category,
        allowed_categories=allowed,
        stats=stats,
        okrs=okr_list,
        chart_data=chart_data,
        has_any_trend=has_any_trend,
        categories=config.OKR_CATEGORIES,
        creatable_categories=auth.creatable_categories(),
    )


# ---------- API routes for AJAX actions ----------

@app.route("/api/refresh")
@login_required
def api_refresh():
    sheets.clear_cache()
    return jsonify({"ok": True})


@app.route("/api/okr/add", methods=["POST"])
@login_required
def api_add_okr():
    if not auth.can_create_okr():
        return jsonify({"ok": False, "error": "Permission denied"}), 403
    d = request.json
    cat = d.get("category", "")
    if not auth.can_create_okr_in_category(cat):
        return jsonify({"ok": False, "error": f"You don't have permission to create OKRs in {cat or 'this'} category"}), 403
    quarter = d.get("quarter", config.current_quarter())
    okr_id = str(uuid.uuid4())[:8]
    now = datetime.now().strftime("%m/%d/%Y %H:%M")
    row = [
        okr_id, d.get("title", ""), d.get("description", ""),
        d.get("owner", ""), d.get("target_date", ""),
        0, now, d.get("category", ""),
    ]
    sheets.add_okr(quarter, row)
    return jsonify({"ok": True, "id": okr_id})


@app.route("/api/okr/edit", methods=["POST"])
@login_required
def api_edit_okr():
    if not auth.can_edit_okr():
        return jsonify({"ok": False, "error": "Permission denied"}), 403
    d = request.json
    quarter = d.get("quarter", config.current_quarter())
    okr_id = d.get("id")
    now = datetime.now().strftime("%m/%d/%Y %H:%M")
    fields = {}
    for f in ("title", "description", "owner", "target_date", "category"):
        if f in d:
            fields[f] = d[f]
    fields["last_updated"] = now
    sheets.update_okr_fields(quarter, okr_id, fields)
    return jsonify({"ok": True})


@app.route("/api/okr/delete", methods=["POST"])
@login_required
def api_delete_okr():
    d = request.json
    cat = d.get("category", "")
    if not auth.can_delete_okr(cat):
        return jsonify({"ok": False, "error": "Permission denied"}), 403
    sheets.delete_okr(d.get("quarter", config.current_quarter()), d.get("id"))
    return jsonify({"ok": True})


@app.route("/api/okr/move", methods=["POST"])
@login_required
def api_move_okr():
    if not auth.can_edit_okr():
        return jsonify({"ok": False, "error": "Permission denied"}), 403
    d = request.json
    sheets.move_okr(d.get("old_quarter"), d.get("new_quarter"), d.get("id"))
    return jsonify({"ok": True})


@app.route("/api/kr/add", methods=["POST"])
@login_required
def api_add_kr():
    if not auth.can_create_kr():
        return jsonify({"ok": False, "error": "Permission denied"}), 403
    d = request.json
    quarter = d.get("quarter", config.current_quarter())
    kr_id = str(uuid.uuid4())[:8]
    now = datetime.now().strftime("%m/%d/%Y %H:%M")
    row = [
        kr_id, d.get("okr_id", ""), d.get("name", ""),
        d.get("owner", ""), 0, d.get("target_value", 0),
        d.get("baseline_value", 0), d.get("direction", "increase"),
        d.get("unit", ""), now,
    ]
    sheets.add_kpi(quarter, row)
    return jsonify({"ok": True, "id": kr_id})


@app.route("/api/kr/edit", methods=["POST"])
@login_required
def api_edit_kr():
    if not auth.can_edit_kr():
        return jsonify({"ok": False, "error": "Permission denied"}), 403
    d = request.json
    quarter = d.get("quarter", config.current_quarter())
    kr_id = d.get("id")
    now = datetime.now().strftime("%m/%d/%Y %H:%M")
    fields = {}
    for f in ("name", "owner", "target_value", "baseline_value", "direction", "unit"):
        if f in d:
            fields[f] = d[f]
    fields["last_updated"] = now
    sheets.update_kpi_fields(quarter, kr_id, fields)
    return jsonify({"ok": True})


@app.route("/api/kr/update", methods=["POST"])
@login_required
def api_update_kr():
    if not auth.can_update_kr():
        return jsonify({"ok": False, "error": "Permission denied"}), 403
    d = request.json
    quarter = d.get("quarter", config.current_quarter())
    now = datetime.now().strftime("%m/%d/%Y %H:%M")
    sheets.update_kpi_value(quarter, d["id"], d["okr_id"], float(d["value"]), now)
    return jsonify({"ok": True})


@app.route("/api/kr/delete", methods=["POST"])
@login_required
def api_delete_kr():
    d = request.json
    cat = d.get("category", "")
    if not auth.can_delete_kr(cat):
        return jsonify({"ok": False, "error": "Permission denied"}), 403
    sheets.delete_kpi(d.get("quarter", config.current_quarter()), d["id"], d["okr_id"])
    return jsonify({"ok": True})


@app.route("/api/note/add", methods=["POST"])
@login_required
def api_add_note():
    if not auth.can_add_note():
        return jsonify({"ok": False, "error": "Permission denied"}), 403
    d = request.json
    now = datetime.now().strftime("%m/%d/%Y %H:%M")
    author = auth.user_display_name()
    sheets.add_note(d["parent_type"], d["parent_id"], author, d["text"], now)
    return jsonify({"ok": True, "author": author, "timestamp": now})


@app.route("/api/note/edit", methods=["POST"])
@login_required
def api_edit_note():
    d = request.json
    sheets.update_note(d["parent_type"], d["parent_id"], d["timestamp"], d["author"], d["text"])
    return jsonify({"ok": True})


# ---------- Admin panel ----------

@app.route("/admin")
@login_required
@admin_required
def admin_panel():
    users = auth.list_users()
    user_list = users.to_dict("records") if not users.empty else []
    return render_template("admin.html", users=user_list, roles=config.ROLES,
                           categories=config.OKR_CATEGORIES)


@app.route("/api/admin/user/create", methods=["POST"])
@login_required
@admin_required
def api_create_user():
    d = request.json
    try:
        auth.create_user(
            d["email"], d["first_name"], d["last_name"],
            d["password"], d["role"], d.get("categories", ""),
        )
        return jsonify({"ok": True})
    except ValueError as e:
        return jsonify({"ok": False, "error": str(e)}), 400


@app.route("/api/admin/user/update", methods=["POST"])
@login_required
@admin_required
def api_update_user():
    d = request.json
    email = d.pop("email")
    try:
        auth.update_user(email, d)
        return jsonify({"ok": True})
    except ValueError as e:
        return jsonify({"ok": False, "error": str(e)}), 400


@app.route("/api/admin/user/delete", methods=["POST"])
@login_required
@admin_required
def api_delete_user():
    d = request.json
    try:
        auth.delete_user(d["email"])
        return jsonify({"ok": True})
    except ValueError as e:
        return jsonify({"ok": False, "error": str(e)}), 400


@app.route("/api/admin/user/reset-password", methods=["POST"])
@login_required
@admin_required
def api_reset_password():
    d = request.json
    try:
        auth.change_password(d["email"], d["password"])
        return jsonify({"ok": True})
    except ValueError as e:
        return jsonify({"ok": False, "error": str(e)}), 400


# ---------- Email panel ----------

@app.route("/email")
@login_required
def email_panel():
    if not auth.can_access_reports():
        flash("Access denied.", "error")
        return redirect(url_for("tracker"))
    quarter = request.args.get("quarter", config.current_quarter())
    return render_template("email_panel.html", quarter=quarter)


@app.route("/api/email/test", methods=["POST"])
@login_required
def api_email_test():
    if not auth.can_access_reports():
        return jsonify({"ok": False, "error": "Permission denied"}), 403
    d = request.json
    ok, msg = email_service.send_test_email(d["recipient"])
    return jsonify({"ok": ok, "message": msg})


@app.route("/api/email/send-report", methods=["POST"])
@login_required
def api_email_send_report():
    if not auth.can_access_reports():
        return jsonify({"ok": False, "error": "Permission denied"}), 403
    d = request.json
    quarter = d.get("quarter", config.current_quarter())
    okrs_df = sheets.read_okrs(quarter)
    kpis_df = sheets.read_kpis(quarter)
    notes_df = sheets.read_notes()
    user = d.get("user")
    if user:
        ok, msg = email_service.send_report(user, okrs_df, kpis_df, notes_df, quarter)
        return jsonify({"ok": ok, "message": msg})
    else:
        results = email_service.send_all_reports(okrs_df, kpis_df, notes_df, quarter)
        return jsonify({"ok": True, "results": [{"email": e, "ok": o, "msg": m} for e, o, m in results]})


@app.route("/api/email/send-nudge", methods=["POST"])
@login_required
def api_email_send_nudge():
    if not auth.can_access_reports():
        return jsonify({"ok": False, "error": "Permission denied"}), 403
    d = request.json
    quarter = d.get("quarter", config.current_quarter())
    okrs_df = sheets.read_okrs(quarter)
    kpis_df = sheets.read_kpis(quarter)
    user = d.get("user")
    stale = email_service.find_stale_krs(user, okrs_df, kpis_df)
    ok, msg = email_service.send_update_request(user, stale, quarter)
    return jsonify({"ok": ok, "message": msg})


@app.route("/api/email/send-digest", methods=["POST"])
@login_required
def api_email_send_digest():
    if not auth.can_access_reports():
        return jsonify({"ok": False, "error": "Permission denied"}), 403
    d = request.json
    quarter = d.get("quarter", config.current_quarter())
    okrs_df = sheets.read_okrs(quarter)
    kpis_df = sheets.read_kpis(quarter)
    notes_df = sheets.read_notes()
    results = email_service.send_weekly_digest(okrs_df, kpis_df, notes_df, quarter)
    return jsonify({"ok": True, "results": [{"email": e, "ok": o, "msg": m} for e, o, m in results]})


# ---------- Account settings ----------

@app.route("/account")
@login_required
def account_settings():
    return render_template("account.html")


@app.route("/api/account/change-password", methods=["POST"])
@login_required
def api_change_password():
    d = request.json
    current_pw = d.get("current_password", "")
    new_pw = d.get("new_password", "")

    user = auth.get_current_user()
    if not user:
        return jsonify({"ok": False, "error": "Not logged in"}), 401

    # Verify current password
    verified = auth.login(user["email"], current_pw)
    if not verified:
        return jsonify({"ok": False, "error": "Current password is incorrect"}), 400

    auth.change_password(user["email"], new_pw)
    auth.refresh_current_user()
    return jsonify({"ok": True, "message": "Password changed successfully"})


# ---------- Health check ----------

@app.route("/health")
def health_check():
    return jsonify({"status": "ok"})


# ---------- Error handlers ----------

@app.errorhandler(404)
def not_found(e):
    return render_template("base.html", error="Page not found"), 404


@app.errorhandler(500)
def server_error(e):
    return render_template("base.html", error="Internal server error"), 500


if __name__ == "__main__":
    import os
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=True)

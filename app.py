"""
OKR Tracker — Flask application (deployed on Render).

Optimizations:
- compute_all_progress eliminates N+1 pattern on OKR progress
- notes_for returns list[dict] directly (no DataFrame conversion)
- Minimal chart_data payload (only id + trend, not full OKR tree)
- flask-compress for gzip responses
"""

import os
import secrets
import time
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
import views
import email_service

app = Flask(__name__)
# Trust Render's one-hop proxy so request.host_url reflects the real https URL
# used in outbound invite / reset links.
try:
    from werkzeug.middleware.proxy_fix import ProxyFix
    app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)
except ImportError:
    pass
app.secret_key = config.SECRET_KEY
app.config["SESSION_COOKIE_HTTPONLY"] = True
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
# Tell the browser to cache static files for 1 day. Cache-busting is handled
# via ?v=ASSET_VERSION in template URLs (see base.html).
app.config["SEND_FILE_MAX_AGE_DEFAULT"] = 86400  # 1 day

# Asset version: git commit hash on Render, or process start time locally.
# Appended as ?v= to /static URLs so deploys force fresh CSS/JS.
ASSET_VERSION = os.environ.get("RENDER_GIT_COMMIT", "")[:8] or str(int(time.time()))

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


# ---------- Template filters ----------

@app.template_filter("initials")
def _initials(name: str) -> str:
    """"Jinesh Patel" -> "JP". Used for the header avatar."""
    parts = [p for p in str(name or "").split() if p]
    if not parts:
        return "?"
    if len(parts) == 1:
        return parts[0][:2].upper()
    return (parts[0][0] + parts[-1][0]).upper()


# ---------- Context processor ----------

@app.context_processor
def inject_globals():
    return {
        "auth": auth,
        "config": config,
        "current_quarter": config.current_quarter(),
        "quarter_list": config.quarter_list(),
        "now": datetime.now,
        "asset_version": ASSET_VERSION,
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


# ---------- Forgot password / reset / invite ----------

# Invite tokens live for a week so the recipient has time to click through.
# Reset tokens are short-lived so a leaked link stops working quickly.
_INVITE_TTL = 7 * 24 * 3600
_RESET_TTL = 60 * 60


def _reset_url_for(token: str) -> str:
    # Build against the incoming request so it works whether the app is behind
    # Render's proxy (https) or served from a preview URL, without relying on
    # PREFERRED_URL_SCHEME configuration.
    return request.host_url.rstrip("/") + url_for("reset_password_page", token=token)


@app.route("/forgot-password", methods=["GET", "POST"])
def forgot_password_page():
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        user = auth.find_user(email) if email else None
        if user:
            token = auth.make_reset_token(email, purpose="reset", ttl_seconds=_RESET_TTL)
            try:
                email_service.send_password_reset(
                    email, user.get("first_name", ""), _reset_url_for(token),
                )
            except Exception:
                # Never leak send failures — treat all responses the same so an
                # attacker can't probe which addresses exist.
                pass
        flash(
            "If an account exists for that email, we've sent a reset link. "
            "Check your inbox (and spam folder).",
            "success",
        )
        return redirect(url_for("login_page"))
    return render_template("forgot_password.html")


@app.route("/reset-password", methods=["GET", "POST"])
def reset_password_page():
    token = request.values.get("token", "")
    result = auth.verify_reset_token(token) if token else None
    if not result:
        return render_template(
            "reset_password.html", token="", email="", purpose="",
            error="This link is invalid or has expired. Request a new one below.",
        )
    email, purpose = result

    if request.method == "POST":
        pw = request.form.get("password", "")
        pw2 = request.form.get("password_confirm", "")
        if not pw or len(pw) < 8:
            return render_template("reset_password.html", token=token, email=email,
                                   purpose=purpose, error="Password must be at least 8 characters.")
        if pw != pw2:
            return render_template("reset_password.html", token=token, email=email,
                                   purpose=purpose, error="Passwords don't match.")
        try:
            auth.change_password(email, pw)
        except ValueError as e:
            return render_template("reset_password.html", token=token, email=email,
                                   purpose=purpose, error=str(e))
        # Log the user in so they land on the app directly.
        user = auth.find_user(email)
        if user:
            session["_current_user"] = user
        flash("Password set. Welcome!" if purpose == "invite" else "Password updated.",
              "success")
        return redirect(url_for("tracker"))

    return render_template("reset_password.html", token=token, email=email,
                           purpose=purpose, error="")


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

    okrs_df = views.visible_okrs(
        okrs_df, category, auth.user_role(), auth.user_categories(),
    )

    # Narrow the child frames to what survived the visibility filter.
    visible_okr_ids = set(okrs_df["id"].astype(str))
    kpis_df = kpis_df[kpis_df["okr_id"].astype(str).isin(visible_okr_ids)]
    visible_kpi_ids = set(kpis_df["id"].astype(str))
    if not history_df.empty:
        history_df = history_df[history_df["kpi_id"].astype(str).isin(visible_kpi_ids)]

    # Achievement per KR in one vectorized pass, reused for both the per-KR
    # display and the objective aggregate.
    if not kpis_df.empty:
        kpis_df = kpis_df.copy()
        kpis_df["_achievement"] = data._compute_achievements_vec(kpis_df)

    view = views.build_tracker_view(okrs_df, kpis_df, history_df, notes_df, quarter)

    # Notes are global, so trim the activity feed to parents this user can see.
    if not notes_df.empty:
        visible_notes = notes_df[
            ((notes_df["parent_type"] == "OKR") & (notes_df["parent_id"].astype(str).isin(visible_okr_ids)))
            | ((notes_df["parent_type"] == "KR") & (notes_df["parent_id"].astype(str).isin(visible_kpi_ids)))
        ]
    else:
        visible_notes = notes_df
    activity = data.recent_activity(visible_notes, history_df, okrs_df, kpis_df, limit=60)

    return render_template(
        "tracker.html",
        quarter=quarter,
        category=category,
        allowed_categories=allowed,
        categories=config.OKR_CATEGORIES,
        creatable_categories=auth.creatable_categories(),
        activity=activity,
        has_any_trend=bool(view["chart_data"]),
        **view,
    )


# ---------- API routes for AJAX actions ----------

def _okr_category(quarter: str, okr_id: str) -> str:
    """Look up an objective's real category.

    Permission checks must never key off a category supplied by the caller: a
    Manager could otherwise delete a Corporate objective just by POSTing
    {"id": "<corporate id>", "category": "Growth"}. read_okrs() is TTL-cached,
    so in the common case this is a dict lookup, not a Sheets round-trip.
    """
    okrs_df = sheets.read_okrs(quarter)
    if okrs_df.empty:
        return ""
    row = okrs_df[okrs_df["id"].astype(str) == str(okr_id)]
    return "" if row.empty else str(row.iloc[0].get("category", ""))


def _kr_category(quarter: str, kr_id: str) -> str:
    """Category of the objective a key result belongs to."""
    kpis_df = sheets.read_kpis(quarter)
    if kpis_df.empty:
        return ""
    row = kpis_df[kpis_df["id"].astype(str) == str(kr_id)]
    if row.empty:
        return ""
    return _okr_category(quarter, str(row.iloc[0].get("okr_id", "")))


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
    quarter = d.get("quarter", config.current_quarter())
    if not auth.can_delete_okr(_okr_category(quarter, d.get("id", ""))):
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
    # Resolve the category from the sheet, never from the request body.
    category = _okr_category(quarter, d.get("okr_id", ""))
    if not auth.can_create_kr_in_category(category):
        return jsonify({"ok": False, "error": "Permission denied"}), 403
    kr_id = str(uuid.uuid4())[:8]
    now = datetime.now().strftime("%m/%d/%Y %H:%M")
    row = [
        kr_id, d.get("okr_id", ""), d.get("name", ""),
        d.get("owner", ""), d.get("baseline_value", 0), d.get("target_value", 0),
        d.get("baseline_value", 0), d.get("direction", "increase"),
        d.get("unit", ""), now, d.get("description", ""),
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

    # Detect a move-to-different-Objective request. Enforce category permissions
    # on BOTH source and destination — a Manager cannot pull a KR out of Corporate
    # or push one into it.
    new_okr_id = d.get("okr_id")
    old_okr_id = None
    if new_okr_id:
        okrs_df = sheets.read_okrs(quarter)
        kpis_df = sheets.read_kpis(quarter)
        kr_row = kpis_df[kpis_df["id"] == str(kr_id)]
        if kr_row.empty:
            return jsonify({"ok": False, "error": "Key Result not found"}), 404
        old_okr_id = str(kr_row.iloc[0].get("okr_id", ""))
        if str(new_okr_id) != old_okr_id:
            src = okrs_df[okrs_df["id"] == old_okr_id]
            dst = okrs_df[okrs_df["id"] == str(new_okr_id)]
            if dst.empty:
                return jsonify({"ok": False, "error": "Destination Objective not found"}), 404
            src_cat = src.iloc[0].get("category", "") if not src.empty else ""
            dst_cat = dst.iloc[0].get("category", "")
            if not (auth.can_create_kr_in_category(src_cat) and auth.can_create_kr_in_category(dst_cat)):
                return jsonify({"ok": False, "error": "Permission denied for this category"}), 403
        else:
            new_okr_id = None  # no-op move

    fields = {}
    for f in ("name", "owner", "target_value", "baseline_value", "direction", "unit", "description"):
        if f in d:
            fields[f] = d[f]
    if new_okr_id:
        fields["okr_id"] = new_okr_id
    fields["last_updated"] = now
    sheets.update_kpi_fields(quarter, kr_id, fields)

    # If the KR moved, both OKRs' aggregate progress must be recomputed.
    if new_okr_id and old_okr_id and str(new_okr_id) != old_okr_id:
        fresh_kpis = sheets.read_kpis(quarter)
        sheets._sync_okr_progress(quarter, old_okr_id, fresh_kpis, now)
        sheets._sync_okr_progress(quarter, str(new_okr_id), fresh_kpis, now)
        sheets._cache_invalidate(f"okrs:{quarter}")

    return jsonify({"ok": True})


@app.route("/api/kr/update", methods=["POST"])
@login_required
def api_update_kr():
    if not auth.can_update_kr():
        return jsonify({"ok": False, "error": "Permission denied"}), 403
    d = request.json
    quarter = d.get("quarter", config.current_quarter())
    now = datetime.now().strftime("%m/%d/%Y %H:%M")
    author = auth.user_display_name()
    sheets.update_kpi_value(quarter, d["id"], d["okr_id"], float(d["value"]), now, author)
    # Optional companion note — saves the second round-trip when the user
    # types something in the Note field on the Update modal.
    note = (d.get("note") or "").strip()
    if note and auth.can_add_note():
        sheets.add_note("KR", d["id"], author, note, now)
    return jsonify({"ok": True})


@app.route("/api/kr/delete", methods=["POST"])
@login_required
def api_delete_kr():
    d = request.json
    quarter = d.get("quarter", config.current_quarter())
    if not auth.can_delete_kr(_kr_category(quarter, d.get("id", ""))):
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
    send_invite = bool(d.get("send_invite"))
    # When inviting, the admin doesn't type a password — set a random unguessable
    # one so the account row is valid; the user chooses their real password via
    # the invite link.
    password = d.get("password") or (secrets.token_urlsafe(24) if send_invite else "")
    if not password:
        return jsonify({"ok": False, "error": "Password required (or enable Send Invite)."}), 400
    try:
        auth.create_user(
            d["email"], d["first_name"], d["last_name"],
            password, d["role"], d.get("categories", ""),
        )
    except ValueError as e:
        return jsonify({"ok": False, "error": str(e)}), 400

    if send_invite:
        token = auth.make_reset_token(d["email"], purpose="invite", ttl_seconds=_INVITE_TTL)
        try:
            ok, msg = email_service.send_invite(
                d["email"], d.get("first_name", ""), _reset_url_for(token),
            )
            if not ok:
                return jsonify({"ok": True, "invite_warning": msg})
        except Exception as e:
            return jsonify({"ok": True, "invite_warning": str(e)})
    return jsonify({"ok": True})


@app.route("/api/admin/user/send-invite", methods=["POST"])
@login_required
@admin_required
def api_send_invite():
    d = request.json
    email = (d.get("email") or "").strip().lower()
    user = auth.find_user(email)
    if not user:
        return jsonify({"ok": False, "error": "User not found"}), 404
    token = auth.make_reset_token(email, purpose="invite", ttl_seconds=_INVITE_TTL)
    try:
        ok, msg = email_service.send_invite(
            email, user.get("first_name", ""), _reset_url_for(token),
        )
        if not ok:
            return jsonify({"ok": False, "error": msg}), 502
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 502
    return jsonify({"ok": True})


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
    # Show the exact From header recipients will see, so a misconfigured
    # SENDER_NAME/SENDER_EMAIL is visible before anything is sent.
    sender_from = email_service.from_header() if config.SENDER_EMAIL else ""
    return render_template("email_panel.html", quarter=quarter, sender_from=sender_from)


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
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=True)

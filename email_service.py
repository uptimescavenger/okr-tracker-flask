"""
Email notification service for OKR Tracker (Flask version).
Uses Gmail API with domain-wide delegation via GCP service account.
"""

import base64
from datetime import datetime, timedelta
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.utils import formataddr

import pandas as pd
from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

import auth_service as auth
import config
import data

GMAIL_SCOPES = ["https://www.googleapis.com/auth/gmail.send"]

_gmail_service = None


def _get_gmail_service():
    global _gmail_service
    if _gmail_service is not None:
        return _gmail_service
    if not config.SENDER_EMAIL:
        raise RuntimeError("SENDER_EMAIL is not configured.")
    creds = Credentials.from_service_account_info(
        config.GCP_SERVICE_ACCOUNT, scopes=GMAIL_SCOPES
    )
    delegated = creds.with_subject(config.SENDER_EMAIL)
    _gmail_service = build("gmail", "v1", credentials=delegated, cache_discovery=False)
    return _gmail_service


# -- Data Assembly --

def _filter_okrs_for_user(user: dict, okrs_df: pd.DataFrame) -> pd.DataFrame:
    if okrs_df.empty:
        return okrs_df
    role = user.get("role", "Team Member")
    cats_str = str(user.get("categories", ""))
    if role == "Admin" or cats_str.lower() == "all":
        return okrs_df
    user_cats = [c.strip() for c in cats_str.split(",") if c.strip()]
    if role == "Manager":
        visible = ["Corporate"] + user_cats
        return okrs_df[okrs_df["category"].isin(visible) | (okrs_df["category"] == "")]
    else:
        return okrs_df[okrs_df["category"].isin(user_cats)]


def _index_frames(kpis_df, notes_df):
    """Group the shared frames once so per-user reports are dict lookups.

    Every report used to re-scan the whole KPI frame twice per objective and
    the whole Notes frame once per KR — work that is identical for every
    recipient. These are the same helpers the tracker page uses.
    """
    if not kpis_df.empty and "_achievement" not in kpis_df.columns:
        kpis_df = kpis_df.copy()
        kpis_df["_achievement"] = data._compute_achievements_vec(kpis_df)
    return {
        "kpis_df": kpis_df,
        "krs_by_okr": data.group_kpis_by_okr(kpis_df),
        "notes_by_parent": data.group_notes_by_parent(notes_df),
        # Same aggregate the tracker page uses, so an objective never shows one
        # percentage in the UI and a different one in the email.
        "progress": data.progress_from_achievements(
            [] if kpis_df.empty else list(kpis_df["okr_id"].astype(str).unique()),
            kpis_df,
        ),
    }


def build_user_report(user, okrs_df, kpis_df, notes_df, quarter, idx=None):
    visible_okrs = _filter_okrs_for_user(user, okrs_df)
    if visible_okrs.empty:
        return None
    if idx is None:
        idx = _index_frames(kpis_df, notes_df)
    krs_by_okr = idx["krs_by_okr"]
    notes_by_parent = idx["notes_by_parent"]

    cutoff = datetime.now() - timedelta(days=7)
    okr_reports = []
    for okr_row in visible_okrs.to_dict("records"):
        okr_id = str(okr_row["id"])
        krs = krs_by_okr.get(okr_id, [])
        achievements = [float(k.get("_achievement", 0) or 0) for k in krs]
        pct = idx["progress"].get(okr_id, 0.0)
        color = data.progress_color(pct)

        kr_list = []
        collected_notes = list(notes_by_parent.get(("OKR", okr_id), []))
        for kr_row, achievement in zip(krs, achievements):
            kr_list.append({
                "name": kr_row.get("name", ""),
                "current": data.format_value(kr_row.get("current_value", 0), kr_row.get("unit", "")),
                "target": data.format_value(kr_row.get("target_value", 0), kr_row.get("unit", "")),
                "achievement": round(achievement, 1),
                "color": data.progress_color(achievement),
            })
            collected_notes.extend(notes_by_parent.get(("KR", str(kr_row.get("id", ""))), []))

        recent_notes = []
        for n in collected_notes:
            ts = n.get("_parsed_ts")
            if ts is None:
                try:
                    ts = pd.to_datetime(n.get("timestamp", ""), format="mixed", dayfirst=False)
                except Exception:
                    continue
            if pd.notna(ts) and ts >= cutoff:
                recent_notes.append({
                    "author": n.get("author", ""),
                    "timestamp": str(n.get("timestamp", "")),
                    "text": str(n.get("text", "")),
                })

        okr_reports.append({
            "title": okr_row.get("title", ""),
            "category": okr_row.get("category", ""),
            "owner": okr_row.get("owner", ""),
            "progress": pct,
            "color": color,
            "at_risk": pct < 25,
            "krs": kr_list,
            "recent_notes": recent_notes,
        })

    total = len(okr_reports)
    avg_progress = round(sum(o["progress"] for o in okr_reports) / total, 1) if total else 0
    at_risk = sum(1 for o in okr_reports if o["at_risk"])
    return {
        "user_name": f"{user.get('first_name', '')} {user.get('last_name', '')}".strip(),
        "user_email": user.get("email", ""),
        "quarter": quarter,
        "total_okrs": total,
        "avg_progress": avg_progress,
        "at_risk_count": at_risk,
        "okrs": okr_reports,
    }


def find_accessible_krs(user, okrs_df, kpis_df):
    visible_okrs = _filter_okrs_for_user(user, okrs_df)
    if visible_okrs.empty or kpis_df.empty:
        return []
    cutoff = datetime.now() - timedelta(days=7)
    krs_by_okr = data.group_kpis_by_okr(kpis_df)
    result = []
    for okr_row in visible_okrs.to_dict("records"):
        okr_id = str(okr_row["id"])
        for kr_row in krs_by_okr.get(okr_id, []):
            last_updated = str(kr_row.get("last_updated", ""))
            is_stale = False
            try:
                updated_dt = pd.to_datetime(last_updated, format="mixed", dayfirst=False)
                if pd.isna(updated_dt) or updated_dt < cutoff:
                    is_stale = True
            except Exception:
                is_stale = True
            result.append({
                "kr_name": kr_row.get("name", ""),
                "kr_id": str(kr_row.get("id", "")),
                "okr_title": okr_row.get("title", ""),
                "okr_id": okr_id,
                "category": str(okr_row.get("category", "")),
                "last_updated": last_updated or "Never",
                "current": data.format_value(kr_row.get("current_value", 0), kr_row.get("unit", "")),
                "target": data.format_value(kr_row.get("target_value", 0), kr_row.get("unit", "")),
                "stale": is_stale,
            })
    return result


def find_stale_krs(user, okrs_df, kpis_df, days=7):
    all_krs = find_accessible_krs(user, okrs_df, kpis_df)
    return [kr for kr in all_krs if kr.get("stale")]


# -- HTML Email Templates --

_HEADER_STYLE = (
    "background: linear-gradient(135deg, #6366f1, #8b5cf6); "
    "padding: 24px; text-align: center; border-radius: 8px 8px 0 0;"
)
_CARD_STYLE = (
    "background: #ffffff; border: 1px solid #e2e8f0; border-radius: 8px; "
    "margin-bottom: 16px; overflow: hidden;"
)
_CARD_HEADER = (
    "padding: 12px 16px; border-bottom: 1px solid #e2e8f0; "
    "font-size: 16px; font-weight: 700; color: #1e293b;"
)
_KR_ROW = (
    "padding: 10px 16px; border-bottom: 1px solid #f1f5f9; "
    "font-size: 14px; color: #334155;"
)
_NOTE_STYLE = (
    "padding: 8px 16px; font-size: 13px; color: #64748b; "
    "background: #f8fafc; border-left: 3px solid #6366f1;"
)


def _progress_bar_html(pct, color, width="100%"):
    clamped = max(0, min(pct, 100))
    return (
        f'<div style="background:#e2e8f0; border-radius:4px; height:8px; width:{width};">'
        f'<div style="background:{color}; border-radius:4px; height:8px; '
        f'width:{clamped}%; min-width:2px;"></div></div>'
    )


def _metric_box(label, value, color="#6366f1"):
    return (
        f'<td style="text-align:center; padding:12px 16px; background:#f8fafc; '
        f'border-radius:8px; border:1px solid #e2e8f0;">'
        f'<div style="font-size:11px; color:{color}; text-transform:uppercase; '
        f'font-weight:700; letter-spacing:0.05em;">{label}</div>'
        f'<div style="font-size:22px; font-weight:800; color:#1e293b;">{value}</div>'
        f'</td>'
    )


def render_report_html(report, subject_prefix="Progress Report"):
    quarter = report["quarter"]
    user_name = report["user_name"]
    html = f'''
    <div style="max-width:640px; margin:0 auto; font-family:Arial,Helvetica,sans-serif; color:#1e293b;">
        <div style="{_HEADER_STYLE}">
            <h1 style="color:#ffffff; margin:0; font-size:22px;">OKR Tracker</h1>
            <p style="color:#c7d2fe; margin:4px 0 0; font-size:14px;">{subject_prefix} &mdash; {quarter}</p>
        </div>
        <div style="background:#ffffff; padding:20px; border:1px solid #e2e8f0; border-top:none;">
            <p style="font-size:15px; color:#475569;">Hi {user_name},</p>
            <p style="font-size:14px; color:#64748b;">Here is your OKR progress summary for <strong>{quarter}</strong>.</p>
            <table style="width:100%; border-spacing:8px; margin:16px 0;">
                <tr>
                    {_metric_box("Objectives", str(report["total_okrs"]))}
                    {_metric_box("Avg Progress", f'{report["avg_progress"]}%')}
                    {_metric_box("At Risk", str(report["at_risk_count"]), "#ef4444" if report["at_risk_count"] > 0 else "#6366f1")}
                </tr>
            </table>
    '''
    for okr in report["okrs"]:
        at_risk_bg = "background:#fef2f2;" if okr["at_risk"] else ""
        risk_badge = (
            '<span style="background:#ef4444; color:#fff; padding:2px 8px; border-radius:4px; '
            'font-size:11px; font-weight:700; margin-left:8px;">AT RISK</span>'
            if okr["at_risk"] else ""
        )
        cat_badge = ""
        if okr["category"]:
            cat_badge = (
                f'<span style="background:#f1f0fb; color:#6366f1; padding:2px 8px; '
                f'border-radius:4px; font-size:11px; font-weight:600; margin-left:8px;">'
                f'{okr["category"]}</span>'
            )
        html += f'''
            <div style="{_CARD_STYLE} {at_risk_bg}">
                <div style="{_CARD_HEADER}">{okr["title"]}{cat_badge}{risk_badge}
                    <div style="float:right; color:{okr["color"]}; font-size:18px; font-weight:800;">{okr["progress"]}%</div>
                </div>
                <div style="padding:8px 16px 4px;">{_progress_bar_html(okr["progress"], okr["color"])}</div>
        '''
        for kr in okr["krs"]:
            html += f'''
                <div style="{_KR_ROW}">
                    <div style="margin-bottom:2px;">{kr["name"]}</div>
                    <div style="font-size:12px; color:#94a3b8;">
                        {kr["current"]} / {kr["target"]} &bull;
                        <span style="color:{kr["color"]}; font-weight:700;">{kr["achievement"]}%</span>
                    </div>
                </div>
            '''
        if okr["recent_notes"]:
            html += '<div style="padding:8px 16px 4px;"><div style="font-size:12px; font-weight:700; color:#6366f1; text-transform:uppercase; margin-bottom:6px;">Recent Notes</div>'
            for note in okr["recent_notes"][:5]:
                html += (
                    f'<div style="{_NOTE_STYLE} margin-bottom:6px; border-radius:4px;">'
                    f'<strong>{note["author"]}</strong> &mdash; {note["timestamp"]}<br>'
                    f'{note["text"]}</div>'
                )
            html += '</div>'
        html += '</div>'
    html += '''
            <div style="text-align:center; padding:20px 0 10px; color:#94a3b8; font-size:12px;">
                <p>Powered by OKR Tracker</p>
            </div>
        </div>
    </div>
    '''
    return html


def render_update_request_html(user_name, stale_krs, quarter):
    html = f'''
    <div style="max-width:640px; margin:0 auto; font-family:Arial,Helvetica,sans-serif; color:#1e293b;">
        <div style="{_HEADER_STYLE}">
            <h1 style="color:#ffffff; margin:0; font-size:22px;">OKR Tracker</h1>
            <p style="color:#c7d2fe; margin:4px 0 0; font-size:14px;">Update Request &mdash; {quarter}</p>
        </div>
        <div style="background:#ffffff; padding:20px; border:1px solid #e2e8f0; border-top:none;">
            <p style="font-size:15px; color:#475569;">Hi {user_name},</p>
            <p style="font-size:14px; color:#64748b;">
                The following Key Results haven't been updated recently.
                Please log in to the OKR Tracker and update your progress.
            </p>
    '''
    for kr in stale_krs:
        html += f'''
            <div style="{_CARD_STYLE}">
                <div style="padding:12px 16px;">
                    <div style="font-weight:700; color:#1e293b; margin-bottom:4px;">{kr["kr_name"]}</div>
                    <div style="font-size:13px; color:#64748b;">
                        Objective: {kr["okr_title"]}<br>
                        Current: {kr["current"]} &rarr; Target: {kr["target"]}<br>
                        Last updated: <span style="color:#ef4444; font-weight:600;">{kr["last_updated"]}</span>
                    </div>
                </div>
            </div>
        '''
    html += '''
            <div style="text-align:center; margin:20px 0;">
                <p style="font-size:14px; color:#64748b;">Please update your Key Results at your earliest convenience.</p>
            </div>
            <div style="text-align:center; padding:20px 0 10px; color:#94a3b8; font-size:12px;">
                <p>Powered by OKR Tracker</p>
            </div>
        </div>
    </div>
    '''
    return html


# -- Send Functions --

def from_header() -> str:
    """"OKR Tracker <okr@example.com>", or the bare address if no name is set.

    formataddr handles the fiddly parts: it quotes names containing commas or
    periods, and RFC 2047-encodes anything non-ASCII, either of which would
    produce a malformed header if the string were just concatenated.
    """
    name = (config.SENDER_NAME or "").strip()
    if not name:
        return config.SENDER_EMAIL
    return formataddr((name, config.SENDER_EMAIL))


def _send_email(to, subject, html_body, plain_text=""):
    try:
        service = _get_gmail_service()
    except RuntimeError as e:
        return False, str(e)
    except Exception as e:
        return False, f"Gmail auth failed: {e}"

    msg = MIMEMultipart("alternative")
    msg["From"] = from_header()
    msg["To"] = to
    msg["Subject"] = subject
    if not plain_text:
        plain_text = f"View this email in an HTML-compatible email client.\n\nSubject: {subject}"
    msg.attach(MIMEText(plain_text, "plain"))
    msg.attach(MIMEText(html_body, "html"))
    raw = base64.urlsafe_b64encode(msg.as_bytes()).decode()
    try:
        service.users().messages().send(userId="me", body={"raw": raw}).execute()
        return True, f"Email sent to {to}"
    except HttpError as e:
        if e.resp.status == 403:
            return False, "Permission denied. Ensure domain-wide delegation is enabled."
        if e.resp.status == 400:
            return False, f"Invalid request — check that '{to}' is a valid email."
        if e.resp.status == 429:
            return False, "Rate limit exceeded. Try again in a few minutes."
        return False, f"Gmail API error ({e.resp.status}): {e}"
    except Exception as e:
        return False, f"Failed to send email: {e}"


def send_test_email(recipient):
    html = f'''
    <div style="max-width:640px; margin:0 auto; font-family:Arial,Helvetica,sans-serif;">
        <div style="{_HEADER_STYLE}">
            <h1 style="color:#ffffff; margin:0; font-size:22px;">OKR Tracker</h1>
            <p style="color:#c7d2fe; margin:4px 0 0; font-size:14px;">Test Email</p>
        </div>
        <div style="background:#ffffff; padding:24px; border:1px solid #e2e8f0; border-top:none; text-align:center;">
            <p style="font-size:16px; color:#22c55e; font-weight:700;">Email setup is working!</p>
            <p style="font-size:14px; color:#64748b;">
                This confirms that the OKR Tracker can send emails via Gmail API.
            </p>
            <p style="font-size:12px; color:#94a3b8;">
                Sent from: {from_header()}<br>
                Sent at: {datetime.now().strftime("%m/%d/%Y %H:%M")}
            </p>
        </div>
    </div>
    '''
    return _send_email(recipient, "OKR Tracker — Test Email", html)


def send_report(user, okrs_df, kpis_df, notes_df, quarter):
    report = build_user_report(user, okrs_df, kpis_df, notes_df, quarter)
    if not report:
        return False, f"No OKR data for {user.get('email', 'user')} in {quarter}."
    html = render_report_html(report, "Progress Report")
    return _send_email(user["email"], f"OKR Progress Report — {quarter}", html)


def _broadcast(okrs_df, kpis_df, notes_df, quarter, subject_prefix, subject):
    """Send one report per user, grouping the shared frames a single time."""
    idx = _index_frames(kpis_df, notes_df)
    results = []
    for u in auth.list_users().to_dict("records"):
        report = build_user_report(u, okrs_df, kpis_df, notes_df, quarter, idx=idx)
        if not report:
            results.append((u.get("email", ""), False, "No data for this user."))
            continue
        html = render_report_html(report, subject_prefix)
        ok, msg = _send_email(u["email"], subject, html)
        results.append((u.get("email", ""), ok, msg))
    return results


def send_all_reports(okrs_df, kpis_df, notes_df, quarter):
    return _broadcast(okrs_df, kpis_df, notes_df, quarter,
                      "Progress Report", f"OKR Progress Report — {quarter}")


def send_update_request(user, stale_krs, quarter):
    if not stale_krs:
        return False, "No stale Key Results found for this user."
    name = f"{user.get('first_name', '')} {user.get('last_name', '')}".strip()
    html = render_update_request_html(name, stale_krs, quarter)
    return _send_email(user["email"], f"OKR Update Request — {quarter}", html)


def send_invite(email, first_name, invite_url):
    """First-time invite — asks the recipient to set their password."""
    greeting = f"Hi {first_name}," if first_name else "Hi,"
    html = f'''
    <div style="max-width:640px; margin:0 auto; font-family:Arial,Helvetica,sans-serif; color:#1e293b;">
        <div style="{_HEADER_STYLE}">
            <h1 style="color:#ffffff; margin:0; font-size:22px;">OKR Tracker</h1>
            <p style="color:#c7d2fe; margin:4px 0 0; font-size:14px;">You&#39;re invited</p>
        </div>
        <div style="background:#ffffff; padding:24px; border:1px solid #e2e8f0; border-top:none;">
            <p style="font-size:15px; color:#475569;">{greeting}</p>
            <p style="font-size:14px; color:#64748b;">
                You&#39;ve been invited to the OKR Tracker. Click below to set your password
                and get started. This link is valid for 7 days.
            </p>
            <div style="text-align:center; margin:24px 0;">
                <a href="{invite_url}" style="background:#6366f1; color:#fff; padding:12px 28px; border-radius:6px; text-decoration:none; font-weight:700; font-size:14px; display:inline-block;">Set your password</a>
            </div>
            <p style="font-size:12px; color:#94a3b8;">If the button doesn&#39;t work, paste this URL into your browser:<br>{invite_url}</p>
        </div>
        <div style="text-align:center; padding:20px 0 10px; color:#94a3b8; font-size:12px;">
            <p>Powered by OKR Tracker</p>
        </div>
    </div>
    '''
    plain = f"You've been invited to OKR Tracker. Set your password: {invite_url}"
    return _send_email(email, "You're invited to OKR Tracker", html, plain)


def send_password_reset(email, first_name, reset_url):
    """Password reset — link is valid for 1 hour."""
    greeting = f"Hi {first_name}," if first_name else "Hi,"
    html = f'''
    <div style="max-width:640px; margin:0 auto; font-family:Arial,Helvetica,sans-serif; color:#1e293b;">
        <div style="{_HEADER_STYLE}">
            <h1 style="color:#ffffff; margin:0; font-size:22px;">OKR Tracker</h1>
            <p style="color:#c7d2fe; margin:4px 0 0; font-size:14px;">Password reset</p>
        </div>
        <div style="background:#ffffff; padding:24px; border:1px solid #e2e8f0; border-top:none;">
            <p style="font-size:15px; color:#475569;">{greeting}</p>
            <p style="font-size:14px; color:#64748b;">
                We received a request to reset your password. Click below to choose a new one.
                This link is valid for 1 hour. If you didn&#39;t request this, you can safely ignore this email.
            </p>
            <div style="text-align:center; margin:24px 0;">
                <a href="{reset_url}" style="background:#6366f1; color:#fff; padding:12px 28px; border-radius:6px; text-decoration:none; font-weight:700; font-size:14px; display:inline-block;">Reset your password</a>
            </div>
            <p style="font-size:12px; color:#94a3b8;">If the button doesn&#39;t work, paste this URL into your browser:<br>{reset_url}</p>
        </div>
        <div style="text-align:center; padding:20px 0 10px; color:#94a3b8; font-size:12px;">
            <p>Powered by OKR Tracker</p>
        </div>
    </div>
    '''
    plain = f"Reset your OKR Tracker password: {reset_url}"
    return _send_email(email, "Reset your OKR Tracker password", html, plain)


def send_weekly_digest(okrs_df, kpis_df, notes_df, quarter):
    return _broadcast(okrs_df, kpis_df, notes_df, quarter,
                      "Weekly Digest", f"OKR Weekly Digest — {quarter}")

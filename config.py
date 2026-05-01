"""
Configuration for the OKR Tracker application.
Credentials loaded from environment variables (Render) or .env file (local dev).
"""

import os
from datetime import date

# ---------- Google Sheets ----------
SPREADSHEET_ID = os.environ.get("SPREADSHEET_ID", "1JNgemdOvb8JstpGnlkxumT62qkK3wLyNNdDbTq6xTxo")

# GCP Service Account credentials from env vars
GCP_SERVICE_ACCOUNT = {
    "type": "service_account",
    "project_id": os.environ.get("GCP_PROJECT_ID", ""),
    "private_key_id": os.environ.get("GCP_PRIVATE_KEY_ID", ""),
    "private_key": os.environ.get("GCP_PRIVATE_KEY", "").replace("\\n", "\n"),
    "client_email": os.environ.get("GCP_CLIENT_EMAIL", ""),
    "client_id": os.environ.get("GCP_CLIENT_ID", ""),
    "auth_uri": "https://accounts.google.com/o/oauth2/auth",
    "token_uri": "https://oauth2.googleapis.com/token",
    "auth_provider_x509_cert_url": "https://www.googleapis.com/oauth2/v1/certs",
    "client_x509_cert_url": os.environ.get("GCP_CLIENT_CERT_URL", ""),
}

# ---------- Flask ----------
SECRET_KEY = os.environ.get("FLASK_SECRET_KEY", "okr-tracker-dev-secret-change-me")

# ---------- Quarter helpers ----------
def current_quarter() -> str:
    today = date.today()
    q = (today.month - 1) // 3 + 1
    return f"{today.year}-Q{q}"


def quarter_list(start_year: int = 2024) -> list[str]:
    today = date.today()
    current_y, current_q = today.year, (today.month - 1) // 3 + 1
    if current_q < 4:
        next_y, next_q = current_y, current_q + 1
    else:
        next_y, next_q = current_y + 1, 1
    quarters = []
    for y in range(start_year, next_y + 1):
        for q in range(1, 5):
            if y == next_y and q > next_q:
                break
            quarters.append(f"{y}-Q{q}")
    return quarters


# ---------- Sheet tab naming ----------
def okr_tab_name(quarter: str) -> str:
    return f"OKRs {quarter}"

def kpi_tab_name(quarter: str) -> str:
    return f"KPIs {quarter}"

def notes_tab_name() -> str:
    return "Notes"

def users_tab_name() -> str:
    return "Users"

# ---------- Column schemas ----------
OKR_COLUMNS = [
    "id", "title", "description", "owner",
    "target_date", "progress", "last_updated", "category",
]

OKR_CATEGORIES = ["Corporate", "Growth", "Operations", "Development", "Finance"]

KPI_COLUMNS = [
    "id", "okr_id", "name", "owner", "current_value",
    "target_value", "baseline_value", "direction", "unit", "last_updated",
    "description",
]

KPI_HISTORY_COLUMNS = ["kpi_id", "date", "value", "author"]

NOTES_COLUMNS = ["parent_type", "parent_id", "timestamp", "author", "text"]

USER_COLUMNS = [
    "email", "first_name", "last_name", "password_hash", "role", "categories",
]

ROLES = ["Admin", "Manager", "Team Member"]

# ---------- Email ----------
SENDER_EMAIL = os.environ.get("SENDER_EMAIL", "")

# ---------- UI ----------
PAGE_TITLE = "OKR Tracker"
CACHE_TTL_SECONDS = 120

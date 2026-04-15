"""
Authentication and user management for OKR Tracker (Flask version).
Users stored in Google Sheets "Users" tab.
Passwords hashed with SHA-256 + salt. Remember-me via signed HMAC cookie.

Optimizations:
- User reads routed through sheets.read_users() (cached with TTL)
- Write operations clear cache after mutation
"""

import hashlib
import hmac
import base64
import secrets
from flask import session
import pandas as pd
import config


# -- Password hashing --

def _hash_password(password: str, salt: str = "") -> str:
    if not salt:
        salt = secrets.token_hex(8)
    h = hashlib.sha256(f"{salt}{password}".encode()).hexdigest()
    return f"{salt}:{h}"


def _check_password(password: str, stored_hash: str) -> bool:
    if ":" not in stored_hash:
        return False
    salt, _ = stored_hash.split(":", 1)
    return _hash_password(password, salt) == stored_hash


# -- Remember-me token --

_TOKEN_SECRET = "okr-tracker-v2-remember"


def make_remember_token(email: str) -> str:
    key = f"{_TOKEN_SECRET}:{config.SPREADSHEET_ID}".encode()
    sig = hmac.new(key, email.strip().lower().encode(), hashlib.sha256).hexdigest()
    payload = f"{email.strip().lower()}:{sig}"
    return base64.urlsafe_b64encode(payload.encode()).decode()


def verify_remember_token(token: str) -> str | None:
    try:
        payload = base64.urlsafe_b64decode(token.encode()).decode()
        email, sig = payload.rsplit(":", 1)
        key = f"{_TOKEN_SECRET}:{config.SPREADSHEET_ID}".encode()
        expected = hmac.new(key, email.encode(), hashlib.sha256).hexdigest()
        if hmac.compare_digest(sig, expected):
            return email
    except Exception:
        pass
    return None


def auto_login_from_cookie(token: str) -> bool:
    if not token:
        return False
    email = verify_remember_token(token)
    if not email:
        return False
    df = _read_users()
    match = df[df["email"].str.lower() == email]
    if match.empty:
        return False
    session["_current_user"] = match.iloc[0].to_dict()
    return True


# -- Sheet access (uses cached reads) --

def _get_users_ws():
    import sheets
    return sheets._get_or_create_worksheet(config.users_tab_name(), config.USER_COLUMNS)


def _read_users() -> pd.DataFrame:
    """Read users through the cached sheets layer."""
    import sheets
    return sheets.read_users()


def _clear_users_cache():
    """Clear cache after user mutations."""
    import sheets
    sheets.clear_cache()


def seed_admin():
    df = _read_users()
    if not df.empty:
        return
    ws = _get_users_ws()
    pw_hash = _hash_password("Test1234")
    ws.append_row(
        ["jinesh@uptimehealth.com", "Jinesh", "Patel", pw_hash, "Admin", "All"],
        value_input_option="USER_ENTERED",
    )
    _clear_users_cache()


# -- Public API --

def login(email: str, password: str) -> dict | None:
    df = _read_users()
    match = df[df["email"].str.lower() == email.strip().lower()]
    if match.empty:
        return None
    user = match.iloc[0]
    if _check_password(password, str(user["password_hash"])):
        return user.to_dict()
    return None


def get_current_user() -> dict | None:
    return session.get("_current_user")


def is_logged_in() -> bool:
    return get_current_user() is not None


def user_display_name() -> str:
    u = get_current_user()
    if u:
        return f"{u.get('first_name', '')} {u.get('last_name', '')}".strip()
    return "Unknown"


def user_role() -> str:
    u = get_current_user()
    return u.get("role", "Team Member") if u else "Team Member"


def user_email() -> str:
    u = get_current_user()
    return u.get("email", "") if u else ""


def user_categories() -> list[str]:
    u = get_current_user()
    if not u:
        return []
    cats = str(u.get("categories", ""))
    if cats.lower() == "all" or u.get("role") == "Admin":
        return config.OKR_CATEGORIES
    return [c.strip() for c in cats.split(",") if c.strip()]


def allowed_filter_options() -> list[str]:
    role = user_role()
    if role == "Admin":
        return ["All"] + config.OKR_CATEGORIES
    elif role == "Manager":
        cats = user_categories()
        options = ["All", "Corporate"]
        for c in cats:
            if c not in options:
                options.append(c)
        return options
    else:
        return user_categories()


def can_create_okr() -> bool:
    return user_role() in ("Admin", "Manager")

def can_create_okr_in_category(category: str) -> bool:
    """Check if user can create an OKR in a specific category."""
    role = user_role()
    if role == "Admin":
        return True
    if role == "Manager":
        if category == "Corporate":
            return False
        return category in user_categories() or category == ""
    return False

def creatable_categories() -> list[str]:
    """Return the list of categories the current user can create OKRs in."""
    role = user_role()
    if role == "Admin":
        return config.OKR_CATEGORIES
    if role == "Manager":
        return [c for c in user_categories() if c != "Corporate"]
    return []

def can_create_kr() -> bool:
    return user_role() in ("Admin", "Manager")

def can_edit_okr() -> bool:
    return user_role() in ("Admin", "Manager")

def can_edit_kr() -> bool:
    return user_role() in ("Admin", "Manager")

def can_update_kr() -> bool:
    return True

def can_add_note() -> bool:
    return True

def can_delete_okr(category: str = "") -> bool:
    role = user_role()
    if role == "Admin":
        return True
    if role == "Manager":
        if category == "Corporate" or category == "":
            return False
        return category in user_categories()
    return False

def can_delete_kr(category: str = "") -> bool:
    role = user_role()
    if role == "Admin":
        return True
    if role == "Manager":
        if category == "Corporate" or category == "":
            return False
        return category in user_categories()
    return False

def is_admin() -> bool:
    return user_role() == "Admin"

def can_access_reports() -> bool:
    return user_role() in ("Admin", "Manager")


def list_users_for_reports() -> pd.DataFrame:
    all_users = _read_users()
    if all_users.empty:
        return all_users
    role = user_role()
    if role == "Admin":
        return all_users
    my_cats = set(user_categories())
    mask = []
    for _, row in all_users.iterrows():
        u_cats_str = str(row.get("categories", ""))
        if u_cats_str.lower() == "all":
            mask.append(bool(my_cats))
        else:
            u_cats = {c.strip() for c in u_cats_str.split(",") if c.strip()}
            mask.append(bool(my_cats & u_cats))
    return all_users[mask].reset_index(drop=True)


# -- User CRUD (admin) --

def list_users() -> pd.DataFrame:
    return _read_users()


def create_user(email: str, first_name: str, last_name: str,
                password: str, role: str, categories: str):
    df = _read_users()
    if not df[df["email"].str.lower() == email.strip().lower()].empty:
        raise ValueError(f"User with email '{email}' already exists.")
    ws = _get_users_ws()
    pw_hash = _hash_password(password)
    ws.append_row(
        [email.strip().lower(), first_name.strip(), last_name.strip(),
         pw_hash, role, categories],
        value_input_option="USER_ENTERED",
    )
    _clear_users_cache()


def update_user(email: str, fields: dict):
    ws = _get_users_ws()
    cell = ws.find(email.strip().lower(), in_column=1)
    if cell is None:
        raise ValueError(f"User '{email}' not found.")
    row_values = ws.row_values(cell.row)
    while len(row_values) < len(config.USER_COLUMNS):
        row_values.append("")
    for col_name, value in fields.items():
        col_idx = config.USER_COLUMNS.index(col_name)
        row_values[col_idx] = value
    ws.update(f"A{cell.row}", [row_values], value_input_option="USER_ENTERED")
    _clear_users_cache()


def change_password(email: str, new_password: str):
    pw_hash = _hash_password(new_password)
    update_user(email, {"password_hash": pw_hash})


def delete_user(email: str):
    ws = _get_users_ws()
    cell = ws.find(email.strip().lower(), in_column=1)
    if cell is None:
        raise ValueError(f"User '{email}' not found.")
    ws.delete_rows(cell.row)
    _clear_users_cache()


def refresh_current_user():
    u = get_current_user()
    if not u:
        return
    df = _read_users()
    match = df[df["email"].str.lower() == u["email"].strip().lower()]
    if not match.empty:
        session["_current_user"] = match.iloc[0].to_dict()

"""
Google Sheets integration layer using gspread.
All reads/writes go through this module.

Optimizations (v2):
- Worksheet references cached to avoid repeated API lookups
- Row-index map cached alongside each DataFrame so writes can skip ws.find()
- Headers verified once per process (not per cache miss)
- Granular cache invalidation — only the affected sheet's key is cleared on writes
- Fixed double clear_cache() in update_kpi_value/delete_kpi (1 sync, not 2)
- Cache TTL configurable via config.CACHE_TTL_SECONDS (default 600s)
"""

import time
import threading
import gspread
from google.oauth2.service_account import Credentials
import pandas as pd

import config

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]

# ---------- Thread-safe cached client ----------

_client_lock = threading.Lock()
_client: gspread.Client | None = None
_spreadsheet: gspread.Spreadsheet | None = None

# TTL cache: key -> (timestamp, DataFrame)
_cache: dict[str, tuple[float, pd.DataFrame]] = {}
_cache_lock = threading.Lock()

# Row-index map: cache_key -> {record_id: sheet_row_number (1-indexed, header is row 1)}
_row_index: dict[str, dict[str, int]] = {}
_row_index_lock = threading.Lock()

# Worksheet reference cache: tab_name -> Worksheet
_ws_cache: dict[str, gspread.Worksheet] = {}
_ws_cache_lock = threading.Lock()

# Headers checked: set of tab_names that already had _ensure_headers run this process
_headers_checked: set[str] = set()
_headers_checked_lock = threading.Lock()


def _get_client() -> gspread.Client:
    global _client
    if _client is None:
        with _client_lock:
            if _client is None:
                creds = Credentials.from_service_account_info(
                    config.GCP_SERVICE_ACCOUNT, scopes=SCOPES
                )
                _client = gspread.authorize(creds)
    return _client


def _get_spreadsheet() -> gspread.Spreadsheet:
    global _spreadsheet
    if _spreadsheet is None:
        client = _get_client()
        _spreadsheet = client.open_by_key(config.SPREADSHEET_ID)
    return _spreadsheet


# ---------- Cache helpers ----------

def _cache_get(key: str) -> pd.DataFrame | None:
    with _cache_lock:
        if key in _cache:
            ts, df = _cache[key]
            if time.time() - ts < config.CACHE_TTL_SECONDS:
                return df  # Reads use filtering which creates new DFs
            del _cache[key]
    return None


def _cache_set(key: str, df: pd.DataFrame):
    with _cache_lock:
        _cache[key] = (time.time(), df.copy())  # Copy on write only


def _cache_invalidate(*keys: str):
    """Clear specific cache keys without nuking everything else."""
    with _cache_lock:
        for k in keys:
            _cache.pop(k, None)
    with _row_index_lock:
        for k in keys:
            _row_index.pop(k, None)


def _row_index_set(cache_key: str, id_to_row: dict[str, int]):
    with _row_index_lock:
        _row_index[cache_key] = id_to_row


def _row_index_get(cache_key: str) -> dict[str, int] | None:
    with _row_index_lock:
        return _row_index.get(cache_key)


def clear_cache():
    """Drop all cached sheet DATA, forcing the next read to hit Sheets.

    Deliberately keeps `_ws_cache` and `_headers_checked`: worksheet handles and
    "these headers are correct" are immutable for the life of the process, and
    re-establishing them costs one `ss.worksheet()` plus one `row_values(1)` per
    tab — 8 wasted round-trips every time someone clicks Refresh.
    """
    with _cache_lock:
        _cache.clear()
    with _row_index_lock:
        _row_index.clear()


def invalidate(*keys: str):
    """Public granular invalidation, for callers outside this module."""
    _cache_invalidate(*keys)


# ---------- Worksheet helpers ----------

def _get_or_create_worksheet(
    tab_name: str, headers: list[str], rows: int = 200, cols: int = 20
) -> gspread.Worksheet:
    # Check ws reference cache first
    with _ws_cache_lock:
        if tab_name in _ws_cache:
            ws = _ws_cache[tab_name]
        else:
            ws = None

    if ws is None:
        ss = _get_spreadsheet()
        try:
            ws = ss.worksheet(tab_name)
        except gspread.WorksheetNotFound:
            ws = ss.add_worksheet(title=tab_name, rows=rows, cols=cols)
            ws.append_row(headers, value_input_option="RAW")
            with _headers_checked_lock:
                _headers_checked.add(tab_name)
        with _ws_cache_lock:
            _ws_cache[tab_name] = ws

    # Only verify headers once per process (not per cache-miss)
    with _headers_checked_lock:
        already_checked = tab_name in _headers_checked
    if not already_checked:
        _ensure_headers(ws, headers)
        with _headers_checked_lock:
            _headers_checked.add(tab_name)

    return ws


def _ensure_headers(ws: gspread.Worksheet, headers: list[str]) -> None:
    """Additive header migration: append any expected header cells that are missing.

    Existing columns are never reordered or renamed — we only append new headers
    at the end. Safe for schema additions like adding `author` to KPI History.
    """
    existing = ws.row_values(1)
    if not existing:
        ws.append_row(headers, value_input_option="RAW")
        return
    missing = [h for h in headers if h not in existing]
    if not missing:
        return
    new_headers = list(existing) + missing
    ws.update("A1", [new_headers], value_input_option="RAW")


def _build_row_index(records: list[dict], id_field: str = "id") -> dict[str, int]:
    """Build {record_id: sheet_row_number} from a get_all_records result.
    Sheet row 1 is the header, so records start at row 2.
    """
    return {str(r.get(id_field, "")): i + 2 for i, r in enumerate(records)}


# ---------- Read ----------

def read_okrs(quarter: str) -> pd.DataFrame:
    cache_key = f"okrs:{quarter}"
    cached = _cache_get(cache_key)
    if cached is not None:
        return cached
    ws = _get_or_create_worksheet(config.okr_tab_name(quarter), config.OKR_COLUMNS)
    records = ws.get_all_records()
    if not records:
        df = pd.DataFrame(columns=config.OKR_COLUMNS)
    else:
        df = pd.DataFrame(records)
        if "category" not in df.columns:
            df["category"] = ""
        df["category"] = df["category"].fillna("").astype(str)
        df["progress"] = pd.to_numeric(df["progress"], errors="coerce").fillna(0)
    _cache_set(cache_key, df)
    _row_index_set(cache_key, _build_row_index(records))
    return df


def read_kpis(quarter: str) -> pd.DataFrame:
    cache_key = f"kpis:{quarter}"
    cached = _cache_get(cache_key)
    if cached is not None:
        return cached
    ws = _get_or_create_worksheet(config.kpi_tab_name(quarter), config.KPI_COLUMNS)
    records = ws.get_all_records()
    if not records:
        df = pd.DataFrame(columns=config.KPI_COLUMNS)
    else:
        df = pd.DataFrame(records)
        for col in ("current_value", "target_value", "baseline_value"):
            df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0)
        if "direction" not in df.columns:
            df["direction"] = "increase"
        df["direction"] = df["direction"].replace("", "increase").fillna("increase")
    _cache_set(cache_key, df)
    _row_index_set(cache_key, _build_row_index(records))
    return df


def read_kpi_history(quarter: str) -> pd.DataFrame:
    cache_key = f"kpi_history:{quarter}"
    cached = _cache_get(cache_key)
    if cached is not None:
        return cached
    tab = f"KPI History {quarter}"
    ws = _get_or_create_worksheet(tab, config.KPI_HISTORY_COLUMNS)
    records = ws.get_all_records()
    if not records:
        df = pd.DataFrame(columns=config.KPI_HISTORY_COLUMNS)
    else:
        df = pd.DataFrame(records)
        df["value"] = pd.to_numeric(df["value"], errors="coerce")
        # Pre-parse dates once at load time
        df["_parsed_date"] = pd.to_datetime(
            df["date"], format="mixed", dayfirst=False, errors="coerce"
        )
    _cache_set(cache_key, df)
    return df


def read_notes() -> pd.DataFrame:
    cache_key = "notes"
    cached = _cache_get(cache_key)
    if cached is not None:
        return cached
    ws = _get_or_create_worksheet(config.notes_tab_name(), config.NOTES_COLUMNS)
    records = ws.get_all_records()
    if not records:
        df = pd.DataFrame(columns=config.NOTES_COLUMNS)
    else:
        df = pd.DataFrame(records)
        # Pre-parse timestamps once at load time
        df["_parsed_ts"] = pd.to_datetime(
            df["timestamp"], format="mixed", dayfirst=False, errors="coerce"
        )
    _cache_set(cache_key, df)
    return df


def read_users() -> pd.DataFrame:
    """Cached read of the Users sheet."""
    cache_key = "users"
    cached = _cache_get(cache_key)
    if cached is not None:
        return cached
    ws = _get_or_create_worksheet(config.users_tab_name(), config.USER_COLUMNS)
    records = ws.get_all_records()
    if not records:
        df = pd.DataFrame(columns=config.USER_COLUMNS)
    else:
        df = pd.DataFrame(records)
        for col in config.USER_COLUMNS:
            if col not in df.columns:
                df[col] = ""
        df = df.fillna("")
    _cache_set(cache_key, df)
    _row_index_set(cache_key, _build_row_index(records, id_field="email"))
    return df


# ---------- Write ----------

def _find_row_or_lookup(cache_key: str, ws: gspread.Worksheet, record_id: str) -> int | None:
    """Look up sheet row number from the cached row-index map. Falls back to ws.find()
    if the index is missing or stale. Returns None if not found.
    """
    idx = _row_index_get(cache_key)
    if idx is not None:
        row = idx.get(str(record_id))
        if row is not None:
            return row
    # Fallback: cache miss or stale — do the slow find
    cell = ws.find(str(record_id), in_column=1)
    return cell.row if cell else None


def _sync_okr_progress(quarter: str, okr_id: str, kpis_df, updated_at: str):
    """Recompute and write OKR progress without re-reading the KPIs sheet."""
    from data import okr_progress_from_krs
    progress = okr_progress_from_krs(okr_id, kpis_df)
    okr_cache_key = f"okrs:{quarter}"
    ws = _get_or_create_worksheet(config.okr_tab_name(quarter), config.OKR_COLUMNS)
    row_num = _find_row_or_lookup(okr_cache_key, ws, okr_id)
    if row_num is None:
        return
    row_values = ws.row_values(row_num)
    while len(row_values) < len(config.OKR_COLUMNS):
        row_values.append("")
    row_values[config.OKR_COLUMNS.index("progress")] = progress
    row_values[config.OKR_COLUMNS.index("last_updated")] = updated_at
    ws.update(f"A{row_num}", [row_values], value_input_option="USER_ENTERED")


def update_kpi_value(
    quarter: str, kpi_id: str, okr_id: str, value: float,
    updated_at: str, author: str = "",
):
    kpi_cache_key = f"kpis:{quarter}"
    ws = _get_or_create_worksheet(config.kpi_tab_name(quarter), config.KPI_COLUMNS)
    row_num = _find_row_or_lookup(kpi_cache_key, ws, kpi_id)
    if row_num is None:
        raise ValueError(f"Key Result id '{kpi_id}' not found")
    row_values = ws.row_values(row_num)
    while len(row_values) < len(config.KPI_COLUMNS):
        row_values.append("")
    row_values[config.KPI_COLUMNS.index("current_value")] = value
    row_values[config.KPI_COLUMNS.index("last_updated")] = updated_at
    ws.update(f"A{row_num}", [row_values], value_input_option="USER_ENTERED")

    # Append history (with author when provided)
    history_tab = f"KPI History {quarter}"
    hws = _get_or_create_worksheet(history_tab, config.KPI_HISTORY_COLUMNS)
    hws.append_row(
        [kpi_id, updated_at, value, author],
        value_input_option="USER_ENTERED",
    )

    # Invalidate KPI + history cache (NOT the row-index — the row didn't move),
    # then re-read once and sync OKR progress. Single read instead of double.
    _cache_invalidate(kpi_cache_key, f"kpi_history:{quarter}")
    fresh_kpis = read_kpis(quarter)
    _sync_okr_progress(quarter, okr_id, fresh_kpis, updated_at)
    # OKR row was updated — clear OKR cache too
    _cache_invalidate(f"okrs:{quarter}")


def add_note(parent_type: str, parent_id: str, author: str, text: str, timestamp: str):
    ws = _get_or_create_worksheet(config.notes_tab_name(), config.NOTES_COLUMNS)
    ws.append_row(
        [parent_type, parent_id, timestamp, author, text],
        value_input_option="USER_ENTERED",
    )
    _cache_invalidate("notes")


def update_note(parent_type: str, parent_id: str, timestamp: str, author: str, new_text: str):
    ws = _get_or_create_worksheet(config.notes_tab_name(), config.NOTES_COLUMNS)
    all_rows = ws.get_all_values()
    for i, row_vals in enumerate(all_rows[1:], start=2):
        if (len(row_vals) >= 5
            and row_vals[0] == parent_type
            and row_vals[1] == str(parent_id)
            and row_vals[2] == str(timestamp)
            and row_vals[3] == str(author)):
            row_vals[4] = new_text
            ws.update(f"A{i}", [row_vals], value_input_option="USER_ENTERED")
            break
    _cache_invalidate("notes")


def add_okr(quarter: str, row: list):
    ws = _get_or_create_worksheet(config.okr_tab_name(quarter), config.OKR_COLUMNS)
    ws.append_row(row, value_input_option="USER_ENTERED")
    _cache_invalidate(f"okrs:{quarter}")


def add_kpi(quarter: str, row: list):
    ws = _get_or_create_worksheet(config.kpi_tab_name(quarter), config.KPI_COLUMNS)
    ws.append_row(row, value_input_option="USER_ENTERED")
    _cache_invalidate(f"kpis:{quarter}")


def update_okr_fields(quarter: str, okr_id: str, fields: dict):
    cache_key = f"okrs:{quarter}"
    ws = _get_or_create_worksheet(config.okr_tab_name(quarter), config.OKR_COLUMNS)
    row_num = _find_row_or_lookup(cache_key, ws, okr_id)
    if row_num is None:
        raise ValueError(f"OKR id '{okr_id}' not found")
    row_values = ws.row_values(row_num)
    while len(row_values) < len(config.OKR_COLUMNS):
        row_values.append("")
    for col_name, value in fields.items():
        col_idx = config.OKR_COLUMNS.index(col_name)
        row_values[col_idx] = value
    ws.update(f"A{row_num}", [row_values], value_input_option="USER_ENTERED")
    _cache_invalidate(cache_key)


def update_kpi_fields(quarter: str, kpi_id: str, fields: dict):
    cache_key = f"kpis:{quarter}"
    ws = _get_or_create_worksheet(config.kpi_tab_name(quarter), config.KPI_COLUMNS)
    row_num = _find_row_or_lookup(cache_key, ws, kpi_id)
    if row_num is None:
        raise ValueError(f"Key Result id '{kpi_id}' not found")
    row_values = ws.row_values(row_num)
    while len(row_values) < len(config.KPI_COLUMNS):
        row_values.append("")
    for col_name, value in fields.items():
        col_idx = config.KPI_COLUMNS.index(col_name)
        row_values[col_idx] = value
    ws.update(f"A{row_num}", [row_values], value_input_option="USER_ENTERED")
    _cache_invalidate(cache_key)


# ---------- Move ----------

def move_okr(old_quarter: str, new_quarter: str, okr_id: str):
    okr_cache_key_old = f"okrs:{old_quarter}"
    okr_ws = _get_or_create_worksheet(config.okr_tab_name(old_quarter), config.OKR_COLUMNS)
    row_num = _find_row_or_lookup(okr_cache_key_old, okr_ws, okr_id)
    if row_num is None:
        raise ValueError(f"OKR id '{okr_id}' not found in {old_quarter}")
    okr_row = okr_ws.row_values(row_num)
    while len(okr_row) < len(config.OKR_COLUMNS):
        okr_row.append("")

    kpi_ws = _get_or_create_worksheet(config.kpi_tab_name(old_quarter), config.KPI_COLUMNS)
    all_kpis = kpi_ws.get_all_values()
    okr_id_col = config.KPI_COLUMNS.index("okr_id")
    kr_rows, kr_ids, kr_row_nums = [], [], []
    if len(all_kpis) > 1:
        for i, row_vals in enumerate(all_kpis[1:], start=2):
            if len(row_vals) > okr_id_col and str(row_vals[okr_id_col]) == str(okr_id):
                kr_rows.append(row_vals)
                kr_ids.append(str(row_vals[0]))
                kr_row_nums.append(i)

    hist_ws = _get_or_create_worksheet(f"KPI History {old_quarter}", config.KPI_HISTORY_COLUMNS)
    all_hist = hist_ws.get_all_values()
    kr_id_set = set(kr_ids)
    hist_rows, hist_row_nums = [], []
    if len(all_hist) > 1:
        for i, row_vals in enumerate(all_hist[1:], start=2):
            if len(row_vals) > 0 and str(row_vals[0]) in kr_id_set:
                hist_rows.append(row_vals)
                hist_row_nums.append(i)

    # Write to new quarter
    new_okr_ws = _get_or_create_worksheet(config.okr_tab_name(new_quarter), config.OKR_COLUMNS)
    new_okr_ws.append_row(okr_row, value_input_option="USER_ENTERED")

    if kr_rows:
        new_kpi_ws = _get_or_create_worksheet(config.kpi_tab_name(new_quarter), config.KPI_COLUMNS)
        new_kpi_ws.append_rows(kr_rows, value_input_option="USER_ENTERED")

    if hist_rows:
        new_hist_ws = _get_or_create_worksheet(f"KPI History {new_quarter}", config.KPI_HISTORY_COLUMNS)
        new_hist_ws.append_rows(hist_rows, value_input_option="USER_ENTERED")

    # Delete from old quarter (reverse order to preserve row indices)
    # Use row numbers we already collected — no need to re-read
    for row_num_h in reversed(hist_row_nums):
        hist_ws.delete_rows(row_num_h)

    for row_num_k in reversed(kr_row_nums):
        kpi_ws.delete_rows(row_num_k)

    okr_ws.delete_rows(row_num)
    _cache_invalidate(
        f"okrs:{old_quarter}", f"okrs:{new_quarter}",
        f"kpis:{old_quarter}", f"kpis:{new_quarter}",
        f"kpi_history:{old_quarter}", f"kpi_history:{new_quarter}",
    )


# ---------- Delete ----------

def delete_okr(quarter: str, okr_id: str):
    okr_cache_key = f"okrs:{quarter}"
    kpi_cache_key = f"kpis:{quarter}"
    ws = _get_or_create_worksheet(config.okr_tab_name(quarter), config.OKR_COLUMNS)
    row_num = _find_row_or_lookup(okr_cache_key, ws, okr_id)
    if row_num is not None:
        ws.delete_rows(row_num)

    kpi_ws = _get_or_create_worksheet(config.kpi_tab_name(quarter), config.KPI_COLUMNS)
    all_values = kpi_ws.get_all_values()
    if len(all_values) > 1:
        okr_id_col = config.KPI_COLUMNS.index("okr_id")
        rows_to_delete = [
            i for i, row_vals in enumerate(all_values[1:], start=2)
            if len(row_vals) > okr_id_col and str(row_vals[okr_id_col]) == str(okr_id)
        ]
        for row_num_k in reversed(rows_to_delete):
            kpi_ws.delete_rows(row_num_k)
    _cache_invalidate(okr_cache_key, kpi_cache_key, f"kpi_history:{quarter}")


def delete_kpi(quarter: str, kpi_id: str, okr_id: str):
    kpi_cache_key = f"kpis:{quarter}"
    ws = _get_or_create_worksheet(config.kpi_tab_name(quarter), config.KPI_COLUMNS)
    row_num = _find_row_or_lookup(kpi_cache_key, ws, kpi_id)
    if row_num is not None:
        ws.delete_rows(row_num)

    # Single invalidation + re-read + sync (instead of double clear_cache)
    _cache_invalidate(kpi_cache_key)
    from datetime import datetime
    now = datetime.now().strftime("%m/%d/%Y %H:%M")
    fresh_kpis = read_kpis(quarter)
    _sync_okr_progress(quarter, okr_id, fresh_kpis, now)
    _cache_invalidate(f"okrs:{quarter}")

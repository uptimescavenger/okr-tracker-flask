"""
Google Sheets integration layer using gspread.
All reads/writes go through this module.
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

# Simple TTL cache for sheet reads
_cache: dict[str, tuple[float, pd.DataFrame]] = {}
_cache_lock = threading.Lock()


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


def _invalidate_spreadsheet():
    """Force re-open of spreadsheet on next access (e.g. after auth refresh)."""
    global _spreadsheet
    _spreadsheet = None


# ---------- Cache helpers ----------

def _cache_get(key: str) -> pd.DataFrame | None:
    with _cache_lock:
        if key in _cache:
            ts, df = _cache[key]
            if time.time() - ts < config.CACHE_TTL_SECONDS:
                return df.copy()
            del _cache[key]
    return None


def _cache_set(key: str, df: pd.DataFrame):
    with _cache_lock:
        _cache[key] = (time.time(), df.copy())


def clear_cache():
    with _cache_lock:
        _cache.clear()


# ---------- Worksheet helpers ----------

def _get_or_create_worksheet(
    tab_name: str, headers: list[str], rows: int = 200, cols: int = 20
) -> gspread.Worksheet:
    ss = _get_spreadsheet()
    try:
        ws = ss.worksheet(tab_name)
        existing_headers = ws.row_values(1)
        if len(existing_headers) < len(headers):
            ws.update("A1", [headers], value_input_option="RAW")
    except gspread.WorksheetNotFound:
        ws = ss.add_worksheet(title=tab_name, rows=rows, cols=cols)
        ws.append_row(headers, value_input_option="RAW")
    return ws


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
    _cache_set(cache_key, df)
    return df


# ---------- Write ----------

def _sync_okr_progress(quarter: str, okr_id: str, kpis_df, updated_at: str):
    from data import okr_progress_from_krs
    progress = okr_progress_from_krs(okr_id, kpis_df)
    ws = _get_or_create_worksheet(config.okr_tab_name(quarter), config.OKR_COLUMNS)
    cell = ws.find(str(okr_id), in_column=1)
    if cell is None:
        return
    row_values = ws.row_values(cell.row)
    while len(row_values) < len(config.OKR_COLUMNS):
        row_values.append("")
    row_values[config.OKR_COLUMNS.index("progress")] = progress
    row_values[config.OKR_COLUMNS.index("last_updated")] = updated_at
    ws.update(f"A{cell.row}", [row_values], value_input_option="USER_ENTERED")


def update_kpi_value(quarter: str, kpi_id: str, okr_id: str, value: float, updated_at: str):
    ws = _get_or_create_worksheet(config.kpi_tab_name(quarter), config.KPI_COLUMNS)
    cell = ws.find(str(kpi_id), in_column=1)
    if cell is None:
        raise ValueError(f"Key Result id '{kpi_id}' not found")
    row_values = ws.row_values(cell.row)
    while len(row_values) < len(config.KPI_COLUMNS):
        row_values.append("")
    row_values[config.KPI_COLUMNS.index("current_value")] = value
    row_values[config.KPI_COLUMNS.index("last_updated")] = updated_at
    ws.update(f"A{cell.row}", [row_values], value_input_option="USER_ENTERED")

    history_tab = f"KPI History {quarter}"
    hws = _get_or_create_worksheet(history_tab, config.KPI_HISTORY_COLUMNS)
    hws.append_row([kpi_id, updated_at, value], value_input_option="USER_ENTERED")

    clear_cache()
    fresh_kpis = read_kpis(quarter)
    _sync_okr_progress(quarter, okr_id, fresh_kpis, updated_at)
    clear_cache()


def add_note(parent_type: str, parent_id: str, author: str, text: str, timestamp: str):
    ws = _get_or_create_worksheet(config.notes_tab_name(), config.NOTES_COLUMNS)
    ws.append_row(
        [parent_type, parent_id, timestamp, author, text],
        value_input_option="USER_ENTERED",
    )
    clear_cache()


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
    clear_cache()


def add_okr(quarter: str, row: list):
    ws = _get_or_create_worksheet(config.okr_tab_name(quarter), config.OKR_COLUMNS)
    ws.append_row(row, value_input_option="USER_ENTERED")
    clear_cache()


def add_kpi(quarter: str, row: list):
    ws = _get_or_create_worksheet(config.kpi_tab_name(quarter), config.KPI_COLUMNS)
    ws.append_row(row, value_input_option="USER_ENTERED")
    clear_cache()


def update_okr_fields(quarter: str, okr_id: str, fields: dict):
    ws = _get_or_create_worksheet(config.okr_tab_name(quarter), config.OKR_COLUMNS)
    cell = ws.find(str(okr_id), in_column=1)
    if cell is None:
        raise ValueError(f"OKR id '{okr_id}' not found")
    row_values = ws.row_values(cell.row)
    while len(row_values) < len(config.OKR_COLUMNS):
        row_values.append("")
    for col_name, value in fields.items():
        col_idx = config.OKR_COLUMNS.index(col_name)
        row_values[col_idx] = value
    ws.update(f"A{cell.row}", [row_values], value_input_option="USER_ENTERED")
    clear_cache()


def update_kpi_fields(quarter: str, kpi_id: str, fields: dict):
    ws = _get_or_create_worksheet(config.kpi_tab_name(quarter), config.KPI_COLUMNS)
    cell = ws.find(str(kpi_id), in_column=1)
    if cell is None:
        raise ValueError(f"Key Result id '{kpi_id}' not found")
    row_values = ws.row_values(cell.row)
    while len(row_values) < len(config.KPI_COLUMNS):
        row_values.append("")
    for col_name, value in fields.items():
        col_idx = config.KPI_COLUMNS.index(col_name)
        row_values[col_idx] = value
    ws.update(f"A{cell.row}", [row_values], value_input_option="USER_ENTERED")
    clear_cache()


# ---------- Move ----------

def move_okr(old_quarter: str, new_quarter: str, okr_id: str):
    okr_ws = _get_or_create_worksheet(config.okr_tab_name(old_quarter), config.OKR_COLUMNS)
    cell = okr_ws.find(str(okr_id), in_column=1)
    if cell is None:
        raise ValueError(f"OKR id '{okr_id}' not found in {old_quarter}")
    okr_row = okr_ws.row_values(cell.row)
    while len(okr_row) < len(config.OKR_COLUMNS):
        okr_row.append("")

    kpi_ws = _get_or_create_worksheet(config.kpi_tab_name(old_quarter), config.KPI_COLUMNS)
    all_kpis = kpi_ws.get_all_values()
    okr_id_col = config.KPI_COLUMNS.index("okr_id")
    kr_rows, kr_ids = [], []
    if len(all_kpis) > 1:
        for row_vals in all_kpis[1:]:
            if len(row_vals) > okr_id_col and str(row_vals[okr_id_col]) == str(okr_id):
                kr_rows.append(row_vals)
                kr_ids.append(str(row_vals[0]))

    hist_ws = _get_or_create_worksheet(f"KPI History {old_quarter}", config.KPI_HISTORY_COLUMNS)
    all_hist = hist_ws.get_all_values()
    hist_rows = []
    if len(all_hist) > 1:
        for row_vals in all_hist[1:]:
            if len(row_vals) > 0 and str(row_vals[0]) in kr_ids:
                hist_rows.append(row_vals)

    new_okr_ws = _get_or_create_worksheet(config.okr_tab_name(new_quarter), config.OKR_COLUMNS)
    new_okr_ws.append_row(okr_row, value_input_option="USER_ENTERED")

    if kr_rows:
        new_kpi_ws = _get_or_create_worksheet(config.kpi_tab_name(new_quarter), config.KPI_COLUMNS)
        new_kpi_ws.append_rows(kr_rows, value_input_option="USER_ENTERED")

    if hist_rows:
        new_hist_ws = _get_or_create_worksheet(f"KPI History {new_quarter}", config.KPI_HISTORY_COLUMNS)
        new_hist_ws.append_rows(hist_rows, value_input_option="USER_ENTERED")

    # Delete from old quarter
    if hist_rows:
        all_hist_fresh = hist_ws.get_all_values()
        rows_to_del = []
        for i, row_vals in enumerate(all_hist_fresh[1:], start=2):
            if len(row_vals) > 0 and str(row_vals[0]) in kr_ids:
                rows_to_del.append(i)
        for row_num in reversed(rows_to_del):
            hist_ws.delete_rows(row_num)

    if kr_rows:
        all_kpis_fresh = kpi_ws.get_all_values()
        rows_to_del = []
        for i, row_vals in enumerate(all_kpis_fresh[1:], start=2):
            if len(row_vals) > okr_id_col and str(row_vals[okr_id_col]) == str(okr_id):
                rows_to_del.append(i)
        for row_num in reversed(rows_to_del):
            kpi_ws.delete_rows(row_num)

    okr_ws.delete_rows(cell.row)
    clear_cache()


# ---------- Delete ----------

def delete_okr(quarter: str, okr_id: str):
    ws = _get_or_create_worksheet(config.okr_tab_name(quarter), config.OKR_COLUMNS)
    cell = ws.find(str(okr_id), in_column=1)
    if cell:
        ws.delete_rows(cell.row)

    kpi_ws = _get_or_create_worksheet(config.kpi_tab_name(quarter), config.KPI_COLUMNS)
    all_values = kpi_ws.get_all_values()
    if len(all_values) > 1:
        okr_id_col = config.KPI_COLUMNS.index("okr_id")
        rows_to_delete = []
        for i, row_vals in enumerate(all_values[1:], start=2):
            if len(row_vals) > okr_id_col and str(row_vals[okr_id_col]) == str(okr_id):
                rows_to_delete.append(i)
        for row_num in reversed(rows_to_delete):
            kpi_ws.delete_rows(row_num)
    clear_cache()


def delete_kpi(quarter: str, kpi_id: str, okr_id: str):
    ws = _get_or_create_worksheet(config.kpi_tab_name(quarter), config.KPI_COLUMNS)
    cell = ws.find(str(kpi_id), in_column=1)
    if cell:
        ws.delete_rows(cell.row)

    clear_cache()
    from datetime import datetime
    now = datetime.now().strftime("%m/%d/%Y %H:%M")
    fresh_kpis = read_kpis(quarter)
    _sync_okr_progress(quarter, okr_id, fresh_kpis, now)
    clear_cache()

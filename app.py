# ============================================================
# УЧЁТ РЕЙСОВ И ПЕРЕВОЗОК — v2.17.9
# Streamlit + Google Sheets
# v2.17.9: функционал и структура v2.17.7 сохранены;
#          добавлена защита от временных ошибок/квот Google Sheets (429/5xx);
#          убрано лишнее принудительное чтение users при каждом запуске;
#          ошибки Google API теперь показываются понятным сообщением без traceback.
# ============================================================

import streamlit as st
import gspread
from oauth2client.service_account import ServiceAccountCredentials
from datetime import datetime, timedelta
import hashlib
import hmac
import uuid
import os
import time
from pathlib import Path
import re
import html
import pandas as pd
import altair as alt

SHEET_ID = "1MHslz3VRowoOLtS_AAQ5h-tFm-fIXBN8noqtyQpmKLw"
CRED_FILE = "gifted-mountain-508410-s3-96f38b5a0f63.json"

SCOPES = [
    "https://spreadsheets.google.com/feeds",
    "https://www.googleapis.com/auth/drive",
]

SHEET_SCHEMAS = {
    "users": ["login", "password_hash", "role", "full_name", "active",
              "failed_attempts", "locked_until"],
    # A=id, B=tractor_number, C=driver, D=route, E=date_departure, F=date_return,
    # G=created_by, H=created_at, I=archived, J=completed, K=completed_at,
    # L=invoice_number, M=invoice_date, N=deleted_at, O=loading_from,
    # P=payment_date, Q=payment_comment,
    # R=pay1_amount, S=pay1_date, T=pay1_comment,
    # U=pay2_amount, V=pay2_date, W=pay2_comment,
    # X=pay3_amount, Y=pay3_date, Z=pay3_comment,
    # AA=loading_contract_number, AB=trip_number
    "trips": ["id", "tractor_number", "driver", "route",
              "date_departure", "date_return", "created_by", "created_at",
              "archived", "completed", "completed_at",
              "invoice_number", "invoice_date", "deleted_at",
              "loading_from",
              "payment_date", "payment_comment",
              "pay1_amount", "pay1_date", "pay1_comment",
              "pay2_amount", "pay2_date", "pay2_comment",
              "pay3_amount", "pay3_date", "pay3_comment",
              "loading_contract_number", "trip_number"],
    "shipments": ["id", "trip_id", "position", "car_model", "client",
                  "amount", "date_pay", "paid_to",
                  "delivery_city", "vin",
                  "advance", "advance_date",
                  "payer_type", "customer", "contract_number",
                  "nds_amount", "amount_no_nds",
                  "created_by", "created_at", "issued", "issued_ever",
                  "paid", "archived", "paid_amount",
                  "invoice_number", "invoice_date",
                  "transferred_to_trip", "transferred_at",
                  "transferred_from_trip",
                  "advance_2", "advance_date_2", "paid_to_2",
                  "car_pay1_amount", "car_pay1_date", "car_pay1_to", "car_pay1_comment",
                  "car_pay2_amount", "car_pay2_date", "car_pay2_to", "car_pay2_comment",
                  "car_pay3_amount", "car_pay3_date", "car_pay3_to", "car_pay3_comment"],
    "transfer_history": ["id", "ts", "shipment_id", "from_trip", "to_trip",
                         "from_position", "to_position", "user_login", "note",
                         "cancelled"],
    "audit_log": ["id", "ts", "login", "role", "action", "details"],
    "act_log": ["id", "ts", "login", "trip_id", "shipment_id", "client"],
}

ROLES = ["admin", "director", "logist", "dispatcher"]
ROLE_LABELS = {
    "admin": "Администратор",
    "director": "Директор",
    "logist": "Логист",
    "dispatcher": "Диспетчер",
}
VIEW_LABELS = {
    "active": "🚛 Активные рейсы",
    "completed": "✅ Завершённые рейсы",
    "archive": "📦 Архив",
}
PAYER_TYPES = ["нал", "эквайринг", "безнал с НДС 22%"]
NDS_RATE = 0.22
NDS_PAYER = "безнал с НДС 22%"
MAX_CARS = 8
CACHE_TTL = 300
LOGIN_MAX_ATTEMPTS = 5
LOGIN_LOCK_MINUTES = 15

MAX_PAY_SPLITS = 3
PAY_SPLIT_LABELS = ["аванс", "доплата", "остаток"]


# ============================================================
# УТИЛИТЫ
# ============================================================

def s(x):
    if x is None:
        return ""
    return str(x)


def to_float(x):
    if x is None:
        return 0.0
    if isinstance(x, (int, float)):
        return float(x)
    try:
        t = str(x).strip()
    except Exception:
        return 0.0
    if not t:
        return 0.0
    t = t.replace(" ", "").replace("\u00a0", "").replace("\u202f", "")
    if "." in t and "," in t:
        t = t.replace(".", "").replace(",", ".")
    else:
        t = t.replace(",", ".")
    try:
        return float(t)
    except ValueError:
        return 0.0


def money_value(x):
    return round(to_float(x), 2)


def fmt_money(x):
    v = to_float(x)
    ss = "{:,.2f}".format(v)
    ss = ss.replace(",", " ")
    ss = ss.replace(".", ",")
    return ss


def parse_date_ui(x):
    if x is None:
        return ""
    t = str(x).strip()
    if not t:
        return ""
    for fmt in ("%d.%m.%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(t, fmt).strftime("%d.%m.%Y")
        except ValueError:
            continue
    raise ValueError("Неверный формат даты: «" + t + "». Введите ДД.ММ.ГГГГ (например 12.05.2026)")


def date_to_display_safe(x):
    if x is None:
        return ""
    t = str(x).strip()
    if not t:
        return ""
    for fmt in ("%d.%m.%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(t, fmt).strftime("%d.%m.%Y")
        except ValueError:
            continue
    return t


def date_sort_key(x):
    if x is None:
        return datetime.min.date()
    t = str(x).strip()
    if not t:
        return datetime.min.date()
    for fmt in ("%d.%m.%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(t, fmt).date()
        except ValueError:
            continue
    return datetime.min.date()


def looks_like_datetime(v):
    t = s(v).strip()
    if not t:
        return False
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d", "%d.%m.%Y", "%d.%m.%Y %H:%M:%S"):
        try:
            datetime.strptime(t, fmt)
            return True
        except ValueError:
            continue
    return False


def new_uuid(prefix=""):
    return (prefix + str(uuid.uuid4())[:8]).upper()


def is_active(u):
    return s(u.get("active")).strip().lower() in ("1", "1.0", "true")


def is_archived(v):
    return s(v).strip().lower() in ("1", "1.0", "true")


def check_paid(v):
    return s(v).strip().lower() in ("1", "1.0", "true", "да", "yes")


def check_issued(v):
    return s(v).strip().lower() in ("1", "1.0", "true", "да", "yes")


def check_completed(v):
    return s(v).strip().lower() in ("1", "1.0", "true", "да", "yes")


def is_deleted(v):
    t = s(v).strip()
    if not t:
        return False
    if looks_like_datetime(t):
        return True
    return False


def safe_df(rows):
    if not rows:
        return rows
    return [{k: ("" if v is None else str(v)) for k, v in r.items()} for r in rows]


# ============================================================
# ПАРОЛИ
# ============================================================

try:
    import bcrypt
    HAS_BCRYPT = True
except ImportError:
    HAS_BCRYPT = False


def hash_password(p):
    if HAS_BCRYPT:
        return bcrypt.hashpw(p.encode("utf-8"), bcrypt.gensalt(rounds=10)).decode("utf-8")
    return hashlib.sha256(p.encode("utf-8")).hexdigest()


def verify_password(p, stored_hash):
    if not stored_hash:
        return False
    stored_hash = s(stored_hash)
    if stored_hash.startswith(("$2a$", "$2b$", "$2y$")):
        if not HAS_BCRYPT:
            return False
        try:
            return bcrypt.checkpw(p.encode("utf-8"), stored_hash.encode("utf-8"))
        except Exception:
            return False
    old_hash = hashlib.sha256(p.encode("utf-8")).hexdigest()
    return hmac.compare_digest(old_hash, stored_hash)


def needs_password_upgrade(stored_hash):
    if not stored_hash:
        return False
    return not s(stored_hash).startswith(("$2a$", "$2b$", "$2y$"))


def check_lockout(u):
    locked_until = s(u.get("locked_until", "")).strip()
    if not locked_until:
        return None
    try:
        dt = datetime.strptime(locked_until, "%Y-%m-%d %H:%M:%S")
        if dt > datetime.now():
            return dt
    except Exception:
        pass
    return None


# ============================================================
# GOOGLE SHEETS
# ============================================================


def _resolve_credentials_file():
    """Ищет JSON-ключ Google независимо от папки запуска Streamlit."""
    candidates = []

    def add(path):
        try:
            path = Path(path).expanduser()
        except Exception:
            return
        if path not in candidates:
            candidates.append(path)

    try:
        script_dir = Path(__file__).resolve().parent
        add(script_dir / CRED_FILE)
        add(script_dir.parent / CRED_FILE)
    except Exception:
        pass

    try:
        cwd = Path.cwd()
        add(cwd / CRED_FILE)
        add(cwd.parent / CRED_FILE)
    except Exception:
        pass

    home = Path.home()
    desktop_dirs = [home / "Desktop", home / "OneDrive" / "Desktop"]
    add(home / CRED_FILE)
    for desktop in desktop_dirs:
        add(desktop / CRED_FILE)
        if desktop.exists():
            try:
                for found in desktop.glob("*/" + CRED_FILE):
                    add(found)
            except Exception:
                pass

    for candidate in candidates:
        try:
            if candidate.is_file():
                return str(candidate), [str(x) for x in candidates]
        except Exception:
            continue
    return "", [str(x) for x in candidates]


@st.cache_resource
def get_book():
    cred_path, searched = _resolve_credentials_file()
    if cred_path:
        creds = ServiceAccountCredentials.from_json_keyfile_name(cred_path, SCOPES)
    else:
        try:
            creds_dict = dict(st.secrets["gcp_service_account"])
        except Exception:
            searched_text = "\n".join("- " + x for x in searched)
            raise FileNotFoundError(
                "Не найден JSON-ключ Google Sheets: " + CRED_FILE + ".\n"
                "Скопируйте этот файл в ту же папку, где находится app.py.\n"
                "Проверены пути:\n" + searched_text
            )
        creds = ServiceAccountCredentials.from_json_keyfile_dict(creds_dict, SCOPES)
    client = gspread.authorize(creds)
    return client.open_by_key(SHEET_ID)


@st.cache_resource
def get_ws_cached(name):
    return get_book().worksheet(name)


def _gspread_status_code(exc):
    """Безопасно извлекает HTTP-код из gspread APIError."""
    response = getattr(exc, "response", None)
    try:
        return int(getattr(response, "status_code", 0) or 0)
    except Exception:
        return 0


def _gspread_reason(exc):
    """Короткая безопасная причина без токенов/учётных данных."""
    err = getattr(exc, "error", None)
    if isinstance(err, dict):
        status = s(err.get("status", "")).strip()
        message = s(err.get("message", "")).strip()
        if status and message:
            return status + ": " + message
        if status:
            return status
        if message:
            return message
    return ""


def _google_api_help_text(exc, operation="чтение Google Sheets"):
    code = _gspread_status_code(exc)
    reason = _gspread_reason(exc)
    if code == 429:
        text = (
            "Google Sheets временно ограничил количество запросов (HTTP 429). "
            "Приложение уже выполнило автоматические повторы. "
            "Обновите страницу через несколько секунд; данные не повреждены."
        )
    elif code == 403:
        text = (
            "Google отклонил доступ (HTTP 403). Проверьте, что таблица открыта "
            "для service account из JSON/secrets и что Google Sheets API включён."
        )
    elif code == 404:
        text = (
            "Google не нашёл таблицу или лист (HTTP 404). Проверьте SHEET_ID и названия листов."
        )
    elif code in (500, 502, 503, 504):
        text = (
            "Временная ошибка Google Sheets (HTTP " + s(code) + "). "
            "Приложение выполнило автоматические повторы; попробуйте обновить страницу."
        )
    elif code:
        text = "Google Sheets вернул HTTP " + s(code) + " при операции: " + operation + "."
    else:
        text = "Google Sheets не выполнил операцию: " + operation + "."
    if reason:
        text += " Причина: " + reason
    return text


def _gspread_call_with_retry(func, operation="Google Sheets", attempts=5):
    """Повторяет только временные API-ошибки; 403/404 не маскирует."""
    delays = [1.0, 2.0, 4.0, 7.0]
    last_exc = None
    for attempt in range(max(1, attempts)):
        try:
            return func()
        except gspread.exceptions.APIError as exc:
            last_exc = exc
            code = _gspread_status_code(exc)
            retryable = code in (429, 500, 502, 503, 504) or code == 0
            if not retryable or attempt >= attempts - 1:
                raise
            time.sleep(delays[min(attempt, len(delays) - 1)])
    if last_exc is not None:
        raise last_exc


@st.cache_data(ttl=CACHE_TTL, show_spinner=False)
def read_all_cached(name):
    ws = get_ws_cached(name)
    return _gspread_call_with_retry(
        lambda: ws.get_all_records(),
        operation="чтение листа " + s(name),
        attempts=5,
    )


def read_all(name, fresh=False):
    if fresh:
        read_all_cached.clear(name)
    return read_all_cached(name)


def invalidate_cache(name=None):
    if name:
        read_all_cached.clear(name)
    else:
        read_all_cached.clear()
    try:
        _id_to_row_index.clear()
    except Exception:
        pass


@st.cache_data(ttl=300, show_spinner=False)
def _id_to_row_index(name):
    rows = read_all_cached(name)
    result = {}
    for idx, r in enumerate(rows):
        rid = s(r.get("id"))
        if rid:
            result[rid] = idx + 2
    return result


def _find_row_index_by_id(name, row_id):
    return _id_to_row_index(name).get(s(row_id))


def _migrate_trips_n_to_o(ws):
    """Сохраняет старую миграцию и один раз назначает номера старым рейсам.

    trip_number хранится в конце листа trips, поэтому существующие колонки
    не сдвигаются. Уже назначенные номера никогда не перенумеровываются.
    """
    try:
        all_vals = ws.get_all_values()
        if len(all_vals) < 2:
            return
        updates = []
        for i, row in enumerate(all_vals[1:], start=2):
            n_val = s(row[13]).strip() if len(row) > 13 else ""
            o_val = s(row[14]).strip() if len(row) > 14 else ""
            if n_val and not looks_like_datetime(n_val) and not o_val:
                updates.append({"range": "O" + str(i), "values": [[n_val]]})
                updates.append({"range": "N" + str(i), "values": [[""]]})

        # v2.17.9 — порядковый номер рейса.
        # Для существующих строк заполняем только пустые номера, в порядке строк листа.
        headers = [s(v).strip() for v in all_vals[0]]
        if "trip_number" in headers:
            num_idx = headers.index("trip_number")
            used = set()
            for row in all_vals[1:]:
                raw = s(row[num_idx]).strip() if len(row) > num_idx else ""
                if raw:
                    try:
                        n = int(to_float(raw))
                        if n > 0:
                            used.add(n)
                    except Exception:
                        pass

            next_no = 1
            col_no = num_idx + 1
            col_letters = ""
            ncol = col_no
            while ncol:
                ncol, rem = divmod(ncol - 1, 26)
                col_letters = chr(ord("A") + rem) + col_letters

            for row_no, row in enumerate(all_vals[1:], start=2):
                raw = s(row[num_idx]).strip() if len(row) > num_idx else ""
                if raw:
                    continue
                # Пустые технические строки без id не нумеруем.
                row_id = s(row[0]).strip() if row else ""
                if not row_id:
                    continue
                while next_no in used:
                    next_no += 1
                updates.append({
                    "range": col_letters + str(row_no),
                    "values": [[next_no]],
                })
                used.add(next_no)
                next_no += 1

        if updates:
            ws.batch_update(updates, value_input_option="USER_ENTERED")
            invalidate_cache("trips")
    except Exception as e:
        _log_system_error("migrate_trips_n_to_o", "trips", e)


def ensure_sheets_once():
    book = get_book()
    existing = {ws.title for ws in book.worksheets()}
    for name, headers in SHEET_SCHEMAS.items():
        if name not in existing:
            ws = book.add_worksheet(title=name, rows=1000, cols=len(headers))
            ws.append_row(headers)
            continue

        ws = book.worksheet(name)
        try:
            if ws.col_count < len(headers):
                ws.add_cols(len(headers) - ws.col_count)
        except Exception as e:
            _log_system_error("ensure_cols", name, e)

        try:
            current = ws.row_values(1)
        except Exception as e:
            _log_system_error("row_values", name, e)
            current = []

        missing = [h for h in headers if h not in current]
        if missing:
            start_col = len(current) + 1
            for idx, h in enumerate(missing):
                try:
                    ws.update_cell(1, start_col + idx, h)
                except Exception as e:
                    _log_system_error("update_cell_header", name, e)

        if name == "trips":
            _migrate_trips_n_to_o(ws)
            try:
                current2 = ws.row_values(1)
                required_pay = {
                    "R": "pay1_amount", "S": "pay1_date", "T": "pay1_comment",
                    "U": "pay2_amount", "V": "pay2_date", "W": "pay2_comment",
                    "X": "pay3_amount", "Y": "pay3_date", "Z": "pay3_comment",
                }
                for col_letter, header_name in required_pay.items():
                    idx = 0
                    for ch in col_letter:
                        idx = idx * 26 + (ord(ch) - ord('A') + 1)
                    idx0 = idx - 1
                    val = current2[idx0] if idx0 < len(current2) else ""
                    if s(val).strip() != header_name:
                        try:
                            ws.update_cell(1, idx, header_name)
                        except Exception as e:
                            _log_system_error("fix_v2120_col_" + col_letter, name, e)
            except Exception as e:
                _log_system_error("ensure_v2120_cols", name, e)

        if name == "shipments":
            try:
                current2 = ws.row_values(1)
                required_at = {
                    "AD": "advance_2",
                    "AE": "advance_date_2",
                    "AF": "paid_to_2",
                    # v2.16.0 — 3 платежа по автомобилю
                    "AG": "car_pay1_amount",
                    "AH": "car_pay1_date",
                    "AI": "car_pay1_to",
                    "AJ": "car_pay1_comment",
                    "AK": "car_pay2_amount",
                    "AL": "car_pay2_date",
                    "AM": "car_pay2_to",
                    "AN": "car_pay2_comment",
                    "AO": "car_pay3_amount",
                    "AP": "car_pay3_date",
                    "AQ": "car_pay3_to",
                    "AR": "car_pay3_comment",
                }
                for col_letter, header_name in required_at.items():
                    idx = 0
                    for ch in col_letter:
                        idx = idx * 26 + (ord(ch) - ord('A') + 1)
                    idx0 = idx - 1
                    val = current2[idx0] if idx0 < len(current2) else ""
                    if s(val).strip() != header_name:
                        try:
                            ws.update_cell(1, idx, header_name)
                        except Exception as e:
                            _log_system_error("fix_shipment_col_" + col_letter, name, e)
            except Exception as e:
                _log_system_error("ensure_v2107_cols", name, e)
    return True


def append_row(name, row_dict):
    ws = get_ws_cached(name)
    headers = SHEET_SCHEMAS[name]
    try:
        if ws.col_count < len(headers):
            ws.add_cols(len(headers) - ws.col_count)
    except Exception as e:
        _log_system_error("append_cols", name, e)
    row = [row_dict.get(h, "") for h in headers]
    ws.append_row(row)
    invalidate_cache(name)


def log_action(login, role, action, details=""):
    try:
        ws = get_ws_cached("audit_log")
        row = [
            new_uuid(),
            datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            s(login), s(role), s(action), s(details),
        ]
        ws.append_row(row)
        invalidate_cache("audit_log")
    except Exception as e:
        print("AUDIT ERROR:", e)


def _log_system_error(where, name, exc):
    try:
        log_action("SYSTEM", "system", "sheets_error",
                   where + " | " + s(name) + " | " + type(exc).__name__ + ": " + s(exc))
    except Exception:
        print("SHEETS ERROR:", where, name, exc)


def get_trips(fresh=False):
    return read_all("trips", fresh=fresh)


def get_shipments(fresh=False):
    return read_all("shipments", fresh=fresh)


def get_transfer_history(fresh=False):
    return read_all("transfer_history", fresh=fresh)


def next_trip_id():
    rows = read_all_cached("trips")
    max_id = 0
    for r in rows:
        try:
            v = int(to_float(r.get("id")))
            if v > max_id:
                max_id = v
        except Exception:
            pass
    return max_id + 1


def next_trip_number():
    """Следующий постоянный порядковый номер рейса."""
    rows = read_all_cached("trips")
    max_no = 0
    for r in rows:
        try:
            v = int(to_float(r.get("trip_number")))
            if v > max_no:
                max_no = v
        except Exception:
            pass
    return max_no + 1


def next_shipment_id():
    return new_uuid()


# ============================================================
# ФИНАНСЫ И СТАТУСЫ
# ============================================================

def _adv1(x):
    return to_float(x.get("advance"))


def _adv2(x):
    return to_float(x.get("advance_2"))


def _total_advance(x):
    return _adv1(x) + _adv2(x)


def is_advance_covers(x):
    amount = to_float(x.get("amount"))
    if amount <= 0.01:
        return False
    total_adv = _total_advance(x)
    return total_adv >= amount - 0.01


def is_effectively_issued(x):
    if check_issued(s(x.get("issued", "0"))):
        return True
    if is_advance_covers(x):
        return True
    return False


CAR_PAYMENT_PARTS = 3


def normalize_car_payments(payments):
    """Возвращает ровно три безопасно нормализованных платежа."""
    result = []
    payments = payments or []
    for i in range(CAR_PAYMENT_PARTS):
        src = payments[i] if i < len(payments) and payments[i] else {}
        result.append({
            "amount": money_value(max(0.0, to_float(src.get("amount", 0)))),
            "date": date_to_display_safe(src.get("date", "")),
            "paid_to": s(src.get("paid_to", "")).strip(),
            "comment": s(src.get("comment", "")).strip(),
        })
    return result


def has_explicit_car_payments(row):
    """True, если запись уже использует новую схему v2.16.0."""
    for i in range(1, CAR_PAYMENT_PARTS + 1):
        if to_float(row.get(f"car_pay{i}_amount")) > 0.01:
            return True
        if s(row.get(f"car_pay{i}_date", "")).strip():
            return True
        if s(row.get(f"car_pay{i}_to", "")).strip():
            return True
        if s(row.get(f"car_pay{i}_comment", "")).strip():
            return True
    return False


def get_car_payments(row):
    """Три платежа автомобиля с совместимостью со старыми данными.

    Новые записи используют car_pay1..car_pay3. Старые записи автоматически
    читаются из advance / advance_2 / paid_amount и не теряют историю.
    """
    if has_explicit_car_payments(row):
        payments = []
        for i in range(1, CAR_PAYMENT_PARTS + 1):
            payments.append({
                "amount": money_value(max(0.0, to_float(row.get(f"car_pay{i}_amount")))),
                "date": date_to_display_safe(row.get(f"car_pay{i}_date", "")),
                "paid_to": s(row.get(f"car_pay{i}_to", "")).strip(),
                "comment": s(row.get(f"car_pay{i}_comment", "")).strip(),
            })
        return payments

    # Совместимость с версиями до v2.16.0.
    return normalize_car_payments([
        {
            "amount": row.get("advance", 0),
            "date": row.get("advance_date", ""),
            "paid_to": row.get("paid_to", ""),
            "comment": "",
        },
        {
            "amount": row.get("advance_2", 0),
            "date": row.get("advance_date_2", ""),
            "paid_to": row.get("paid_to_2", ""),
            "comment": "",
        },
        {
            "amount": row.get("paid_amount", 0),
            "date": row.get("date_pay", "") if to_float(row.get("paid_amount")) > 0.01 else "",
            "paid_to": "",
            "comment": "",
        },
    ])


def car_payments_total(row):
    return money_value(sum(p["amount"] for p in get_car_payments(row)))


def payments_last_date(payments):
    dates = [s(p.get("date", "")).strip() for p in normalize_car_payments(payments)
             if s(p.get("date", "")).strip()]
    return dates[-1] if dates else ""


def calculate_financials(row):
    """Единый расчёт денег по автомобилю для трёх платежей."""
    amount = max(0.0, to_float(row.get("amount")))
    payments = get_car_payments(row)
    recorded_paid = money_value(sum(p["amount"] for p in payments))
    if amount > 0.01:
        recorded_paid = min(recorded_paid, amount)

    paid_flag = check_paid(row.get("paid", "0"))
    payer_type = s(row.get("payer_type"))

    # paid=1 остаётся совместимым со старыми версиями и пакетной оплатой НДС.
    paid_value = amount if paid_flag and amount > 0.01 else recorded_paid
    debt = max(0.0, amount - paid_value)

    if payer_type == NDS_PAYER:
        nds = amount * NDS_RATE / (1 + NDS_RATE)
        amount_no_nds = amount - nds
    else:
        nds = 0.0
        amount_no_nds = amount

    status_text = "🟢 Оплачен" if debt <= 0.01 else "🔴 Долг"

    p1, p2, p3 = payments
    return {
        "amount": money_value(amount),
        # Старые ключи сохранены, чтобы остальная логика приложения не ломалась.
        "advance": money_value(p1["amount"] + p2["amount"]),
        "advance_1": money_value(p1["amount"]),
        "advance_2": money_value(p2["amount"]),
        "paid_amount": money_value(p3["amount"]),
        "payments": payments,
        "recorded_paid": money_value(recorded_paid),
        "paid_value": money_value(paid_value),
        "debt": money_value(debt),
        "nds": money_value(nds),
        "amount_no_nds": money_value(amount_no_nds),
        "status_text": status_text,
    }


def is_car_active_on_avtovoz(x):
    if is_effectively_issued(x):
        return False
    if s(x.get("transferred_to_trip", "")):
        return False
    return True


def count_active_cars(cars):
    return sum(1 for x in cars if is_car_active_on_avtovoz(x))


def count_issued_cars(cars):
    return sum(1 for x in cars if is_effectively_issued(x))


def is_trace_row(x):
    return (bool(s(x.get("transferred_to_trip", "")))
            and s(x.get("car_model", "")).startswith("Перенесён:"))


def is_transferred_car(x):
    if is_trace_row(x):
        return False
    ac = s(x.get("transferred_from_trip", "")).strip()
    aa = s(x.get("transferred_to_trip", "")).strip()
    return bool(ac or aa)


def count_outgoing_transfers(cars):
    return sum(1 for x in cars if is_trace_row(x))


def count_incoming_transfers(cars):
    return sum(1 for x in cars if is_transferred_car(x))


def count_incoming_on_route(cars):
    return sum(
        1 for x in cars
        if is_transferred_car(x)
        and not is_effectively_issued(x)
    )


def sum_incoming_transfers(cars):
    total_amount = 0.0
    total_advance = 0.0
    total_debt = 0.0
    for x in cars:
        if not is_transferred_car(x):
            continue
        f = calculate_financials(x)
        total_amount += f["amount"]
        total_advance += f["advance"]
        total_debt += f["debt"]
    return total_amount, total_advance, total_debt


def _is_nal_or_acquiring(x):
    pt = s(x.get("payer_type", "")).strip()
    return pt in ("нал", "эквайринг")


def _is_beznal_nds(x):
    return s(x.get("payer_type", "")).strip() == NDS_PAYER


# v2.12.8: единая проверка, может ли роль оплачивать конкретное авто
def can_pay_shipment(x, role):
    """
    Оплата авто разрешена:
    - для нал / эквайринг — всем, у кого есть edit_payment;
    - для безнал с НДС — только admin и director (право pay_beznal_nds).
    """
    if not _is_beznal_nds(x):
        return can("edit_payment", role)
    return can("pay_beznal_nds", role)


def is_trip_beznal_nds(trip_id, shipments):
    for x in shipments:
        if s(x.get("trip_id")) == s(trip_id) and _is_beznal_nds(x):
            return True
    return False


def compute_grand_totals(trips, shipments):
    active_trip_ids = {s(t.get("id")) for t in trips
                       if not is_archived(t.get("archived", "0"))
                       and not is_deleted(t.get("deleted_at", ""))
                       and not check_completed(t.get("completed", "0"))}

    completed_trip_ids = {s(t.get("id")) for t in trips
                          if check_completed(t.get("completed", "0"))
                          and not is_archived(t.get("archived", "0"))
                          and not is_deleted(t.get("deleted_at", ""))}

    active_cars = [x for x in shipments
                   if s(x.get("trip_id")) in active_trip_ids]

    total_amount = 0.0
    total_paid = 0.0
    active_cars_count = 0
    for x in active_cars:
        f = calculate_financials(x)
        total_amount += f["amount"]
        total_paid += f["paid_value"]
        if not is_effectively_issued(x):
            active_cars_count += 1

    debt_nal = 0.0
    for x in active_cars:
        if not _is_nal_or_acquiring(x):
            continue
        if is_effectively_issued(x):
            continue
        if check_paid(s(x.get("paid", "0"))):
            continue
        f = calculate_financials(x)
        if f["debt"] > 0.01:
            debt_nal += f["debt"]

    beznal_trip_ids = active_trip_ids | completed_trip_ids
    debt_beznal = 0.0
    nds_beznal = 0.0
    for x in shipments:
        if s(x.get("trip_id")) not in beznal_trip_ids:
            continue
        if not _is_beznal_nds(x):
            continue
        if check_paid(s(x.get("paid", "0"))):
            continue
        f = calculate_financials(x)
        if f["debt"] > 0.01:
            debt_beznal += f["debt"]
            nds_beznal += f["nds"]

    return {
        "active_trips": len(active_trip_ids),
        "active_cars": active_cars_count,
        "total_amount": total_amount,
        "total_paid": total_paid,
        "total_advance": total_paid,
        "debt_nal": debt_nal,
        "debt_beznal": debt_beznal,
        "nds_beznal": nds_beznal,
    }


def next_free_position(cars):
    """Возвращает свободное место 1..MAX_CARS.

    Важно: реально перенесённый в этот рейс автомобиль тоже занимает место.
    Не занимают место только служебные строки "Перенесён:" и выданные авто.
    """
    occupied = set()
    for x in cars:
        if is_trace_row(x) or is_effectively_issued(x):
            continue
        try:
            pos = int(to_float(x.get("position")))
        except Exception:
            continue
        if 1 <= pos <= MAX_CARS:
            occupied.add(pos)

    for p in range(1, MAX_CARS + 1):
        if p not in occupied:
            return p
    return None


def describe_trip(trip):
    if not trip:
        return ""
    parts = []
    trip_no = s(trip.get("trip_number", "")).strip()
    if trip_no:
        parts.append("рейс № " + trip_no)
    if s(trip.get("tractor_number", "")):
        parts.append(s(trip.get("tractor_number", "")))
    if s(trip.get("driver", "")):
        parts.append(s(trip.get("driver", "")))
    if s(trip.get("loading_from", "")):
        loading_label = "погрузка от " + s(trip.get("loading_from", ""))
        if s(trip.get("loading_contract_number", "")):
            loading_label += " · договор № " + s(trip.get("loading_contract_number", ""))
        parts.append(loading_label)
    if s(trip.get("route", "")):
        parts.append(s(trip.get("route", "")))
    dep = date_to_display_safe(trip.get("date_departure", ""))
    if dep:
        parts.append("выезд " + dep)
    return " — ".join(parts)


def find_trip_by_id(all_trips, trip_id):
    tid = s(trip_id).strip()
    if not tid:
        return None
    for t in all_trips:
        if s(t.get("id")) == tid:
            return t
    return None


def resolve_target_trip_label(target_id, all_trips, transfer_history=None):
    tid = s(target_id).strip()
    if not tid:
        return "—"
    trip = find_trip_by_id(all_trips, tid)
    if trip:
        label = describe_trip(trip)
        if check_completed(trip.get("completed", "0")):
            label += " ✅"
        if is_archived(trip.get("archived", "0")):
            label += " 📦"
        if is_deleted(trip.get("deleted_at", "")):
            label += " 🗑"
        return label
    if transfer_history:
        for h in transfer_history:
            if s(h.get("to_trip")) == tid:
                ts = s(h.get("ts", ""))
                if ts:
                    return "рейс #" + tid + " (перенос " + ts[:10] + ")"
                break
    return "рейс #" + tid


def find_target_trip_for_parent(parent_ship_id, all_shipments):
    pid = s(parent_ship_id).strip()
    if not pid:
        return ""
    for c in all_shipments:
        if s(c.get("id")) == pid:
            return s(c.get("trip_id", ""))
    return ""


def resolve_source_trip_label_for_car(x, all_trips, transfer_history):
    ac = s(x.get("transferred_from_trip", "")).strip()
    if ac:
        return resolve_target_trip_label(ac, all_trips, transfer_history)
    sid = s(x.get("id"))
    for h in transfer_history:
        if s(h.get("shipment_id")) == sid and s(h.get("cancelled", "")) != "1":
            from_trip = s(h.get("from_trip", "")).strip()
            if from_trip:
                return resolve_target_trip_label(from_trip, all_trips, transfer_history)
    return "—"


# ============================================================
# РАЗДЕЛЕНИЕ СЧЁТА НА 3 ПЛАТЕЖА
# ============================================================

def extract_trip_payments(trip):
    payments = []
    for i in range(1, MAX_PAY_SPLITS + 1):
        amt = to_float(trip.get("pay" + s(i) + "_amount"))
        dt = date_to_display_safe(trip.get("pay" + s(i) + "_date", ""))
        cm = s(trip.get("pay" + s(i) + "_comment", "")).strip()
        if amt > 0.01 or dt or cm:
            payments.append({
                "idx": i,
                "amount": money_value(amt),
                "date": dt,
                "comment": cm,
            })
    return payments


def compute_trip_payments_total(trip):
    total = 0.0
    for i in range(1, MAX_PAY_SPLITS + 1):
        total += to_float(trip.get("pay" + s(i) + "_amount"))
    return money_value(total)


def compute_trip_nds_debt(trip, shipments):
    cars = [x for x in shipments if s(x.get("trip_id")) == s(trip.get("id"))]
    nds_total = 0.0
    nds_debt = 0.0
    for x in cars:
        if not _is_beznal_nds(x):
            continue
        f = calculate_financials(x)
        nds_total += f["amount"]
        if not check_paid(s(x.get("paid", "0"))):
            nds_debt += f["debt"]

    paid_total = compute_trip_payments_total(trip)

    paid_from_sum = nds_total - nds_debt
    if paid_from_sum < 0.01:
        paid_from_sum = 0.0

    parts = extract_trip_payments(trip)
    is_paid = bool(nds_debt <= 0.01 or paid_total >= nds_debt - 0.01)

    return {
        "nds_debt": money_value(nds_debt),
        "nds_total": money_value(nds_total),
        "paid_from_sum": money_value(paid_from_sum),
        "paid_total": money_value(paid_total),
        "parts_count": len(parts),
        "is_paid": is_paid,
        "parts": parts,
    }


def update_trip_payments_split(trip_id, payments):
    ws = get_ws_cached("trips")
    row_idx = _find_row_index_by_id("trips", trip_id)
    if row_idx is None:
        return False

    payload = []
    col_map = [
        ("R", "S", "T"),
        ("U", "V", "W"),
        ("X", "Y", "Z"),
    ]
    for i in range(MAX_PAY_SPLITS):
        amt = 0.0
        dt = ""
        cm = ""
        if i < len(payments):
            p = payments[i] or {}
            amt = money_value(p.get("amount"))
            dt = date_to_display_safe(p.get("date", ""))
            cm = s(p.get("comment", "")).strip()
        c_amt, c_dt, c_cm = col_map[i]
        payload.append({"range": c_amt + str(row_idx), "values": [[amt]]})
        payload.append({"range": c_dt + str(row_idx), "values": [[dt]]})
        payload.append({"range": c_cm + str(row_idx), "values": [[cm]]})

    ws.batch_update(payload, value_input_option="USER_ENTERED")
    invalidate_cache("trips")
    return True


def clear_trip_payments_split(trip_id):
    return update_trip_payments_split(trip_id, [])


# ============================================================
# CRUD: РЕЙСЫ
# ============================================================

def create_trip(tractor, driver, route, dep, ret, created_by,
                loading_from="", loading_contract_number=""):
    new_trip_id = next_trip_id()
    new_trip_number = next_trip_number()
    append_row("trips", {
        "id": new_trip_id,
        "tractor_number": s(tractor), "driver": s(driver), "route": s(route),
        "date_departure": s(dep), "date_return": s(ret),
        "created_by": s(created_by),
        "created_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "archived": "0",
        "completed": "0",
        "completed_at": "",
        "invoice_number": "",
        "invoice_date": "",
        "deleted_at": "",
        "loading_from": s(loading_from),
        "payment_date": "",
        "payment_comment": "",
        "pay1_amount": "", "pay1_date": "", "pay1_comment": "",
        "pay2_amount": "", "pay2_date": "", "pay2_comment": "",
        "pay3_amount": "", "pay3_date": "", "pay3_comment": "",
        "loading_contract_number": s(loading_contract_number),
        "trip_number": new_trip_number,
    })
    return new_trip_number


def update_trip(trip_id, tractor, driver, route, dep, ret,
                loading_from="", loading_contract_number="",
                payment_date="", payment_comment=""):
    ws = get_ws_cached("trips")
    row_idx = _find_row_index_by_id("trips", trip_id)
    if row_idx is None:
        return False
    payload = [
        {"range": "B" + str(row_idx), "values": [[s(tractor)]]},
        {"range": "C" + str(row_idx), "values": [[s(driver)]]},
        {"range": "D" + str(row_idx), "values": [[s(route)]]},
        {"range": "E" + str(row_idx), "values": [[s(dep)]]},
        {"range": "F" + str(row_idx), "values": [[s(ret)]]},
        {"range": "O" + str(row_idx), "values": [[s(loading_from)]]},
        {"range": "P" + str(row_idx), "values": [[s(payment_date)]]},
        {"range": "Q" + str(row_idx), "values": [[s(payment_comment)]]},
        {"range": "AA" + str(row_idx), "values": [[s(loading_contract_number)]]},
    ]
    ws.batch_update(payload, value_input_option="USER_ENTERED")
    invalidate_cache("trips")
    return True


def update_trip_payment(trip_id, payment_date, payment_comment):
    ws = get_ws_cached("trips")
    row_idx = _find_row_index_by_id("trips", trip_id)
    if row_idx is None:
        return False
    payload = [
        {"range": "P" + str(row_idx), "values": [[s(payment_date)]]},
        {"range": "Q" + str(row_idx), "values": [[s(payment_comment)]]},
    ]
    ws.batch_update(payload, value_input_option="USER_ENTERED")
    invalidate_cache("trips")
    return True


def update_trip_invoice(trip_id, invoice_number, invoice_date):
    ws = get_ws_cached("trips")
    row_idx = _find_row_index_by_id("trips", trip_id)
    if row_idx is None:
        return False
    payload = [
        {"range": "L" + str(row_idx), "values": [[s(invoice_number)]]},
        {"range": "M" + str(row_idx), "values": [[s(invoice_date)]]},
    ]
    ws.batch_update(payload, value_input_option="USER_ENTERED")
    invalidate_cache("trips")
    return True


def archive_trip(trip_id):
    ws = get_ws_cached("trips")
    row_idx = _find_row_index_by_id("trips", trip_id)
    if row_idx is None:
        return False
    ws.update_cell(row_idx, 9, "1")
    invalidate_cache("trips")
    return True


def unarchive_trip(trip_id):
    ws = get_ws_cached("trips")
    row_idx = _find_row_index_by_id("trips", trip_id)
    if row_idx is None:
        return False
    ws.update_cell(row_idx, 9, "0")
    invalidate_cache("trips")
    return True


def complete_trip(trip_id, completed_at):
    ws = get_ws_cached("trips")
    row_idx = _find_row_index_by_id("trips", trip_id)
    if row_idx is None:
        return False
    payload = [
        {"range": "J" + str(row_idx), "values": [["1"]]},
        {"range": "K" + str(row_idx), "values": [[s(completed_at)]]},
    ]
    ws.batch_update(payload, value_input_option="USER_ENTERED")
    invalidate_cache("trips")
    return True


def uncomplete_trip(trip_id):
    ws = get_ws_cached("trips")
    row_idx = _find_row_index_by_id("trips", trip_id)
    if row_idx is None:
        return False
    payload = [
        {"range": "J" + str(row_idx), "values": [["0"]]},
        {"range": "K" + str(row_idx), "values": [[""]]},
    ]
    ws.batch_update(payload, value_input_option="USER_ENTERED")
    invalidate_cache("trips")
    return True


def soft_delete_trip(trip_id):
    ws = get_ws_cached("trips")
    row_idx = _find_row_index_by_id("trips", trip_id)
    if row_idx is None:
        return False
    ws.update_cell(row_idx, 14,
                   datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
    invalidate_cache("trips")
    return True


def delete_trip(trip_id):
    ws = get_ws_cached("trips")
    row_idx = _find_row_index_by_id("trips", trip_id)
    if row_idx is not None:
        ws.delete_rows(row_idx)
        invalidate_cache("trips")
    ws2 = get_ws_cached("shipments")
    to_del = [i for i, row in enumerate(ws2.get_all_values()[1:], start=2)
              if s(row[1]) == s(trip_id)]
    for i in reversed(to_del):
        try:
            ws2.delete_rows(i)
        except Exception:
            pass
    invalidate_cache("shipments")


# ============================================================
# CRUD: АВТОМОБИЛИ
# ============================================================

def _normalize_advances(amount, adv1, adv2):
    amount = money_value(amount)
    adv1 = money_value(adv1)
    adv2 = money_value(adv2)
    was_trimmed = False

    if amount <= 0.01:
        if adv1 > 0 or adv2 > 0:
            return 0.0, 0.0, True
        return adv1, adv2, False

    total = adv1 + adv2
    if total <= amount + 0.01:
        return adv1, adv2, False

    if adv2 >= (total - amount) - 0.01:
        adv2 = max(0.0, adv2 - (total - amount))
    else:
        adv2 = 0.0
        adv1 = max(0.0, amount)

    total = adv1 + adv2
    if total > amount:
        adv1 = max(0.0, amount - adv2)
        total = adv1 + adv2
        if total > amount:
            adv1 = amount
            adv2 = 0.0
    was_trimmed = True
    return money_value(adv1), money_value(adv2), was_trimmed


def create_shipment(trip_id, position, car_model, client, amount,
                    date_pay, paid_to, delivery_city, vin,
                    advance, advance_date,
                    payer_type, customer, contract_number,
                    nds_amount, amount_no_nds, created_by,
                    advance_2=0, advance_date_2="", paid_to_2="",
                    payments=None):
    """Создаёт автомобиль и сохраняет до трёх отдельных платежей."""
    if payments is None:
        payments = [
            {"amount": advance, "date": advance_date, "paid_to": paid_to, "comment": ""},
            {"amount": advance_2, "date": advance_date_2, "paid_to": paid_to_2, "comment": ""},
            {"amount": 0, "date": date_pay, "paid_to": "", "comment": ""},
        ]
    parts = normalize_car_payments(payments)
    p1, p2, p3 = parts
    total_paid = money_value(sum(p["amount"] for p in parts))
    amount_val = money_value(amount)
    paid_flag = "1" if amount_val > 0.01 and total_paid >= amount_val - 0.01 else "0"

    append_row("shipments", {
        "id": next_shipment_id(),
        "trip_id": trip_id,
        "position": position,
        "car_model": s(car_model),
        "client": s(client),
        "amount": amount_val,
        "date_pay": payments_last_date(parts),
        "paid_to": p1["paid_to"],
        "delivery_city": s(delivery_city),
        "vin": s(vin),
        # Старые поля зеркалируются для совместимости со старыми отчётами.
        "advance": p1["amount"],
        "advance_date": p1["date"],
        "payer_type": s(payer_type),
        "customer": s(customer),
        "contract_number": s(contract_number),
        "nds_amount": money_value(nds_amount),
        "amount_no_nds": money_value(amount_no_nds),
        "created_by": s(created_by),
        "created_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "issued": "0",
        "issued_ever": "0",
        "paid": paid_flag,
        "archived": "0",
        "paid_amount": p3["amount"],
        "invoice_number": "",
        "invoice_date": "",
        "transferred_to_trip": "",
        "transferred_at": "",
        "transferred_from_trip": "",
        "advance_2": p2["amount"],
        "advance_date_2": p2["date"],
        "paid_to_2": p2["paid_to"],
        "car_pay1_amount": p1["amount"],
        "car_pay1_date": p1["date"],
        "car_pay1_to": p1["paid_to"],
        "car_pay1_comment": p1["comment"],
        "car_pay2_amount": p2["amount"],
        "car_pay2_date": p2["date"],
        "car_pay2_to": p2["paid_to"],
        "car_pay2_comment": p2["comment"],
        "car_pay3_amount": p3["amount"],
        "car_pay3_date": p3["date"],
        "car_pay3_to": p3["paid_to"],
        "car_pay3_comment": p3["comment"],
    })


def update_shipment(shipment_id, position, car_model, client, amount,
                    date_pay, paid_to, delivery_city, vin, advance, advance_date,
                    payer_type, customer, contract_number,
                    nds_amount, amount_no_nds,
                    advance_2=0, advance_date_2="", paid_to_2="",
                    payments=None):
    ws = get_ws_cached("shipments")
    row_idx = _find_row_index_by_id("shipments", shipment_id)
    if row_idx is None:
        return False

    current = None
    for r in read_all_cached("shipments"):
        if s(r.get("id")) == s(shipment_id):
            current = r
            break

    if payments is None:
        if current is not None:
            payments = get_car_payments(current)
        else:
            payments = [
                {"amount": advance, "date": advance_date, "paid_to": paid_to, "comment": ""},
                {"amount": advance_2, "date": advance_date_2, "paid_to": paid_to_2, "comment": ""},
                {"amount": 0, "date": date_pay, "paid_to": "", "comment": ""},
            ]
    parts = normalize_car_payments(payments)
    p1, p2, p3 = parts
    amount_val = money_value(amount)
    total_paid = money_value(sum(p["amount"] for p in parts))

    # Если редактируются только данные автомобиля, не снимаем старую отметку
    # полной оплаты. При реальном изменении платежей статус пересчитывается.
    current_parts = get_car_payments(current or {})
    payments_changed = normalize_car_payments(current_parts) != parts
    if (current is not None and check_paid(current.get("paid", "0"))
            and not payments_changed):
        paid_flag = "1"
    else:
        paid_flag = "1" if amount_val > 0.01 and total_paid >= amount_val - 0.01 else "0"

    current_issued_val = s((current or {}).get("issued", "0")).strip() or "0"
    auto_issued = amount_val > 0.01 and (p1["amount"] + p2["amount"]) >= amount_val - 0.01
    new_issued_val = "1" if auto_issued else current_issued_val

    payload = [
        {"range": "C" + str(row_idx), "values": [[position]]},
        {"range": "D" + str(row_idx), "values": [[s(car_model)]]},
        {"range": "E" + str(row_idx), "values": [[s(client)]]},
        {"range": "F" + str(row_idx), "values": [[amount_val]]},
        {"range": "G" + str(row_idx), "values": [[payments_last_date(parts)]]},
        {"range": "H" + str(row_idx), "values": [[p1["paid_to"]]]},
        {"range": "I" + str(row_idx), "values": [[s(delivery_city)]]},
        {"range": "J" + str(row_idx), "values": [[s(vin)]]},
        {"range": "K" + str(row_idx), "values": [[p1["amount"]]]},
        {"range": "L" + str(row_idx), "values": [[p1["date"]]]},
        {"range": "M" + str(row_idx), "values": [[s(payer_type)]]},
        {"range": "N" + str(row_idx), "values": [[s(customer)]]},
        {"range": "O" + str(row_idx), "values": [[s(contract_number)]]},
        {"range": "P" + str(row_idx), "values": [[money_value(nds_amount)]]},
        {"range": "Q" + str(row_idx), "values": [[money_value(amount_no_nds)]]},
        {"range": "T" + str(row_idx), "values": [[new_issued_val]]},
        {"range": "V" + str(row_idx), "values": [[paid_flag]]},
        {"range": "X" + str(row_idx), "values": [[p3["amount"]]]},
        {"range": "AD" + str(row_idx), "values": [[p2["amount"]]]},
        {"range": "AE" + str(row_idx), "values": [[p2["date"]]]},
        {"range": "AF" + str(row_idx), "values": [[p2["paid_to"]]]},
        {"range": "AG" + str(row_idx), "values": [[p1["amount"]]]},
        {"range": "AH" + str(row_idx), "values": [[p1["date"]]]},
        {"range": "AI" + str(row_idx), "values": [[p1["paid_to"]]]},
        {"range": "AJ" + str(row_idx), "values": [[p1["comment"]]]},
        {"range": "AK" + str(row_idx), "values": [[p2["amount"]]]},
        {"range": "AL" + str(row_idx), "values": [[p2["date"]]]},
        {"range": "AM" + str(row_idx), "values": [[p2["paid_to"]]]},
        {"range": "AN" + str(row_idx), "values": [[p2["comment"]]]},
        {"range": "AO" + str(row_idx), "values": [[p3["amount"]]]},
        {"range": "AP" + str(row_idx), "values": [[p3["date"]]]},
        {"range": "AQ" + str(row_idx), "values": [[p3["paid_to"]]]},
        {"range": "AR" + str(row_idx), "values": [[p3["comment"]]]},
    ]
    ws.batch_update(payload, value_input_option="USER_ENTERED")
    invalidate_cache("shipments")
    return True


def save_car_payments(shipment_id, payments, role=None, user_login=""):
    """Сохраняет три платежа автомобиля без изменения остальных данных."""
    shipments = read_all("shipments", fresh=True)
    ship = next((x for x in shipments if s(x.get("id")) == s(shipment_id)), None)
    if ship is None:
        return False, "Автомобиль не найден"
    if role is not None and not can_pay_shipment(ship, role):
        return False, "Для этого типа оплаты у вашей роли нет права изменять платежи"

    parts = normalize_car_payments(payments)
    amount = money_value(ship.get("amount"))
    total = money_value(sum(p["amount"] for p in parts))
    if amount > 0.01 and total > amount + 0.01:
        return False, ("Сумма трёх платежей больше стоимости перевозки на "
                       + fmt_money(total - amount) + " ₽")

    row_idx = _find_row_index_by_id("shipments", shipment_id)
    if row_idx is None:
        return False, "Строка автомобиля не найдена"

    p1, p2, p3 = parts
    paid_flag = "1" if amount > 0.01 and total >= amount - 0.01 else "0"
    payload = [
        # Новая детальная история платежей.
        {"range": "AG" + str(row_idx), "values": [[p1["amount"]]]},
        {"range": "AH" + str(row_idx), "values": [[p1["date"]]]},
        {"range": "AI" + str(row_idx), "values": [[p1["paid_to"]]]},
        {"range": "AJ" + str(row_idx), "values": [[p1["comment"]]]},
        {"range": "AK" + str(row_idx), "values": [[p2["amount"]]]},
        {"range": "AL" + str(row_idx), "values": [[p2["date"]]]},
        {"range": "AM" + str(row_idx), "values": [[p2["paid_to"]]]},
        {"range": "AN" + str(row_idx), "values": [[p2["comment"]]]},
        {"range": "AO" + str(row_idx), "values": [[p3["amount"]]]},
        {"range": "AP" + str(row_idx), "values": [[p3["date"]]]},
        {"range": "AQ" + str(row_idx), "values": [[p3["paid_to"]]]},
        {"range": "AR" + str(row_idx), "values": [[p3["comment"]]]},
        # Старые поля оставляем синхронными для совместимости.
        {"range": "K" + str(row_idx), "values": [[p1["amount"]]]},
        {"range": "L" + str(row_idx), "values": [[p1["date"]]]},
        {"range": "H" + str(row_idx), "values": [[p1["paid_to"]]]},
        {"range": "AD" + str(row_idx), "values": [[p2["amount"]]]},
        {"range": "AE" + str(row_idx), "values": [[p2["date"]]]},
        {"range": "AF" + str(row_idx), "values": [[p2["paid_to"]]]},
        {"range": "X" + str(row_idx), "values": [[p3["amount"]]]},
        {"range": "G" + str(row_idx), "values": [[payments_last_date(parts)]]},
        {"range": "V" + str(row_idx), "values": [[paid_flag]]},
    ]
    ws = get_ws_cached("shipments")
    ws.batch_update(payload, value_input_option="USER_ENTERED")
    invalidate_cache("shipments")
    if user_login:
        log_action(user_login, s(role), "edit_car_payments",
                   "shipment " + s(shipment_id) + " | оплачено " + fmt_money(total) + " ₽")
    return True, ""


def toggle_issued(shipment_id, current_value, current_ever):
    """Меняет только статус выдачи.

    Раньше при снятии "Выдан" дополнительно сбрасывался столбец paid.
    Это связывало две независимые операции и могло случайно вернуть долг.
    """
    ws = get_ws_cached("shipments")
    row_idx = _find_row_index_by_id("shipments", shipment_id)
    if row_idx is None:
        return current_value

    was_issued = check_issued(current_value)
    if not was_issued:
        payload = [
            {"range": "T" + str(row_idx), "values": [["1"]]},
            {"range": "U" + str(row_idx), "values": [["1"]]},
        ]
    else:
        # issued_ever (U) остаётся = 1 как исторический признак.
        # paid (V) здесь не трогаем.
        payload = [
            {"range": "T" + str(row_idx), "values": [["0"]]},
        ]

    ws.batch_update(payload, value_input_option="USER_ENTERED")
    invalidate_cache("shipments")
    return "0" if was_issued else "1"


# v2.12.8: добавлена защита — безнал с НДС может оплачивать только admin/director
def _legacy_original_advances(ship):
    """Восстановление аванса для записей, оплаченных старой версией.

    В v2.12.8 при оплате K (advance) заменялся полной суммой,
    а X (paid_amount) использовался как резервная копия общего аванса.
    """
    amount = money_value(ship.get("amount"))
    adv1 = money_value(ship.get("advance"))
    adv2 = money_value(ship.get("advance_2"))
    stored = money_value(ship.get("paid_amount"))

    is_legacy = (
        check_paid(ship.get("paid", "0"))
        and amount > 0.01
        and adv1 >= amount - 0.01
        and stored > 0.01
    )
    if not is_legacy:
        return adv1, adv2, False

    original_total = min(stored, amount)
    original_adv1 = max(0.0, original_total - adv2)
    return money_value(original_adv1), money_value(adv2), True


# v2.14.0: оплата больше не изменяет авансы
def toggle_paid(shipment_id, current_paid, current_amount, current_advance,
                role=None):
    shipments = read_all_cached("shipments")
    ship = next((x for x in shipments if s(x.get("id")) == s(shipment_id)), None)
    if ship is None:
        return current_paid

    if role is not None and not can_pay_shipment(ship, role):
        log_action(
            "SYSTEM", s(role), "toggle_paid_denied",
            "shipment " + s(shipment_id)
            + " (" + s(ship.get("payer_type", "")) + ") — нет прав"
        )
        return current_paid

    ws = get_ws_cached("shipments")
    row_idx = _find_row_index_by_id("shipments", shipment_id)
    if row_idx is None:
        return current_paid

    was_paid = check_paid(current_paid)
    amount_val = money_value(ship.get("amount", current_amount))

    if not was_paid:
        advance_total = min(
            amount_val,
            money_value(_total_advance(ship))
        ) if amount_val > 0.01 else 0.0
        balance_payment = max(0.0, amount_val - advance_total)
        payload = [
            {"range": "X" + str(row_idx), "values": [[money_value(balance_payment)]]},
            {"range": "V" + str(row_idx), "values": [["1"]]},
        ]
    else:
        old_adv1, _old_adv2, legacy = _legacy_original_advances(ship)
        payload = [
            {"range": "X" + str(row_idx), "values": [[0]]},
            {"range": "V" + str(row_idx), "values": [["0"]]},
        ]
        if legacy:
            payload.append(
                {"range": "K" + str(row_idx), "values": [[old_adv1]]}
            )

    ws.batch_update(payload, value_input_option="USER_ENTERED")
    invalidate_cache("shipments")
    return "0" if was_paid else "1"


def delete_shipment(shipment_id):
    ws = get_ws_cached("shipments")
    row_idx = _find_row_index_by_id("shipments", shipment_id)
    if row_idx is None:
        return
    ws.delete_rows(row_idx)
    invalidate_cache("shipments")


# ============================================================
# ОПЛАТА РЕЙСА (безнал с НДС)
# ============================================================

# v2.12.8: только admin/director (право pay_beznal_nds)
def pay_entire_trip_nds(trip_id, user_login, role):
    """Закрывает остаток по всем неоплаченным авто с НДС в рейсе."""
    if not can("pay_beznal_nds", role):
        log_action(user_login, role, "pay_entire_trip_nds_denied",
                   "trip " + s(trip_id) + " — нет прав")
        return 0, 0, 0.0

    shipments = read_all("shipments", fresh=True)
    cars = [
        x for x in shipments
        if s(x.get("trip_id")) == s(trip_id) and _is_beznal_nds(x)
    ]

    paid_count = 0
    skipped_count = 0
    closed_sum = 0.0
    ws = get_ws_cached("shipments")

    for x in cars:
        sid = s(x.get("id"))
        row_idx = _find_row_index_by_id("shipments", sid)
        if row_idx is None:
            skipped_count += 1
            continue

        if check_paid(x.get("paid", "0")):
            skipped_count += 1
            continue

        f = calculate_financials(x)
        debt_val = f["debt"]
        if debt_val <= 0.01:
            # Денег уже достаточно, просто подтверждаем оплату.
            debt_val = 0.0

        payload = [
            {"range": "X" + str(row_idx), "values": [[money_value(debt_val)]]},
            {"range": "V" + str(row_idx), "values": [["1"]]},
        ]
        ws.batch_update(payload, value_input_option="USER_ENTERED")
        paid_count += 1
        closed_sum += debt_val

    if paid_count > 0:
        invalidate_cache("shipments")
        log_action(
            user_login, role, "pay_entire_trip_nds",
            "trip " + s(trip_id) + " | оплачено " + s(paid_count)
            + " авто | закрыто " + fmt_money(closed_sum) + " ₽"
        )

    return paid_count, skipped_count, money_value(closed_sum)


def unpay_entire_trip_nds(trip_id, user_login, role):
    """Снимает отметку оплаты, не уничтожая исходные авансы."""
    if not can("pay_beznal_nds", role):
        log_action(user_login, role, "unpay_entire_trip_nds_denied",
                   "trip " + s(trip_id) + " — нет прав")
        return 0

    shipments = read_all("shipments", fresh=True)
    cars = [
        x for x in shipments
        if s(x.get("trip_id")) == s(trip_id) and _is_beznal_nds(x)
    ]

    unpay_count = 0
    ws = get_ws_cached("shipments")

    for x in cars:
        if not check_paid(x.get("paid", "0")):
            continue

        sid = s(x.get("id"))
        row_idx = _find_row_index_by_id("shipments", sid)
        if row_idx is None:
            continue

        old_adv1, _old_adv2, legacy = _legacy_original_advances(x)
        payload = [
            {"range": "X" + str(row_idx), "values": [[0]]},
            {"range": "V" + str(row_idx), "values": [["0"]]},
        ]
        if legacy:
            payload.append(
                {"range": "K" + str(row_idx), "values": [[old_adv1]]}
            )

        ws.batch_update(payload, value_input_option="USER_ENTERED")
        unpay_count += 1

    if unpay_count > 0:
        invalidate_cache("shipments")
        log_action(
            user_login, role, "unpay_entire_trip_nds",
            "trip " + s(trip_id) + " | снято с " + s(unpay_count) + " авто"
        )

    return unpay_count


# ============================================================
# ПЕРЕНОСЫ
# ============================================================

def log_transfer(shipment_id, from_trip, to_trip,
                 from_position, to_position, user_login, note="",
                 cancelled=""):
    append_row("transfer_history", {
        "id": new_uuid(),
        "ts": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "shipment_id": s(shipment_id),
        "from_trip": s(from_trip),
        "to_trip": s(to_trip),
        "from_position": s(from_position),
        "to_position": s(to_position),
        "user_login": s(user_login),
        "note": s(note),
        "cancelled": s(cancelled),
    })


def transfer_shipment_to_trip(shipment_id, target_trip_id, new_position,
                              user_login):
    ws = get_ws_cached("shipments")
    row_idx = _find_row_index_by_id("shipments", shipment_id)
    if row_idx is None:
        return False

    rows = read_all_cached("shipments")
    src = None
    for r in rows:
        if s(r.get("id")) == s(shipment_id):
            src = r
            break
    if src is None:
        return False

    source_trip_id = s(src.get("trip_id", ""))
    source_position = s(src.get("position", ""))

    payload = [
        {"range": "B" + str(row_idx), "values": [[s(target_trip_id)]]},
        {"range": "C" + str(row_idx), "values": [[new_position]]},
        {"range": "AA" + str(row_idx), "values": [[s(target_trip_id)]]},
        {"range": "AB" + str(row_idx), "values": [[datetime.now().strftime("%d.%m.%Y")]]},
        {"range": "AC" + str(row_idx), "values": [[source_trip_id]]},
    ]
    ws.batch_update(payload, value_input_option="USER_ENTERED")

    trace = {
        "id": next_shipment_id(),
        "trip_id": source_trip_id,
        "position": source_position,
        "car_model": "Перенесён: " + s(src.get("car_model", "")),
        "client": "",
        "amount": 0,
        "date_pay": "",
        "paid_to": "",
        "delivery_city": s(src.get("delivery_city", "")),
        "vin": s(src.get("vin", "")),
        "advance": 0,
        "advance_date": "",
        "payer_type": "",
        "customer": "",
        "contract_number": "",
        "nds_amount": 0,
        "amount_no_nds": 0,
        "created_by": s(user_login),
        "created_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "issued": "0",
        "issued_ever": "0",
        "paid": "1",
        "archived": "0",
        "paid_amount": 0,
        "invoice_number": "",
        "invoice_date": "",
        "transferred_to_trip": s(shipment_id),
        "transferred_at": datetime.now().strftime("%d.%m.%Y"),
        "transferred_from_trip": s(shipment_id),
        "advance_2": 0,
        "advance_date_2": "",
        "paid_to_2": "",
    }
    append_row("shipments", trace)

    log_transfer(shipment_id, source_trip_id, target_trip_id,
                 source_position, new_position, user_login,
                 note="Перенос авто")

    invalidate_cache("shipments")
    return True


def reassign_transfer_for_car(car_id, new_target_trip_id, new_position,
                              user_login, role):
    ws = get_ws_cached("shipments")
    car_row = _find_row_index_by_id("shipments", car_id)
    if car_row is None:
        return False, "Авто не найдено"

    rows = read_all_cached("shipments")
    src = None
    for r in rows:
        if s(r.get("id")) == s(car_id):
            src = r
            break
    if src is None:
        return False, "Авто не найдено"

    old_target = s(src.get("transferred_to_trip", "")).strip()
    if not old_target:
        return False, "У этого авто нет переноса — нечего переназначать"

    old_position = s(src.get("position", ""))
    old_trip_id = s(src.get("trip_id", ""))
    source_trip_id = s(src.get("transferred_from_trip", "")).strip() or old_trip_id

    payload = [
        {"range": "B" + str(car_row), "values": [[s(new_target_trip_id)]]},
        {"range": "C" + str(car_row), "values": [[new_position]]},
        {"range": "AA" + str(car_row), "values": [[s(new_target_trip_id)]]},
        {"range": "AB" + str(car_row), "values": [[datetime.now().strftime("%d.%m.%Y")]]},
        {"range": "AC" + str(car_row), "values": [[source_trip_id]]},
    ]
    ws.batch_update(payload, value_input_option="USER_ENTERED")

    try:
        all_vals = ws.get_all_values()
        for i, row in enumerate(all_vals[1:], start=2):
            if len(row) < 32:
                row = row + [""] * (32 - len(row))
            car_model = s(row[3])
            aa_val = s(row[26]) if len(row) > 26 else ""
            ac_val = s(row[28]) if len(row) > 28 else ""
            if car_model.startswith("Перенесён:") and (
                    ac_val == s(car_id) or aa_val == s(car_id)):
                ws.batch_update([
                    {"range": "B" + str(i), "values": [[source_trip_id]]},
                    {"range": "AA" + str(i), "values": [[s(car_id)]]},
                    {"range": "AB" + str(i), "values": [[datetime.now().strftime("%d.%m.%Y")]]},
                    {"range": "AC" + str(i), "values": [[s(car_id)]]},
                ], value_input_option="USER_ENTERED")
    except Exception as e:
        _log_system_error("reassign_update_trace", car_id, e)

    log_transfer(car_id, source_trip_id, new_target_trip_id,
                 old_position, new_position, user_login,
                 note="Переназначение переноса вручную")

    invalidate_cache("shipments")
    return True, ""


def update_trace_shipment(trace_id, new_car_model, new_vin, new_delivery_city,
                          new_position=None):
    ws = get_ws_cached("shipments")
    row_idx = _find_row_index_by_id("shipments", trace_id)
    if row_idx is None:
        return False
    payload = [
        {"range": "D" + str(row_idx), "values": [[s(new_car_model)]]},
        {"range": "J" + str(row_idx), "values": [[s(new_vin)]]},
        {"range": "I" + str(row_idx), "values": [[s(new_delivery_city)]]},
    ]
    if new_position is not None:
        payload.append({"range": "C" + str(row_idx),
                        "values": [[int(new_position)]]})
    ws.batch_update(payload, value_input_option="USER_ENTERED")
    invalidate_cache("shipments")
    return True


def delete_trace_shipment(trace_id):
    ws = get_ws_cached("shipments")
    row_idx = _find_row_index_by_id("shipments", trace_id)
    if row_idx is None:
        return False
    ws.delete_rows(row_idx)
    invalidate_cache("shipments")
    return True


def find_parent_for_trace(trace_row, all_shipments):
    parent_id = s(trace_row.get("transferred_from_trip", "")).strip()
    if parent_id:
        for c in all_shipments:
            if s(c.get("id")) == parent_id:
                return parent_id
    trace_vin = s(trace_row.get("vin", "")).strip()
    trace_car = s(trace_row.get("car_model", "")).replace(
        "Перенесён: ", "").strip()
    tr_from = s(trace_row.get("trip_id", "")).strip()
    for c in all_shipments:
        if s(c.get("id")) == s(trace_row.get("id")):
            continue
        if s(c.get("car_model", "")).startswith("Перенесён:"):
            continue
        if s(c.get("transferred_to_trip", "")):
            continue
        c_vin = s(c.get("vin", "")).strip()
        c_car = s(c.get("car_model", "")).strip()
        if trace_vin and c_vin and trace_vin == c_vin:
            return s(c.get("id"))
        if trace_car and c_car and trace_car == c_car \
                and s(c.get("trip_id")) == tr_from:
            return s(c.get("id"))
    return ""


def cancel_transfer(shipment_id, user_login, role, new_position=None):
    ws = get_ws_cached("shipments")
    row_idx = _find_row_index_by_id("shipments", shipment_id)
    if row_idx is None:
        return False, "Авто не найдено"

    rows = read_all_cached("shipments")
    src = None
    for r in rows:
        if s(r.get("id")) == s(shipment_id):
            src = r
            break
    if src is None:
        return False, "Авто не найдено"

    source_trip_id = s(src.get("transferred_from_trip", "")).strip()
    if not source_trip_id:
        return False, "Нет данных о переносе — отменить нечего"

    current_trip_id = s(src.get("trip_id", ""))
    current_position = s(src.get("position", ""))

    if new_position is None:
        siblings = [x for x in rows
                    if s(x.get("trip_id")) == source_trip_id
                    and s(x.get("id")) != s(shipment_id)
                    and not s(x.get("transferred_to_trip", ""))]
        new_position = next_free_position(siblings) or 1

    payload = [
        {"range": "B" + str(row_idx), "values": [[source_trip_id]]},
        {"range": "C" + str(row_idx), "values": [[new_position]]},
        {"range": "AA" + str(row_idx), "values": [[""]]},
        {"range": "AB" + str(row_idx), "values": [[datetime.now().strftime("%d.%m.%Y")]]},
        {"range": "AC" + str(row_idx), "values": [[""]]},
    ]
    ws.batch_update(payload, value_input_option="USER_ENTERED")

    try:
        ws2 = get_ws_cached("shipments")
        all_vals = ws2.get_all_values()
        to_del = []
        for i, row in enumerate(all_vals[1:], start=2):
            if len(row) < 32:
                row = row + [""] * (32 - len(row))
            car_model = s(row[3]) if len(row) > 3 else ""
            ac_val = s(row[28]) if len(row) > 28 else ""
            aa_val = s(row[26]) if len(row) > 26 else ""
            if car_model.startswith("Перенесён:") and (
                    ac_val == s(shipment_id) or aa_val == s(shipment_id)):
                to_del.append(i)
        for i in reversed(to_del):
            try:
                ws2.delete_rows(i)
            except Exception:
                pass
    except Exception as e:
        _log_system_error("cancel_transfer_del_trace", shipment_id, e)

    log_transfer(shipment_id,
                 current_trip_id, source_trip_id,
                 current_position, new_position,
                 user_login,
                 note="Отмена переноса",
                 cancelled="1")

    invalidate_cache("shipments")
    return True, ""


def get_shipment_history(shipment_id):
    rows = read_all_cached("transfer_history")
    result = [r for r in rows if s(r.get("shipment_id")) == s(shipment_id)]
    result.sort(key=lambda r: s(r.get("ts")), reverse=True)
    return result


# ============================================================
# АКТЫ
# ============================================================

def render_act_html(trip, shipment):
    route = s(trip.get("route", "")) if trip else ""
    loading_from = s(trip.get("loading_from", "")) if trip else ""
    loading_contract_number = s(trip.get("loading_contract_number", "")) if trip else ""
    car_model = s(shipment.get("car_model", ""))
    client = s(shipment.get("client", ""))
    customer = s(shipment.get("customer", ""))
    vin = s(shipment.get("vin", "")).strip()[:17]
    receiver = client if client else customer

    LINE_LONG = "_" * 30
    LINE_SHORT = "_" * 12

    p = []
    p.append("<!DOCTYPE html>")
    p.append('<html xmlns:o="urn:schemas-microsoft-com:office:office" '
             'xmlns:w="urn:schemas-microsoft-com:office:word" '
             'xmlns="http://www.w3.org/TR/REC-html40">')
    p.append("<head>")
    p.append('<meta charset="utf-8">')
    p.append("<title>Акт приема-передачи</title>")
    p.append("<style>")
    p.append("@page { size: A4; margin: 1.5cm; }")
    p.append('body { font-family: "Times New Roman", Times, serif; font-size: 14pt; line-height: 1.5; }')
    p.append("h1 { text-align: center; font-size: 16pt; text-transform: uppercase; margin-bottom: 20px; }")
    p.append("p { margin: 8px 0; white-space: nowrap; }")
    p.append('.mono { font-family: "Courier New", monospace; font-size: 14pt; }')
    p.append(".small { font-size: 12pt; }")
    p.append("</style>")
    p.append("</head>")
    p.append("<body>")
    p.append("<h1>Акт приема-передачи транспортного средства</h1>")
    p.append("<p><b>Перевозчик:</b> ИП Сагитдинов Максим Наильевич, тел. 8-987-131-00-62</p>")
    p.append("<p><b>Заказчик / Получатель:</b> " + receiver + "</p>")
    p.append("<p><b>Марка автомобиля:</b> " + car_model + "</p>")
    p.append("<p><b>VIN:</b> " + (vin if vin else "_" * 20) + "</p>")
    p.append("<p><b>Маршрут:</b> " + route + "</p>")
    if loading_from:
        loading_line = "<p><b>Погрузка от:</b> " + loading_from
        if loading_contract_number:
            loading_line += " &nbsp;·&nbsp; <b>Договор №:</b> " + loading_contract_number
        p.append(loading_line + "</p>")
    p.append("<p>&nbsp;</p>")
    p.append('<p><b>Груз сдал:</b> <span class="mono">' + LINE_LONG +
             "</span> / Сагитдинов М.Н. /</p>")
    p.append('<p><b>Груз принял:</b> <span class="mono">' + LINE_LONG +
             "</span> / " + receiver + " /</p>")
    p.append("<p>&nbsp;</p>")
    p.append('<p><b>Дата вручения груза:</b> <span class="mono">' + LINE_SHORT +
             '</span> &nbsp;&nbsp; <b>Время:</b> <span class="mono">' + LINE_SHORT + "</span></p>")
    p.append('<p class="small" style="margin-top: 25px;">'
             'При подписании акта приема-передачи на момент вручения груза '
             'Стороны каких-либо претензий друг к другу не имеют.</p>')
    p.append("</body>")
    p.append("</html>")
    return "".join(p)


def render_act_text(trip, shipment):
    route = s(trip.get("route", "")) if trip else ""
    loading_from = s(trip.get("loading_from", "")) if trip else ""
    loading_contract_number = s(trip.get("loading_contract_number", "")) if trip else ""
    car_model = s(shipment.get("car_model", ""))
    client = s(shipment.get("client", ""))
    customer = s(shipment.get("customer", ""))
    vin = s(shipment.get("vin", "")).strip()[:17]
    receiver = client if client else customer

    LINE_LONG = "_" * 30
    LINE_SHORT = "_" * 12

    lines = [
        "АКТ ПРИЕМА-ПЕРЕДАЧИ ТРАНСПОРТНОГО СРЕДСТВА", "",
        "Перевозчик: ИП Сагитдинов Максим Наильевич, тел. 8-987-131-00-62",
        "Заказчик / Получатель: " + receiver,
        "Марка автомобиля: " + car_model,
        "VIN: " + (vin if vin else "_" * 20),
        "Маршрут: " + route,
    ]
    if loading_from:
        loading_line = "Погрузка от: " + loading_from
        if loading_contract_number:
            loading_line += " · Договор № " + loading_contract_number
        lines.append(loading_line)
    lines += [
        "", "",
        "Груз сдал: " + LINE_LONG + " / Сагитдинов М.Н. /",
        "Груз принят: " + LINE_LONG + " / " + receiver + " /", "", "",
        "Дата вручения груза: " + LINE_SHORT + "   Время: " + LINE_SHORT,
        "",
        "При подписании акта приема-передачи на момент вручения груза Стороны каких-либо претензий друг другу не имеют.",
    ]
    return "\n".join(lines)


def log_act_print(login, trip_id, shipment_id, client):
    ws = get_ws_cached("act_log")
    row = [
        new_uuid(),
        datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        s(login), s(trip_id), s(shipment_id), s(client),
    ]
    ws.append_row(row)
    invalidate_cache("act_log")


def show_act(trip, shipment):
    u = st.session_state["user"]
    log_act_print(u["login"], trip.get("id") if trip else "",
                  shipment.get("id"), shipment.get("client"))
    st.info("Нажмите **Ctrl+P** для печати или скачайте акт кнопкой ниже.")
    safe_car = s(shipment.get("car_model", "")).replace("/", "-").replace("\\", "-")
    safe_client = s(shipment.get("client", "")).replace("/", "-").replace("\\", "-")
    base_name = "Акт_" + safe_car + "_" + safe_client
    col1, col2 = st.columns(2)
    with col1:
        st.download_button("Скачать акт (.doc)",
            data=render_act_html(trip, shipment).encode("utf-8"),
            file_name=base_name + ".doc", mime="application/msword",
            key="dl_doc_" + s(shipment.get("id")))
    with col2:
        st.download_button("Скачать акт (.txt)",
            data=render_act_text(trip, shipment).encode("utf-8-sig"),
            file_name=base_name + ".txt", mime="text/plain",
            key="dl_txt_" + s(shipment.get("id")))
    st.components.v1.html(render_act_html(trip, shipment), height=850, scrolling=True)


# ============================================================
# ПЕЧАТЬ СПИСКА
# ============================================================

def render_print_list_doc(rows, title="СПИСОК ПЕРЕВОЗИМЫХ АВТОМОБИЛЕЙ"):
    groups = []
    cur_key = None
    cur_group = None
    for r in rows:
        key = (s(r.get("tractor")), s(r.get("driver")),
               s(r.get("loading_from")), s(r.get("loading_contract_number")),
               s(r.get("route")), s(r.get("dep")))
        if key != cur_key:
            if cur_group:
                groups.append(cur_group)
            cur_key = key
            cur_group = {
                "tractor": s(r.get("tractor")),
                "driver": s(r.get("driver")),
                "loading_from": s(r.get("loading_from")),
                "loading_contract_number": s(r.get("loading_contract_number")),
                "route": s(r.get("route")),
                "dep": s(r.get("dep")),
                "cars": [],
            }
        cur_group["cars"].append(r)
    if cur_group:
        groups.append(cur_group)

    p = []
    p.append("<!DOCTYPE html>")
    p.append('<html xmlns:o="urn:schemas-microsoft-com:office:office" '
             'xmlns:w="urn:schemas-microsoft-com:office:word" '
             'xmlns="http://www.w3.org/TR/REC-html40">')
    p.append("<head>")
    p.append('<meta charset="utf-8">')
    p.append("<title>" + title + "</title>")
    p.append("<style>")
    p.append("@page { size: A4 landscape; margin: 0.6cm 0.7cm 0.6cm 0.7cm; }")
    p.append("html, body { margin: 0; padding: 0; }")
    p.append('body { font-family: "Times New Roman", Times, serif; '
             'font-size: 11pt; line-height: 1.2; color: #000; }')
    p.append("h1 { text-align: center; font-size: 14pt; "
             "text-transform: uppercase; margin: 0 0 8px 0; "
             "font-weight: bold; }")

    p.append(".trip-block { page-break-after: always; }")
    p.append(".trip-block:last-child { page-break-after: auto; }")

    p.append(".header-trip { "
             "background: #f0f0f0; border: 1px solid #333; "
             "padding: 4px 8px; margin-bottom: 4px; "
             "font-size: 11pt; line-height: 1.25; }")
    p.append(".header-trip b { font-weight: bold; }")

    p.append("table { width: 100%; border-collapse: collapse; "
             "table-layout: fixed; }")
    p.append("th, td { border: 1px solid #333; "
             "padding: 3px 5px; vertical-align: top; "
             "font-size: 11pt; line-height: 1.2; "
             "overflow: hidden; }")
    p.append("th { background: #e0e0e0; font-weight: bold; "
             "text-align: left; padding: 3px 5px; }")

    p.append("col.c-num    { width: 4%; }")
    p.append("col.c-model  { width: 21%; }")
    p.append("col.c-vin    { width: 17%; }")
    p.append("col.c-fio    { width: 16%; }")
    p.append("col.c-city   { width: 13%; }")
    p.append("col.c-debt   { width: 12%; }")
    p.append("col.c-note   { width: 17%; }")

    p.append(".num { text-align: center; }")
    p.append(".debt { text-align: right; font-weight: bold; "
             "white-space: nowrap; }")
    p.append(".vin { font-family: 'Courier New', monospace; "
             "font-size: 9.5pt; word-break: break-all; }")
    p.append(".note { font-size: 10pt; color: #222; }")
    p.append("tr.trace td { background: #f5f5f5; font-style: italic; }")
    p.append("tr.total td { background: #f0f0f0; font-weight: bold; "
             "border-top: 1.5px solid #333; }")
    p.append("</style></head><body>")
    p.append("<h1>" + title + "</h1>")

    for g in groups:
        p.append('<div class="trip-block">')

        p.append('<div class="header-trip">')
        p.append('<b>Дата выезда:</b> ' + s(g["dep"]))
        p.append(' &nbsp;·&nbsp; <b>Тягач:</b> ' + s(g["tractor"]))
        p.append(' &nbsp;·&nbsp; <b>Водитель:</b> ' + s(g["driver"]))
        if s(g.get("loading_from", "")):
            p.append(' &nbsp;·&nbsp; <b>Погрузка от:</b> ' + s(g["loading_from"]))
            if s(g.get("loading_contract_number", "")):
                p.append(' &nbsp;·&nbsp; <b>Договор №:</b> ' + s(g["loading_contract_number"]))
        if s(g["route"]):
            p.append(' &nbsp;·&nbsp; <b>Маршрут:</b> ' + s(g["route"]))
        p.append("</div>")

        p.append("<table>")
        p.append("<colgroup>")
        p.append('<col class="c-num">')
        p.append('<col class="c-model">')
        p.append('<col class="c-vin">')
        p.append('<col class="c-fio">')
        p.append('<col class="c-city">')
        p.append('<col class="c-debt">')
        p.append('<col class="c-note">')
        p.append("</colgroup>")

        p.append("<thead><tr>")
        p.append('<th class="num">№</th>')
        p.append("<th>Марка / модель</th>")
        p.append('<th class="vin">VIN</th>')
        p.append("<th>ФИО</th>")
        p.append("<th>Город доставки</th>")
        p.append('<th class="debt">Задолж., ₽</th>')
        p.append("<th>Примечание</th>")
        p.append("</tr></thead><tbody>")

        total_debt = 0.0
        for i, r in enumerate(g["cars"], start=1):
            fio = s(r.get("client", "")) or s(r.get("customer", ""))
            debt = to_float(r.get("debt"))
            total_debt += debt
            note = s(r.get("note", ""))
            row_class = ' class="trace"' if note else ""
            p.append("<tr" + row_class + ">")
            p.append('<td class="num">' + s(i) + "</td>")
            p.append("<td>" + s(r.get("car_model", "")) + "</td>")
            p.append('<td class="vin">' + (s(r.get("vin", ""))[:17] or "—")
                     + "</td>")
            p.append("<td>" + fio + "</td>")
            p.append("<td>" + s(r.get("delivery_city", "")) + "</td>")
            p.append('<td class="debt">' + fmt_money(debt) + "</td>")
            p.append('<td class="note">' + note + "</td>")
            p.append("</tr>")

        p.append('<tr class="total">')
        p.append('<td colspan="5" style="text-align:right;">Итого по рейсу:</td>')
        p.append('<td class="debt">' + fmt_money(total_debt) + "</td>")
        p.append("<td></td>")
        p.append("</tr>")

        p.append("</tbody></table>")
        p.append("</div>")

    p.append("</body></html>")
    return "".join(p)


def show_print_list(rows):
    st.info("Скачайте файл — он откроется в Word или LibreOffice. "
            "Для PDF: откройте файл и выберите «Сохранить как PDF». "
            "Каждый рейс печатается на отдельном альбомном листе A4.")
    doc_html = render_print_list_doc(rows)
    st.download_button(
        "📥 Скачать список (.doc)",
        data=doc_html.encode("utf-8"),
        file_name="Список_авто.doc",
        mime="application/msword",
        key="dl_print_doc_" + str(len(rows)),
    )
    st.components.v1.html(doc_html, height=1000, scrolling=True)


# ============================================================
# ФОРМА АВТО
# ============================================================

def render_shipment_form(form_key, c=None, submit_label="Сохранить авто",
                         default_position=1):
    """Форма автомобиля + до трёх отдельных платежей клиента."""
    defaults = {
        "position": int(default_position or 1),
        "car_model": "", "client": "", "customer": "", "vin": "",
        "delivery_city": "", "amount": "", "payer_type": PAYER_TYPES[0],
        "contract_number": "",
    }
    if c:
        defaults["position"] = max(1, min(MAX_CARS, int(to_float(c.get("position")) or 1)))
        defaults["car_model"] = s(c.get("car_model", ""))
        defaults["client"] = s(c.get("client", ""))
        defaults["customer"] = s(c.get("customer", ""))
        defaults["vin"] = s(c.get("vin", ""))[:17]
        defaults["delivery_city"] = s(c.get("delivery_city", ""))
        amount_val = c.get("amount", "")
        defaults["amount"] = "" if amount_val in ("", None) else "{:.2f}".format(to_float(amount_val)).replace(".", ",")
        defaults["payer_type"] = s(c.get("payer_type", "")) or PAYER_TYPES[0]
        defaults["contract_number"] = s(c.get("contract_number", ""))
        payment_defaults = get_car_payments(c)
    else:
        payment_defaults = normalize_car_payments([])

    try:
        payer_index = PAYER_TYPES.index(defaults["payer_type"])
    except ValueError:
        payer_index = 0

    with st.form(form_key, clear_on_submit=False):
        st.markdown("**Данные автомобиля**")
        col1, col2 = st.columns(2)
        position = col1.number_input(
            "Позиция (1–8)", min_value=1, max_value=MAX_CARS, step=1,
            value=defaults["position"], key=form_key + "_pos")
        car_model = col2.text_input(
            "Марка / модель авто", value=defaults["car_model"], key=form_key + "_car")

        col3, col4 = st.columns(2)
        client = col3.text_input("ФИО клиента", value=defaults["client"], key=form_key + "_client")
        customer = col4.text_input(
            "Заказчик (если нет ФИО клиента)", value=defaults["customer"], key=form_key + "_customer")

        col5, col6 = st.columns(2)
        vin = col5.text_input("VIN (до 17 символов)", value=defaults["vin"], key=form_key + "_vin")
        delivery_city = col6.text_input(
            "Город доставки", value=defaults["delivery_city"], key=form_key + "_city")

        col7, col8 = st.columns(2)
        amount = col7.text_input(
            "Сумма за перевозку", value=defaults["amount"],
            placeholder="235000,00", key=form_key + "_amount")
        payer_type = col8.selectbox(
            "Тип оплаты", PAYER_TYPES, index=payer_index, key=form_key + "_payer")

        contract_number = st.text_input(
            "№ договора", value=defaults["contract_number"], key=form_key + "_contract")

        st.markdown("---")
        st.markdown("#### 💳 Платежи клиента")
        st.caption(
            "Можно оставить платежи пустыми или разделить оплату на 1–3 части. "
            "Для каждой части укажите сумму, дату, кому передали деньги и комментарий."
        )

        payments = []
        for i in range(3):
            d = payment_defaults[i]
            st.markdown("**Платёж № " + str(i + 1) + "**")
            c_amt, c_date = st.columns(2)
            amount_default = "" if d["amount"] <= 0.01 else "{:.2f}".format(d["amount"]).replace(".", ",")
            p_amount = c_amt.text_input(
                "Сумма платежа", value=amount_default,
                key=form_key + "_pay" + str(i + 1) + "_amount")
            p_date = c_date.text_input(
                "Дата (ДД.ММ.ГГГГ)", value=s(d["date"]),
                key=form_key + "_pay" + str(i + 1) + "_date")

            c_to, c_comment = st.columns(2)
            p_to = c_to.text_input(
                "Кому оплатили", value=s(d["paid_to"]),
                placeholder="Водителю / кассиру / организации",
                key=form_key + "_pay" + str(i + 1) + "_to")
            p_comment = c_comment.text_input(
                "Комментарий", value=s(d["comment"]),
                placeholder="Аванс / доплата / перевод ...",
                key=form_key + "_pay" + str(i + 1) + "_comment")
            payments.append({
                "amount": p_amount, "date": p_date,
                "paid_to": p_to, "comment": p_comment,
            })

        entered_total = sum(money_value(x["amount"]) for x in payments)
        amount_check = money_value(amount)
        if amount_check > 0.01:
            remain = max(0.0, amount_check - entered_total)
            st.caption(
                "По введённым платежам: " + fmt_money(entered_total) + " ₽ · "
                "остаток: " + fmt_money(remain) + " ₽"
            )

        save = st.form_submit_button(submit_label)
        cancel = st.form_submit_button("Отмена")

    # Старые ключи возвращаются для совместимости с обработчиками,
    # но источником истины теперь является payments.
    normalized_for_legacy = normalize_car_payments(payments)
    p1, p2, p3 = normalized_for_legacy
    return {
        "position": position, "car_model": s(car_model), "client": s(client),
        "vin": s(vin), "delivery_city": s(delivery_city),
        "amount": amount, "payer_type": s(payer_type),
        "customer": s(customer), "contract_number": s(contract_number),
        "payments": payments,
        "date_pay": p3["date"], "paid_to": p1["paid_to"],
        "advance": p1["amount"], "advance_date": p1["date"],
        "advance_2": p2["amount"], "advance_date_2": p2["date"],
        "paid_to_2": p2["paid_to"],
        "save": save, "cancel": cancel,
    }
def parse_car_payments_ui(raw_payments):
    """Проверяет суммы и даты, введённые пользователем."""
    result = []
    for idx, src in enumerate(raw_payments or [], start=1):
        amount = money_value(src.get("amount", 0))
        raw_date = s(src.get("date", "")).strip()
        try:
            date_val = parse_date_ui(raw_date) if raw_date else ""
        except ValueError:
            raise ValueError("Платёж № " + str(idx) + ": неверная дата. Используйте ДД.ММ.ГГГГ")
        result.append({
            "amount": amount,
            "date": date_val,
            "paid_to": s(src.get("paid_to", "")).strip(),
            "comment": s(src.get("comment", "")).strip(),
        })
    return normalize_car_payments(result)


# ============================================================
# ШАПКА РЕЙСА
# ============================================================

def render_trip_header(trip_id, tractor, driver, route, dep, ret,
                       trip_completed, trip_completed_at,
                       active_count, issued_count,
                       total, total_advance, total_debt, total_nds,
                       has_nds=False,
                       trip_invoice_number="", trip_invoice_date="",
                       has_transfers=False, transfers_count=0,
                       incoming_count=0, incoming_from_labels=None,
                       incoming_total_amount=0.0,
                       incoming_total_advance=0.0,
                       incoming_total_debt=0.0,
                       total_in_route=None,
                       loading_from="", loading_contract_number="",
                       payment_date="", payment_comment="",
                       payments_info=None):
    if active_count == 0 and issued_count == 0 and not has_transfers and incoming_count == 0:
        bg, bd = "#f8fafc", "#cbd5e1"
    elif total_debt > 0.01:
        bg, bd = "#fff7f7", "#f2a3a3"
    else:
        bg, bd = "#f2fbf5", "#86d39d"

    title_parts = [s(tractor), s(driver)]
    if s(loading_from):
        loading_title = "погрузка от " + s(loading_from)
        if s(loading_contract_number):
            loading_title += " · договор № " + s(loading_contract_number)
        title_parts.append(loading_title)
    title_parts.append(s(route))
    title_parts.append("выезд " + s(dep))
    title = " — ".join([p for p in title_parts if p])
    if ret:
        title += " — возврат " + s(ret)

    if total_in_route is None:
        total_in_route = active_count + incoming_count

    stats = "авто: " + s(total_in_route) + "/" + s(MAX_CARS)
    if issued_count > 0:
        stats += "  |  выдано: " + s(issued_count)
    if transfers_count > 0:
        stats += "  |  перенесено из рейса: " + s(transfers_count)
    if incoming_count > 0:
        stats += "  |  перенесено в рейс: " + s(incoming_count)
    stats += ("  |  сумма: " + fmt_money(total)
              + "  |  аванс: " + fmt_money(total_advance)
              + "  |  задолженность: " + fmt_money(total_debt))
    if total_nds > 0:
        stats += "  |  НДС: " + fmt_money(total_nds)

    incoming_stats = ""
    if incoming_count > 0:
        incoming_stats = (
            "из них перенесено в рейс: " + s(incoming_count) + " авто"
            + "  |  сумма по ним: " + fmt_money(incoming_total_amount)
            + "  |  аванс: " + fmt_money(incoming_total_advance)
            + "  |  задолженность: " + fmt_money(incoming_total_debt)
        )

    inv_html = ""
    if has_nds:
        inv_num = s(trip_invoice_number).strip()
        inv_date = s(trip_invoice_date).strip()
        if inv_num or inv_date:
            inv_html = ('<span style="background:#1976d2; color:#fff; '
                        'padding:2px 10px; border-radius:12px; font-size:12px; '
                        'font-weight:bold; white-space:nowrap; margin-left:6px;">'
                        '📄 Счёт № ' + (inv_num or "—")
                        + (' от ' + date_to_display_safe(inv_date) if inv_date else '')
                        + '</span>')
        else:
            inv_html = ('<span style="background:#c62828; color:#fff; '
                        'padding:2px 10px; border-radius:12px; font-size:12px; '
                        'font-weight:bold; white-space:nowrap; margin-left:6px;">'
                        '📄 Счёт: не выставлен</span>')

    badges = ""
    if transfers_count > 0:
        badges += ('<span style="background:#b71c1c; color:#fff; '
                   'padding:2px 10px; border-radius:12px; font-size:12px; '
                   'font-weight:bold; white-space:nowrap; margin-left:6px;">'
                   '↗ перенесено: ' + s(transfers_count) + '</span>')
    if incoming_count > 0:
        badges += ('<span style="background:#4a148c; color:#fff; '
                   'padding:2px 10px; border-radius:12px; font-size:12px; '
                   'font-weight:bold; white-space:nowrap; margin-left:6px;">'
                   '↘ перенесено в рейс: ' + s(incoming_count) + '</span>')

    completed_html = ""
    if trip_completed:
        badge_text = "✅ Рейс завершён" + (
            ": " + s(trip_completed_at) if trip_completed_at else "")
        completed_html = (
            '<span style="background:#2e7d32; color:#fff; '
            'padding:2px 10px; border-radius:12px; font-size:12px; '
            'font-weight:bold; white-space:nowrap; margin-left:6px;">'
            + badge_text + '</span>')

    incoming_stats_html = ""
    if incoming_stats:
        incoming_stats_html = (
            '<div style="margin-top:4px; color:#4a148c; font-weight:bold; '
            'font-size:13px; word-wrap:break-word;">'
            '↘ ' + incoming_stats +
            '</div>'
        )

    incoming_html = ""
    if incoming_count > 0 and incoming_from_labels:
        labels_text = " · ".join(incoming_from_labels)
        incoming_html = (
            '<div style="background:#ede7f6; border-left:4px solid #5e35b1; '
            'padding:6px 10px; margin-top:6px; border-radius:4px; '
            'font-size:13px; word-wrap:break-word;">'
            '↘ <b>Авто перенесены из рейса:</b> ' + labels_text +
            '</div>'
        )

    loading_html = ""
    if s(loading_from):
        contract_html = (
            ' &nbsp;·&nbsp; Договор № ' + s(loading_contract_number)
            if s(loading_contract_number) else ''
        )
        loading_html = (
            '<div style="margin-top:4px; font-size:13px; '
            'color:#0d47a1; font-weight:bold; word-wrap:break-word;">'
            '📦 Погрузка от: ' + s(loading_from) + contract_html +
            '</div>'
        )

    payment_html = ""
    if has_nds:
        pd_disp = date_to_display_safe(payment_date) if s(payment_date).strip() else "—"
        pc_disp = s(payment_comment).strip() if s(payment_comment).strip() else "—"
        payment_html = (
            '<div style="background:#e3f2fd; border-left:4px solid #1976d2; '
            'padding:6px 10px; margin-top:6px; border-radius:4px; '
            'font-size:13px; word-wrap:break-word;">'
            '💳 <b>Дата оплаты:</b> ' + pd_disp
            + ' &nbsp;·&nbsp; 💬 <b>Комментарий:</b> ' + pc_disp
            + '</div>'
        )

    splits_html = ""
    if has_nds and payments_info:
        parts = payments_info.get("parts", [])
        paid_total = payments_info.get("paid_total", 0.0)
        nds_debt = payments_info.get("nds_debt", 0.0)
        nds_total = payments_info.get("nds_total", 0.0)
        paid_from_sum = payments_info.get("paid_from_sum", 0.0)
        parts_count = payments_info.get("parts_count", 0)
        is_paid = payments_info.get("is_paid", False)

        badge_color = "#2e7d32" if is_paid else "#f57c00"
        badge_text = ("✅ Оплачен" if is_paid
                      else "⏳ Частично оплачен")
        header = (
            '<div style="background:#fff8e1; border-left:4px solid #ffb300; '
            'padding:6px 10px; margin-top:6px; border-radius:4px; '
            'font-size:13px; word-wrap:break-word;">'
            '<b>🧾 Разделение счёта:</b> '
            '<span style="background:' + badge_color + '; color:#fff; '
            'padding:1px 8px; border-radius:10px; font-size:12px; '
            'font-weight:bold;">' + badge_text + '</span>'
            ' &nbsp;·&nbsp; <b>Оплачено от суммы:</b> '
            '<b style="color:#2e7d32;">' + fmt_money(paid_from_sum) + ' ₽</b>'
            ' из <b>' + fmt_money(nds_total) + ' ₽</b>'
            ' &nbsp;·&nbsp; <b>Задолженность:</b> '
            '<b style="color:#c62828;">' + fmt_money(nds_debt) + ' ₽</b>'
            ' &nbsp;·&nbsp; По разбивке: <b>' + fmt_money(paid_total) + ' ₽</b>'
            ' &nbsp;·&nbsp; Частей: <b>' + s(parts_count) + '</b>'
        )
        if parts:
            rows_html = "<br>"
            for p in parts:
                label = PAY_SPLIT_LABELS[p["idx"] - 1]
                rows_html += (
                    "&nbsp;&nbsp;• <b>" + s(p["idx"]) + ". " + label
                    + ":</b> " + fmt_money(p["amount"]) + " ₽"
                    + ("  · дата: " + s(p["date"]) if p["date"] else "")
                    + ("  · " + s(p["comment"]) if p["comment"] else "")
                    + "<br>"
                )
            header += rows_html
        header += "</div>"
        splits_html = header

    html = (
        '<div style="background-color:' + bg +
        '; border:2px solid ' + bd +
        '; border-radius:12px; padding:11px 14px; margin-bottom:9px; '
        'box-shadow:0 2px 9px rgba(15,23,42,.055); '
        'font-size:14px; word-wrap:break-word;">'
        '<div style="display:flex; justify-content:space-between; '
        'align-items:center; gap:10px; flex-wrap:wrap;">'
        '<div style="font-weight:bold; flex:1; min-width:200px;">'
        + title +
        '</div>'
        '<div>' + badges + completed_html + '</div>'
        '</div>'
        + loading_html +
        '<div style="color:#333; margin-top:4px; display:flex; '
        'align-items:center; flex-wrap:wrap;">'
        '<div>' + stats + '</div>'
        '<div>' + inv_html + '</div>'
        '</div>'
        + payment_html
        + splits_html
        + incoming_stats_html
        + incoming_html +
        '</div>'
    )
    return html


# ============================================================
# БЛОК ОПЛАТЫ РЕЙСА
# ============================================================

# v2.12.8: оплата рейса по безнал-НДС — только admin/director
def render_trip_payment_button(trip_id, cars, role, user_login,
                               trip_label="", container=None):
    if container is None:
        container = st

    # ранний выход для ролей без права pay_beznal_nds
    if not can("pay_beznal_nds", role):
        nds_cars = [x for x in cars if _is_beznal_nds(x)]
        if nds_cars:
            unpaid = 0.0
            for x in nds_cars:
                f = calculate_financials(x)
                if not check_paid(x.get("paid", "0")):
                    unpaid += f["debt"]
            if unpaid > 0.01:
                container.info(
                    "💵 Оплата рейса по «безнал с НДС» доступна "
                    "только директору и администратору."
                )
        return

    nds_cars = [x for x in cars
                if s(x.get("payer_type", "")).strip() == NDS_PAYER]
    if not nds_cars:
        return

    unpaid_debt = 0.0
    unpaid_count = 0
    paid_count = 0
    for x in nds_cars:
        f = calculate_financials(x)
        if check_paid(x.get("paid", "0")):
            paid_count += 1
        elif f["debt"] > 0.01:
            unpaid_debt += f["debt"]
            unpaid_count += 1

    key_confirm_pay = "confirm_pay_trip_nds_" + s(trip_id)
    key_confirm_unpay = "confirm_unpay_trip_nds_" + s(trip_id)

    if unpaid_count > 0:
        if not st.session_state.get(key_confirm_pay):
            if container.button(
                "💵 Оплатить весь рейс (безнал с НДС) — "
                + fmt_money(unpaid_debt) + " ₽ / " + s(unpaid_count) + " авто",
                key="btn_pay_trip_nds_" + s(trip_id),
                use_container_width=True,
            ):
                st.session_state[key_confirm_pay] = True
                st.rerun()
        else:
            container.warning(
                "Оплатить **" + s(unpaid_count) + "** авто на сумму **"
                + fmt_money(unpaid_debt) + " ₽**? Долг будет обнулён — "
                "исходные авансы останутся без изменений."
            )
            c1, c2 = container.columns(2)
            if c1.button("✅ Подтвердить оплату",
                         key="ok_" + key_confirm_pay,
                         use_container_width=True):
                paid, skipped, closed = pay_entire_trip_nds(
                    trip_id, user_login, role
                )
                st.session_state.pop(key_confirm_pay, None)
                if paid > 0:
                    st.success(
                        "Оплачено авто: " + s(paid)
                        + " · долг закрыт на " + fmt_money(closed) + " ₽"
                    )
                else:
                    st.info("Нечего оплачивать.")
                st.rerun()
            if c2.button("Отмена",
                         key="cancel_" + key_confirm_pay,
                         use_container_width=True):
                st.session_state.pop(key_confirm_pay, None)
                st.rerun()

    if paid_count > 0 and unpaid_count == 0:
        if not st.session_state.get(key_confirm_unpay):
            if container.button(
                "↩ Снять оплату с рейса (безнал с НДС)",
                key="btn_unpay_trip_nds_" + s(trip_id),
                use_container_width=True,
            ):
                st.session_state[key_confirm_unpay] = True
                st.rerun()
        else:
            container.warning(
                "Снять оплату с **" + s(paid_count) + "** авто? "
                "Доплата будет снята, исходные авансы сохранятся, долг появится снова."
            )
            c1, c2 = container.columns(2)
            if c1.button("✅ Подтвердить снятие",
                         key="ok_" + key_confirm_unpay,
                         use_container_width=True):
                cnt = unpay_entire_trip_nds(trip_id, user_login, role)
                st.session_state.pop(key_confirm_unpay, None)
                if cnt > 0:
                    st.success("Оплата снята с " + s(cnt) + " авто, долг возвращён.")
                else:
                    st.info("Нечего снимать.")
                st.rerun()
            if c2.button("Отмена",
                         key="cancel_" + key_confirm_unpay,
                         use_container_width=True):
                st.session_state.pop(key_confirm_unpay, None)
                st.rerun()


# ============================================================
# УПРАВЛЕНИЕ ПОЛЬЗОВАТЕЛЯМИ
# ============================================================

def _update_user_lockout(login, attempts, locked_until):
    ws = get_ws_cached("users")
    for i, row in enumerate(ws.get_all_values()[1:], start=2):
        if row[0] == login:
            payload = [
                {"range": "F" + str(i), "values": [[str(attempts)]]},
                {"range": "G" + str(i), "values": [[locked_until]]},
            ]
            ws.batch_update(payload, value_input_option="USER_ENTERED")
            break
    invalidate_cache("users")


def _upgrade_password_hash(login, new_hash):
    ws = get_ws_cached("users")
    for i, row in enumerate(ws.get_all_values()[1:], start=2):
        if row[0] == login:
            ws.update_cell(i, 2, new_hash)
            break
    invalidate_cache("users")


def admin_set_password(login, new_password):
    if not login or not new_password:
        return False, "Логин и пароль обязательны"
    if len(new_password) < 8:
        return False, "Пароль минимум 8 символов"
    ws = get_ws_cached("users")
    all_rows = ws.get_all_values()
    row_idx = None
    for i, row in enumerate(all_rows[1:], start=2):
        if s(row[0]).strip() == s(login).strip():
            row_idx = i
            break
    if row_idx is None:
        return False, "Пользователь не найден"
    new_hash = hash_password(new_password)
    ws.update_cell(row_idx, 2, new_hash)
    ws.update_cell(row_idx, 6, "0")
    ws.update_cell(row_idx, 7, "")
    invalidate_cache("users")
    return True, ""


def admin_unlock_user(login):
    ws = get_ws_cached("users")
    all_rows = ws.get_all_values()
    for i, row in enumerate(all_rows[1:], start=2):
        if s(row[0]).strip() == s(login).strip():
            ws.update_cell(i, 6, "0")
            ws.update_cell(i, 7, "")
            invalidate_cache("users")
            return True
    return False


# ============================================================
# АВТОРИЗАЦИЯ
# ============================================================

def login_page():
    st.title("Учёт рейсов — вход")
    with st.form("login"):
        login = st.text_input("Логин")
        pwd = st.text_input("Пароль", type="password")
        ok = st.form_submit_button("Войти")

    if ok:
        users = read_all("users", fresh=True)
        u = next((x for x in users if s(x.get("login")) == login), None)
        if not u:
            st.error("Пользователь не найден")
            log_action(login, "-", "login_fail", "не найден")
            return

        locked = check_lockout(u)
        if locked:
            st.error("Учётная запись заблокирована до "
                     + locked.strftime("%H:%M:%S"))
            log_action(login, u.get("role", "-"), "login_fail", "заблокирован")
            return

        if not is_active(u):
            st.error("Учётная запись отключена")
            log_action(login, u.get("role", "-"), "login_fail", "отключён")
            return

        if not verify_password(pwd, u.get("password_hash")):
            attempts = int(to_float(u.get("failed_attempts")) or 0) + 1
            lock_until = ""
            if attempts >= LOGIN_MAX_ATTEMPTS:
                lock_until = (datetime.now() + timedelta(minutes=LOGIN_LOCK_MINUTES)
                              ).strftime("%Y-%m-%d %H:%M:%S")
                st.error("Слишком много попыток. Блок на "
                         + s(LOGIN_LOCK_MINUTES) + " минут.")
            else:
                st.error("Неверный пароль")
            _update_user_lockout(login, attempts, lock_until)
            log_action(login, u.get("role", "-"), "login_fail",
                       "неверный пароль (попытка " + s(attempts) + ")")
            return

        if to_float(u.get("failed_attempts")) > 0 or s(u.get("locked_until")):
            _update_user_lockout(login, 0, "")

        if HAS_BCRYPT and needs_password_upgrade(u.get("password_hash")):
            try:
                _upgrade_password_hash(login, hash_password(pwd))
                log_action(login, u.get("role", "-"), "password_upgraded",
                           "SHA-256 → bcrypt")
            except Exception as e:
                _log_system_error("password_upgrade", login, e)

        st.session_state["user"] = {
            "login": u["login"], "role": u["role"], "full_name": u["full_name"],
        }
        log_action(u["login"], u["role"], "login_ok")
        st.rerun()


def logout():
    u = st.session_state.get("user")
    if u:
        log_action(u["login"], u["role"], "logout")
    st.session_state.pop("user", None)
    st.rerun()


def ensure_first_admin():
    # После ensure_sheets_once() не делаем лишний принудительный запрос к API.
    # append_row/invalidate_cache сами очищают кэш при создании пользователя.
    users = read_all("users")
    if not users:
        st.warning("Первый запуск: создайте аккаунт администратора")
        with st.form("create_admin"):
            login = st.text_input("Логин администратора", value="admin")
            pwd = st.text_input("Пароль (минимум 8 символов)", type="password")
            pwd2 = st.text_input("Повторите пароль", type="password")
            name = st.text_input("ФИО администратора")
            ok = st.form_submit_button("Создать админа")
        if ok:
            if not login or not pwd or not name:
                st.error("Заполните все поля")
            elif pwd != pwd2:
                st.error("Пароли не совпадают")
            elif len(pwd) < 8:
                st.error("Пароль минимум 8 символов")
            else:
                append_row("users", {
                    "login": login, "password_hash": hash_password(pwd),
                    "role": "admin", "full_name": name, "active": "1",
                    "failed_attempts": "0", "locked_until": "",
                })
                st.success("Админ " + login + " создан. Войдите.")
                st.rerun()
        st.stop()


# v2.12.8: право pay_beznal_nds — только у admin и director
def can(action, role):
    ADMIN_DIRECTOR = {
        "create_trip", "edit_trip", "delete_trip",
        "create_ship", "edit_ship", "delete_ship",
        "export", "print_act", "manage_users", "view_log",
        "archive", "complete_trip", "transfer_ship",
        "cancel_transfer", "edit_invoice", "edit_trace",
        "manage_passwords", "edit_payment", "split_payment",
        "pay_beznal_nds",
    }
    DISPATCHER = {
        "create_trip", "edit_trip", "delete_trip",
        "create_ship", "edit_ship", "delete_ship",
        "export", "print_act", "manage_users", "view_log",
        "archive", "complete_trip", "transfer_ship",
        "cancel_transfer", "edit_invoice", "edit_trace",
        "manage_passwords", "edit_payment", "split_payment",
    }
    LOGIST = {
        "create_trip", "edit_trip", "create_ship", "edit_ship",
        "export", "print_act", "archive", "complete_trip",
        "transfer_ship", "cancel_transfer", "edit_invoice",
        "edit_trace", "edit_payment", "split_payment",
    }
    rights = {
        "admin":      ADMIN_DIRECTOR,
        "director":   ADMIN_DIRECTOR,
        "dispatcher": DISPATCHER,
        "logist":     LOGIST,
    }
    return action in rights.get(role, set())


# ============================================================
# АДМИН-ПАНЕЛЬ
# ============================================================

def admin_panel():
    admin_role = st.session_state["user"]["role"]
    admin_login = st.session_state["user"]["login"]

    st.header("Админ-панель")
    tab1, tab2, tab3 = st.tabs(
        ["Пользователи", "Журнал действий", "Журнал печати актов"])

    with tab1:
        st.subheader("Пользователи")
        users = read_all("users", fresh=True)

        for u in users:
            login_u = s(u.get("login"))
            role_u = s(u.get("role"))
            name_u = s(u.get("full_name"))
            active_u = is_active(u)
            attempts = int(to_float(u.get("failed_attempts")) or 0)
            locked_until = s(u.get("locked_until", "")).strip()
            is_locked = bool(locked_until)

            st.markdown(
                "---\n"
                "**" + login_u + "** · роль: `" + role_u + "` · " + name_u
                + ("  \n🟢 активен" if active_u else "  \n🔴 отключён")
                + ("  \n🔒 **заблокирован** до " + locked_until if is_locked else "")
                + ("  \n⚠️ неудачных попыток: " + s(attempts) if attempts > 0 else "")
            )

            c1, c2, c3, c4 = st.columns(4)

            if c1.button("🔑 Сменить пароль",
                         key="btn_pwd_" + login_u,
                         use_container_width=True):
                st.session_state["show_pwd_form_" + login_u] = True
                st.rerun()

            if is_locked or attempts > 0:
                if c2.button("🔓 Разблокировать",
                             key="btn_unlock_" + login_u,
                             use_container_width=True):
                    if admin_unlock_user(login_u):
                        log_action(admin_login, admin_role, "unlock_user", login_u)
                        st.success("Блокировка снята: " + login_u)
                        st.rerun()

            if c3.button("Отключить" if active_u else "Включить",
                         key="toggle_user_" + login_u,
                         use_container_width=True):
                ws = get_ws_cached("users")
                all_rows = ws.get_all_values()
                for i, row in enumerate(all_rows[1:], start=2):
                    if s(row[0]).strip() == login_u:
                        new_val = "0" if active_u else "1"
                        ws.update_cell(i, 5, new_val)
                        invalidate_cache("users")
                        log_action(admin_login, admin_role, "toggle_user",
                                   login_u + " -> " + new_val)
                        st.rerun()

            if st.session_state.get("show_pwd_form_" + login_u):
                with st.form("pwd_form_" + login_u):
                    st.markdown("**Смена пароля для `" + login_u + "`**")
                    new_pwd = st.text_input("Новый пароль (минимум 8 символов)",
                                            type="password",
                                            key="new_pwd_" + login_u)
                    new_pwd2 = st.text_input("Повторите пароль",
                                             type="password",
                                             key="new_pwd2_" + login_u)
                    col_a, col_b = st.columns(2)
                    ok = col_a.form_submit_button("💾 Сохранить пароль")
                    cancel = col_b.form_submit_button("Отмена")
                if cancel:
                    st.session_state.pop("show_pwd_form_" + login_u, None)
                    st.rerun()
                if ok:
                    if not new_pwd or not new_pwd2:
                        st.error("Заполните оба поля")
                    elif new_pwd != new_pwd2:
                        st.error("Пароли не совпадают")
                    elif len(new_pwd) < 8:
                        st.error("Пароль минимум 8 символов")
                    else:
                        success, err = admin_set_password(login_u, new_pwd)
                        if success:
                            log_action(admin_login, admin_role, "admin_set_password",
                                       "пользователь: " + login_u)
                            st.session_state.pop("show_pwd_form_" + login_u, None)
                            st.success("Пароль для " + login_u + " обновлён. "
                                       "Блокировка снята, счётчик попыток сброшен.")
                            st.rerun()
                        else:
                            st.error("Не удалось: " + s(err))

        st.markdown("---")
        st.subheader("Добавить пользователя")
        with st.form("add_user"):
            new_login = st.text_input("Логин")
            new_pwd = st.text_input("Пароль", type="password")
            new_role = st.selectbox("Роль", ROLES)
            new_name = st.text_input("ФИО")
            ok = st.form_submit_button("Создать")
        if ok:
            if not new_login or not new_pwd or not new_name:
                st.error("Заполните все поля")
            elif len(new_pwd) < 8:
                st.error("Пароль минимум 8 символов")
            elif any(s(x.get("login")) == new_login for x in users):
                st.error("Такой логин уже есть")
            else:
                append_row("users", {
                    "login": new_login, "password_hash": hash_password(new_pwd),
                    "role": new_role, "full_name": new_name, "active": "1",
                    "failed_attempts": "0", "locked_until": "",
                })
                log_action(admin_login, admin_role,
                           "create_user", new_login + " / " + new_role)
                st.success("Пользователь " + new_login + " создан")
                st.rerun()

    with tab2:
        st.subheader("Журнал действий")
        rows = list(reversed(read_all("audit_log")))[:200]
        if rows:
            st.dataframe(safe_df(rows), use_container_width=True)
        else:
            st.info("Пока пусто")

    with tab3:
        st.subheader("Журнал печати актов")
        rows = list(reversed(read_all("act_log")))[:200]
        if rows:
            st.dataframe(safe_df(rows), use_container_width=True)
        else:
            st.info("Пока пусто")


# ============================================================
# ФОРМА РАЗДЕЛЕНИЯ СЧЁТА НА 3 ПЛАТЕЖА
# ============================================================

def render_trip_split_form(trip_id, trip, nds_debt, nds_total,
                           user_login, role):
    parts = extract_trip_payments(trip)
    paid_total = compute_trip_payments_total(trip)
    to_split = nds_debt - paid_total
    if to_split < 0.01:
        to_split = 0.0

    st.markdown("---")
    st.markdown("### 🧾 Разделение счёта на части")

    badge_color = "#2e7d32" if to_split <= 0.01 else "#f57c00"
    badge_text = "✅ Оплачен" if to_split <= 0.01 else "⏳ Частично оплачен"
    st.markdown(
        '<div style="background:#fff8e1; border-left:4px solid #ffb300; '
        'padding:8px 12px; border-radius:4px; font-size:14px;">'
        '<b>Задолженность (безнал с НДС):</b> ' + fmt_money(nds_debt) + ' ₽'
        ' &nbsp;·&nbsp; <b>Разнесено по частям:</b> ' + fmt_money(paid_total) + ' ₽'
        ' &nbsp;·&nbsp; <b>Осталось разнести:</b> <span style="color:'
        + ("#2e7d32" if to_split <= 0.01 else "#c62828") + '; font-weight:bold;">'
        + fmt_money(to_split) + ' ₽</span>'
        ' &nbsp;·&nbsp; <span style="background:' + badge_color + '; color:#fff; '
        'padding:1px 8px; border-radius:10px; font-size:12px; font-weight:bold;">'
        + badge_text + '</span>'
        '</div>',
        unsafe_allow_html=True,
    )

    if parts:
        rows_html = ""
        for p in parts:
            label = PAY_SPLIT_LABELS[p["idx"] - 1]
            rows_html += (
                "• <b>" + s(p["idx"]) + ". " + label + ":</b> "
                + fmt_money(p["amount"]) + " ₽"
                + ("  · дата: " + s(p["date"]) if p["date"] else "")
                + ("  · " + s(p["comment"]) if p["comment"] else "")
                + "<br>"
            )
        st.markdown(
            '<div style="background:#f5f5f5; border:1px solid #cccccc; '
            'border-radius:6px; padding:8px 12px; font-size:13px;">'
            + rows_html + '</div>',
            unsafe_allow_html=True,
        )

    form_key = "split_form_" + s(trip_id)
    with st.form(form_key):
        st.markdown("**Задать до 3 платежей** (пустые части игнорируются)")
        new_payments = []
        for i in range(1, MAX_PAY_SPLITS + 1):
            label = PAY_SPLIT_LABELS[i - 1]
            cur_amt = ""
            cur_date = ""
            cur_comm = ""
            for p in parts:
                if p["idx"] == i:
                    cur_amt = "{:.2f}".format(p["amount"]).replace(".", ",") if p["amount"] > 0 else ""
                    cur_date = p["date"]
                    cur_comm = p["comment"]
                    break

            st.markdown(
                '<div style="background:#fafafa; border:1px dashed #cccccc; '
                'border-radius:6px; padding:8px 10px; margin-bottom:6px;">'
                '<b>Платёж №' + s(i) + ' — ' + label + '</b>'
                '</div>',
                unsafe_allow_html=True,
            )
            c1, c2, c3 = st.columns([1, 1, 2])
            amt_in = c1.text_input(
                "Сумма ₽",
                value=cur_amt,
                key=form_key + "_amt_" + s(i),
            )
            dt_in = c2.text_input(
                "Дата (ДД.ММ.ГГГГ)",
                value=cur_date,
                key=form_key + "_dt_" + s(i),
            )
            cm_in = c3.text_input(
                "Комментарий",
                value=cur_comm,
                key=form_key + "_cm_" + s(i),
            )
            new_payments.append({
                "amount": amt_in,
                "date": dt_in,
                "comment": cm_in,
            })

        total_new = 0.0
        for p in new_payments:
            total_new += money_value(p["amount"])
        total_new = money_value(total_new)
        if nds_debt > 0.01 and total_new > nds_debt + 0.01:
            st.error(
                "⚠️ Сумма платежей (" + fmt_money(total_new)
                + " ₽) больше задолженности по безнал-НДС ("
                + fmt_money(nds_debt) + " ₽). Уменьшите суммы."
            )
        elif total_new > 0.01:
            st.info(
                "Итого по частям: **" + fmt_money(total_new) + " ₽**"
                + " из **" + fmt_money(nds_debt) + " ₽**"
            )

        col_save, col_clear, col_cancel = st.columns(3)
        save_btn = col_save.form_submit_button("💾 Сохранить платежи",
                                                use_container_width=True)
        clear_btn = col_clear.form_submit_button("🗑 Очистить все",
                                                  use_container_width=True)
        cancel_btn = col_cancel.form_submit_button("Отмена",
                                                    use_container_width=True)

    if cancel_btn:
        st.session_state.pop("open_split_" + s(trip_id), None)
        st.rerun()

    if clear_btn:
        clear_trip_payments_split(trip_id)
        log_action(user_login, role, "clear_trip_split",
                   "trip " + s(trip_id))
        st.success("Все части платежей очищены")
        st.rerun()

    if save_btn:
        parsed = []
        for i, p in enumerate(new_payments):
            amt_raw = s(p.get("amount", "")).strip()
            dt_raw = s(p.get("date", "")).strip()
            cm_raw = s(p.get("comment", "")).strip()
            amt_val = money_value(amt_raw) if amt_raw else 0.0
            if amt_val < 0:
                st.error("Сумма платежа №" + s(i + 1) + " не может быть отрицательной")
                st.stop()
            try:
                dt_fmt = parse_date_ui(dt_raw) if dt_raw else ""
            except ValueError as ex:
                st.error("Платёж №" + s(i + 1) + ": " + str(ex))
                st.stop()
            parsed.append({
                "amount": amt_val,
                "date": dt_fmt,
                "comment": cm_raw,
            })

        total_parsed = money_value(sum(x["amount"] for x in parsed))
        if nds_debt > 0.01 and total_parsed > nds_debt + 0.01:
            st.error("Сумма платежей превышает задолженность по безнал-НДС. "
                     "Уменьшите суммы.")
            st.stop()

        update_trip_payments_split(trip_id, parsed)
        log_action(user_login, role, "update_trip_split",
                   "trip " + s(trip_id)
                   + " | итого: " + fmt_money(total_parsed) + " ₽"
                   + " | частей: " + s(sum(1 for x in parsed if x["amount"] > 0.01)))
        st.session_state.pop("open_split_" + s(trip_id), None)
        st.success("Платежи сохранены")
        st.rerun()




# ============================================================
# v2.14.0 — ПОНЯТНАЯ КАРТОЧКА РЕЙСА
# Рейс → Автомобили → Оплата → Документы → Переносы
# ============================================================

TRIP_SECTION_LABELS = {
    "trip": "🚛 Рейс",
    "cars": "🚗 Автомобили",
    "payment": "💳 Оплата",
    "documents": "📄 Документы",
    "transfers": "📦 Переносы",
}


def _trip_normal_cars(cars):
    """Реальные автомобили без технических строк «Перенесён: ...»."""
    return [x for x in cars if not is_trace_row(x)]


def _shipment_ui_status(x):
    """Единый понятный статус автомобиля для карточек интерфейса."""
    fin = calculate_financials(x)
    debt = fin["debt"]
    issued = is_effectively_issued(x)
    paid = check_paid(x.get("paid", "0"))
    auto_issued = is_advance_covers(x)

    if debt <= 0.01 and issued and auto_issued and not check_issued(x.get("issued", "0")):
        return "🟢 Выдан (аванс покрыл сумму)", "#e8f5e9", "#2e7d32"
    if debt <= 0.01 and issued:
        return "🟢 Выдан", "#e8f5e9", "#2e7d32"
    if debt <= 0.01 and paid:
        return "🟢 Оплачен", "#e8f5e9", "#2e7d32"
    if debt <= 0.01:
        return "🟢 Без долга", "#e8f5e9", "#2e7d32"
    if issued:
        return "🔴 Выдан с долгом", "#ffebee", "#c62828"
    return "🔴 Долг", "#ffebee", "#c62828"


def _render_trip_info_box(ctx):
    """Компактная адаптивная сводка по рейсу без крупных st.metric."""
    trip_completed = ctx["trip_completed"]
    status = "✅ Завершён" if trip_completed else "🟢 Активный"
    if ctx["view_mode"] == "archive":
        status = "📦 Архив"

    def esc(value):
        return html.escape(s(value) if value not in (None, "") else "—")

    items_top = [
        ("Тягач", ctx["tractor"] or "—"),
        ("Водитель", ctx["driver"] or "—"),
        ("Дата выезда", ctx["dep"] or "—"),
        ("Статус", status),
    ]
    items_money = [
        ("Авто в рейсе", str(ctx["total_in_route"]) + "/" + str(MAX_CARS)),
        ("Сумма", fmt_money(ctx["total"]) + " ₽"),
        ("Оплачено", fmt_money(ctx["total_paid_value"]) + " ₽"),
        ("Долг", fmt_money(ctx["total_debt"]) + " ₽"),
    ]

    def cells(items, debt_last=False):
        parts = []
        for idx, (label, value) in enumerate(items):
            extra = " trip-mini-debt" if debt_last and idx == len(items) - 1 and ctx["total_debt"] > 0.01 else ""
            parts.append(
                '<div class="trip-mini-card' + extra + '">'
                '<div class="trip-mini-label">' + esc(label) + '</div>'
                '<div class="trip-mini-value">' + esc(value) + '</div>'
                '</div>'
            )
        return "".join(parts)

    st.markdown(
        '<div class="trip-compact">'
        '<div class="trip-mini-grid">' + cells(items_top) + '</div>'
        '<div class="trip-route-line">'
        '<span><b>Маршрут:</b> ' + esc(ctx["route"] or "—") + '</span>'
        '<span><b>Погрузка от:</b> ' + esc(ctx["loading_from"] or "—")
        + (' · <b>Договор №:</b> ' + esc(ctx["loading_contract_number"])
           if ctx.get("loading_contract_number") else '') + '</span>'
        '<span><b>Возвращение:</b> ' + esc(ctx["ret"] or "—") + '</span>'
        '</div>'
        '<div class="trip-mini-grid trip-money-grid">' + cells(items_money, debt_last=True) + '</div>'
        '</div>',
        unsafe_allow_html=True,
    )


def _render_trip_edit_form(ctx):
    trip_id = ctx["trip_id"]
    key = "open_edittrip_" + s(trip_id)
    if not st.session_state.get(key):
        return

    with st.form("edit_trip_" + s(trip_id)):
        st.markdown("### ✏ Редактировать рейс")
        c1, c2 = st.columns(2)
        e_tractor = c1.text_input("Гос номер тягача", value=ctx["tractor"])
        e_driver = c2.text_input("Водитель ФИО", value=ctx["driver"])
        c3, c4 = st.columns(2)
        e_route = c3.text_input("Маршрут", value=ctx["route"])
        e_dep = c4.text_input("Дата выезда (ДД.ММ.ГГГГ)", value=ctx["dep"])
        c5, c6 = st.columns(2)
        e_loading_from = c5.text_input("Погрузка от", value=ctx["loading_from"])
        e_loading_contract_number = c6.text_input(
            "Договор №", value=ctx.get("loading_contract_number", "")
        )
        e_ret = st.text_input("Дата возвращения (можно пусто)", value=ctx["ret"])
        ok = st.form_submit_button("💾 Сохранить изменения")
        cancel = st.form_submit_button("Отмена")

    if cancel:
        st.session_state.pop(key, None)
        st.rerun()
    if ok:
        try:
            dep_fmt = parse_date_ui(e_dep)
            ret_fmt = parse_date_ui(e_ret)
        except ValueError as ex:
            st.error(str(ex))
        else:
            update_trip(
                trip_id, e_tractor, e_driver, e_route, dep_fmt, ret_fmt,
                loading_from=e_loading_from,
                loading_contract_number=e_loading_contract_number,
                payment_date=ctx["payment_date"],
                payment_comment=ctx["payment_comment"],
            )
            log_action(ctx["user_login"], ctx["role"], "edit_trip",
                       s(e_tractor) + " " + s(e_driver) + " " + s(dep_fmt))
            st.session_state.pop(key, None)
            st.success("Рейс обновлён")
            st.rerun()


def _render_trip_complete_form(ctx):
    trip_id = ctx["trip_id"]
    key = "open_complete_" + s(trip_id)
    if not st.session_state.get(key):
        return

    with st.form("complete_" + s(trip_id)):
        st.markdown("### ✅ Завершение рейса")
        default_date = ctx["trip_completed_at"] or datetime.now().strftime("%d.%m.%Y")
        comp_date = st.text_input("Дата завершения (ДД.ММ.ГГГГ)", value=default_date)
        ok = st.form_submit_button("✅ Завершить рейс")
        cancel = st.form_submit_button("Отмена")

    if cancel:
        st.session_state.pop(key, None)
        st.rerun()
    if ok:
        try:
            comp_fmt = parse_date_ui(comp_date)
        except ValueError as ex:
            st.error(str(ex))
        else:
            complete_trip(trip_id, comp_fmt)
            log_action(ctx["user_login"], ctx["role"], "complete_trip",
                       ctx["tractor"] + " " + ctx["driver"] + " " + comp_fmt)
            st.session_state.pop(key, None)
            st.success("Рейс завершён")
            st.rerun()


def render_trip_overview_section(ctx):
    """Раздел 1. Только сведения о рейсе и управление самим рейсом."""
    st.markdown('<div class="trip-section-title">🚛 Рейс</div>', unsafe_allow_html=True)
    st.caption("Здесь только данные рейса. Автомобили, деньги и документы находятся в соседних разделах.")
    _render_trip_info_box(ctx)
    st.divider()

    trip_id = ctx["trip_id"]
    role = ctx["role"]
    view_mode = ctx["view_mode"]

    if view_mode == "active":
        cols = st.columns(3)
        if can("edit_trip", role):
            if cols[0].button("✏ Редактировать рейс", key="v214_edit_trip_" + s(trip_id), use_container_width=True):
                st.session_state["open_edittrip_" + s(trip_id)] = True
                st.rerun()
        if can("complete_trip", role):
            if cols[1].button("✅ Завершить рейс", key="v214_complete_trip_" + s(trip_id), use_container_width=True):
                st.session_state["open_complete_" + s(trip_id)] = True
                st.rerun()
        if can("archive", role):
            if cols[2].button("📦 В архив", key="v214_archive_trip_" + s(trip_id), use_container_width=True):
                archive_trip(trip_id)
                log_action(ctx["user_login"], role, "archive_trip", ctx["tractor"] + " " + ctx["driver"])
                st.success("Рейс отправлен в архив")
                st.rerun()

    elif view_mode == "completed":
        cols = st.columns(2)
        if can("complete_trip", role):
            if cols[0].button("↩ Вернуть в активные", key="v214_uncomplete_" + s(trip_id), use_container_width=True):
                uncomplete_trip(trip_id)
                log_action(ctx["user_login"], role, "uncomplete_trip", ctx["tractor"] + " " + ctx["driver"])
                st.rerun()
        if can("archive", role):
            if cols[1].button("📦 В архив", key="v214_archive_completed_" + s(trip_id), use_container_width=True):
                archive_trip(trip_id)
                log_action(ctx["user_login"], role, "archive_trip", ctx["tractor"] + " " + ctx["driver"])
                st.rerun()

    elif view_mode == "archive" and can("archive", role):
        if st.button("♻ Вернуть из архива", key="v214_unarchive_" + s(trip_id), use_container_width=True):
            unarchive_trip(trip_id)
            log_action(ctx["user_login"], role, "unarchive_trip", ctx["tractor"] + " " + ctx["driver"])
            st.rerun()

    _render_trip_edit_form(ctx)
    _render_trip_complete_form(ctx)

    if view_mode == "active" and role in ("admin", "director", "dispatcher"):
        with st.expander("⚠️ Опасная зона", expanded=False):
            st.caption("Обычная работа с рейсом выполняется выше. Здесь только удаление.")
            c1, c2 = st.columns(2)
            if c1.button("🗑 Пометить удалённым", key="v214_softdel_" + s(trip_id), use_container_width=True):
                soft_delete_trip(trip_id)
                log_action(ctx["user_login"], role, "soft_delete_trip", ctx["tractor"] + " " + ctx["driver"])
                st.success("Рейс помечен удалённым")
                st.rerun()

            if can("delete_trip", role):
                del_key = "v214_confirm_delete_trip_" + s(trip_id)
                if c2.button("❌ Удалить окончательно", key="v214_delete_trip_" + s(trip_id), use_container_width=True):
                    st.session_state[del_key] = True
                    st.rerun()
                if st.session_state.get(del_key):
                    st.error("Будет окончательно удалён рейс и все его автомобили. Восстановить нельзя.")
                    y, n = st.columns(2)
                    if y.button("Да, удалить навсегда", key="v214_delete_trip_yes_" + s(trip_id), use_container_width=True):
                        delete_trip(trip_id)
                        log_action(ctx["user_login"], role, "delete_trip", ctx["tractor"] + " " + ctx["driver"])
                        st.session_state.pop(del_key, None)
                        st.rerun()
                    if n.button("Отмена", key="v214_delete_trip_no_" + s(trip_id), use_container_width=True):
                        st.session_state.pop(del_key, None)
                        st.rerun()


def _render_add_car_form(ctx):
    trip_id = ctx["trip_id"]
    key = "open_addcar_" + s(trip_id)
    if not st.session_state.get(key):
        return

    st.markdown("### ➕ Новый автомобиль")
    f = render_shipment_form(
        "newcar_" + s(trip_id), c=None,
        submit_label="Сохранить авто",
        default_position=ctx["free_pos"] or 1,
    )
    if f["cancel"]:
        st.session_state.pop(key, None)
        st.rerun()
    if not f["save"]:
        return

    if ctx["total_in_route"] >= MAX_CARS:
        st.error("На автовозе уже " + s(MAX_CARS) + " активных авто.")
        return
    if not f["client"] and not f["customer"]:
        st.error("Заполните хотя бы одно: ФИО клиента или Заказчик")
        return

    try:
        payments = parse_car_payments_ui(f["payments"])
    except ValueError as ex:
        st.error(str(ex))
        return

    amount = money_value(f["amount"])
    total_paid = money_value(sum(p["amount"] for p in payments))
    if amount > 0.01 and total_paid > amount + 0.01:
        st.error("Сумма трёх платежей больше стоимости перевозки на "
                 + fmt_money(total_paid - amount) + " ₽")
        return
    if amount <= 0.01 and total_paid > 0.01:
        st.error("Сначала укажите стоимость перевозки.")
        return

    if f["payer_type"] == NDS_PAYER:
        nds = amount * NDS_RATE / (1 + NDS_RATE)
        no_nds = amount - nds
    else:
        nds = 0.0
        no_nds = amount

    p1, p2, p3 = payments
    create_shipment(
        trip_id, f["position"], f["car_model"], f["client"], amount,
        payments_last_date(payments), p1["paid_to"],
        f["delivery_city"], f["vin"][:17],
        p1["amount"], p1["date"], f["payer_type"],
        f["customer"], f["contract_number"], nds, no_nds, ctx["user_login"],
        advance_2=p2["amount"], advance_date_2=p2["date"],
        paid_to_2=p2["paid_to"], payments=payments,
    )
    log_action(ctx["user_login"], ctx["role"], "create_ship",
               "рейс " + s(trip_id) + ", поз " + s(f["position"])
               + ", оплачено " + fmt_money(total_paid) + " ₽")
    st.session_state.pop(key, None)
    st.success("Автомобиль добавлен")
    st.rerun()


def _render_edit_car_form(ctx, car):
    sid = s(car.get("id"))
    key = "edit_ship_" + sid
    if not st.session_state.get(key):
        return

    st.markdown("#### ✏ Редактировать автомобиль")
    f = render_shipment_form("editcar_" + sid, c=car, submit_label="Сохранить изменения")
    if f["cancel"]:
        st.session_state.pop(key, None)
        st.rerun()
    if not f["save"]:
        return
    if not f["client"] and not f["customer"]:
        st.error("Заполните хотя бы одно: ФИО клиента или Заказчик")
        return

    try:
        payments = parse_car_payments_ui(f["payments"])
    except ValueError as ex:
        st.error(str(ex))
        return

    amount = money_value(f["amount"])
    total_paid = money_value(sum(p["amount"] for p in payments))
    if amount > 0.01 and total_paid > amount + 0.01:
        st.error("Сумма трёх платежей больше стоимости перевозки на "
                 + fmt_money(total_paid - amount) + " ₽")
        return
    if amount <= 0.01 and total_paid > 0.01:
        st.error("Сначала укажите стоимость перевозки.")
        return

    if f["payer_type"] == NDS_PAYER:
        nds = amount * NDS_RATE / (1 + NDS_RATE)
        no_nds = amount - nds
    else:
        nds = 0.0
        no_nds = amount

    p1, p2, p3 = payments
    update_shipment(
        sid, f["position"], f["car_model"], f["client"], amount,
        payments_last_date(payments), p1["paid_to"],
        f["delivery_city"], f["vin"][:17],
        p1["amount"], p1["date"], f["payer_type"],
        f["customer"], f["contract_number"], nds, no_nds,
        advance_2=p2["amount"], advance_date_2=p2["date"],
        paid_to_2=p2["paid_to"], payments=payments,
    )
    log_action(ctx["user_login"], ctx["role"], "edit_ship",
               "рейс " + s(ctx["trip_id"]) + ", поз " + s(f["position"])
               + ", оплачено " + fmt_money(total_paid) + " ₽")
    st.session_state.pop(key, None)
    st.success("Изменения сохранены")
    st.rerun()


def _render_car_delete_confirmation(ctx, car):
    sid = s(car.get("id"))
    key = "v214_confirm_delete_car_" + sid
    if not st.session_state.get(key):
        return
    st.warning("Удалить автомобиль «" + s(car.get("car_model", "")) + "»? Действие необратимо.")
    y, n = st.columns(2)
    if y.button("Да, удалить", key="v214_del_car_yes_" + sid, use_container_width=True):
        delete_shipment(sid)
        log_action(ctx["user_login"], ctx["role"], "delete_ship",
                   "рейс " + s(ctx["trip_id"]) + ", поз " + s(car.get("position")))
        st.session_state.pop(key, None)
        st.rerun()
    if n.button("Отмена", key="v214_del_car_no_" + sid, use_container_width=True):
        st.session_state.pop(key, None)
        st.rerun()


def render_trip_cars_section(ctx):
    """Раздел 2. Список машин, выдача, редактирование и удаление."""
    st.markdown("### 🚗 Автомобили")
    st.caption("Здесь работа с самими автомобилями. Оплата, акты и переносы вынесены в отдельные разделы.")

    normal_cars = _trip_normal_cars(ctx["cars"])
    top1, top2, top3 = st.columns([2, 1, 1])
    top1.markdown("**В рейсе:** " + s(ctx["total_in_route"]) + " из " + s(MAX_CARS))
    top2.markdown("**Выдано:** " + s(ctx["issued_count"]))
    top3.markdown("**Свободно:** " + s(max(0, MAX_CARS - ctx["total_in_route"])))

    if (ctx["view_mode"] == "active" and can("create_ship", ctx["role"])
            and ctx["total_in_route"] < MAX_CARS):
        label = "➕ Добавить автомобиль"
        if ctx["free_pos"]:
            label += " · позиция " + s(ctx["free_pos"])
        if st.button(label, key="v214_add_car_" + s(ctx["trip_id"]), use_container_width=True):
            st.session_state["open_addcar_" + s(ctx["trip_id"])] = True
            st.rerun()

    _render_add_car_form(ctx)

    if not normal_cars:
        st.info("В этом рейсе пока нет автомобилей.")
        return

    for car in sorted(normal_cars, key=lambda z: int(to_float(z.get("position")))):
        fin = calculate_financials(car)
        status, bg, border = _shipment_ui_status(car)
        sid = s(car.get("id"))
        client = s(car.get("client", "")) or s(car.get("customer", "")) or "—"
        title = ("Поз. " + s(car.get("position", "")) + " · "
                 + s(car.get("car_model", "")) + " · " + status)

        with st.expander(title, expanded=False):
            st.markdown(
                '<div style="background:' + bg + '; border-left:5px solid ' + border
                + '; padding:10px 12px; border-radius:6px;">'
                '<b>Клиент:</b> ' + client + '<br>'
                '<b>VIN:</b> ' + (s(car.get("vin", ""))[:17] or '—') + '<br>'
                '<b>Город доставки:</b> ' + (s(car.get("delivery_city", "")) or '—') + '<br>'
                '<b>Сумма:</b> ' + fmt_money(fin["amount"]) + ' ₽ &nbsp;·&nbsp; '
                '<b>Долг:</b> ' + fmt_money(fin["debt"]) + ' ₽'
                '</div>', unsafe_allow_html=True)

            if is_transferred_car(car):
                st.info("Автомобиль перенесён из рейса: "
                        + resolve_source_trip_label_for_car(car, ctx["trips"], ctx["transfer_history"]))

            if ctx["view_mode"] in ("active", "completed"):
                cols = st.columns(3)
                issued = check_issued(car.get("issued", "0"))
                issued_label = "↩ Снять статус «Выдан»" if issued else "✅ Отметить «Выдан»"
                if cols[0].button(issued_label, key="v214_issue_" + sid, use_container_width=True):
                    toggle_issued(sid, s(car.get("issued", "0")), s(car.get("issued_ever", "0")))
                    log_action(ctx["user_login"], ctx["role"], "toggle_issued",
                               "рейс " + s(ctx["trip_id"]) + ", поз " + s(car.get("position")))
                    st.rerun()

                if can("edit_ship", ctx["role"]):
                    if cols[1].button("✏ Изменить", key="v214_edit_car_" + sid, use_container_width=True):
                        st.session_state["edit_ship_" + sid] = True
                        st.rerun()
                if can("delete_ship", ctx["role"]):
                    if cols[2].button("🗑 Удалить", key="v214_delete_car_" + sid, use_container_width=True):
                        st.session_state["v214_confirm_delete_car_" + sid] = True
                        st.rerun()
            elif ctx["view_mode"] == "archive" and can("edit_ship", ctx["role"]):
                if st.button("✏ Изменить", key="v214_edit_car_arch_" + sid):
                    st.session_state["edit_ship_" + sid] = True
                    st.rerun()

            _render_car_delete_confirmation(ctx, car)
            _render_edit_car_form(ctx, car)


def _render_trip_payment_meta_form(ctx):
    """Дата фактической оплаты и комментарий по рейсу."""
    trip_id = ctx["trip_id"]
    key = "v214_trip_payment_meta_" + s(trip_id)
    label = "✏ Дата оплаты / комментарий" if (ctx["payment_date"] or ctx["payment_comment"]) else "➕ Указать дату оплаты / комментарий"
    if st.button(label, key="v214_trip_payment_meta_btn_" + s(trip_id), use_container_width=True):
        st.session_state[key] = not st.session_state.get(key, False)
        st.rerun()
    if not st.session_state.get(key):
        return

    with st.form("v214_trip_payment_meta_form_" + s(trip_id)):
        c1, c2 = st.columns(2)
        date_val = date_to_display_safe(ctx["payment_date"]) if s(ctx["payment_date"]).strip() else ""
        new_date = c1.text_input("Дата оплаты (ДД.ММ.ГГГГ)", value=date_val)
        new_comment = c2.text_input("Комментарий / куда ушли деньги", value=ctx["payment_comment"])
        save = st.form_submit_button("💾 Сохранить")
        clear = st.form_submit_button("🗑 Очистить")
        cancel = st.form_submit_button("Отмена")
    if cancel:
        st.session_state.pop(key, None)
        st.rerun()
    if clear:
        update_trip_payment(trip_id, "", "")
        log_action(ctx["user_login"], ctx["role"], "clear_trip_payment", s(trip_id))
        st.session_state.pop(key, None)
        st.rerun()
    if save:
        try:
            date_fmt = parse_date_ui(new_date) if s(new_date).strip() else ""
        except ValueError as ex:
            st.error(str(ex))
        else:
            update_trip_payment(trip_id, date_fmt, new_comment)
            log_action(ctx["user_login"], ctx["role"], "edit_trip_payment", s(trip_id))
            st.session_state.pop(key, None)
            st.rerun()



def _payment_line_html(index, payment):
    amount = money_value(payment.get("amount", 0))
    date_val = s(payment.get("date", "")).strip()
    paid_to = s(payment.get("paid_to", "")).strip()
    comment = s(payment.get("comment", "")).strip()
    has_any = amount > 0.01 or date_val or paid_to or comment
    if not has_any:
        return ""

    meta = []
    if date_val:
        meta.append("Дата: " + html.escape(date_val))
    if paid_to:
        meta.append("Кому: " + html.escape(paid_to))
    if comment:
        meta.append("Комментарий: " + html.escape(comment))
    meta_html = " · ".join(meta) if meta else "Дополнительные данные не указаны"
    return (
        '<div style="padding:7px 9px; border:1px solid #e0e0e0; border-radius:8px; '
        'margin:5px 0; font-size:13px; line-height:1.35;">'
        '<b>Платёж № ' + str(index) + ': ' + fmt_money(amount) + ' ₽</b><br>'
        '<span style="color:#555;">' + meta_html + '</span></div>'
    )


def _render_car_payment_editor(ctx, car):
    sid = s(car.get("id"))
    allowed = can_pay_shipment(car, ctx["role"])
    key = "v216_pay_editor_" + sid

    if not allowed:
        st.info("Изменение платежей для безнала с НДС доступно только директору или администратору.")
        return

    if st.button("✏️ Внести / изменить платежи", key="v216_open_pay_" + sid,
                 use_container_width=True):
        st.session_state[key] = not st.session_state.get(key, False)
        st.rerun()

    if not st.session_state.get(key):
        return

    defaults = get_car_payments(car)
    with st.form("v216_pay_form_" + sid, clear_on_submit=False):
        st.markdown("**До трёх платежей по автомобилю**")
        edited = []
        for i in range(3):
            d = defaults[i]
            st.markdown("**Платёж № " + str(i + 1) + "**")
            c1, c2 = st.columns(2)
            amount_default = "" if d["amount"] <= 0.01 else "{:.2f}".format(d["amount"]).replace(".", ",")
            p_amount = c1.text_input(
                "Сумма", value=amount_default,
                key="v216_pf_amt_" + sid + "_" + str(i))
            p_date = c2.text_input(
                "Дата (ДД.ММ.ГГГГ)", value=s(d["date"]),
                key="v216_pf_date_" + sid + "_" + str(i))
            c3, c4 = st.columns(2)
            p_to = c3.text_input(
                "Кому оплатили", value=s(d["paid_to"]),
                key="v216_pf_to_" + sid + "_" + str(i))
            p_comment = c4.text_input(
                "Комментарий", value=s(d["comment"]),
                key="v216_pf_comment_" + sid + "_" + str(i))
            edited.append({
                "amount": p_amount, "date": p_date,
                "paid_to": p_to, "comment": p_comment,
            })

        total_now = sum(money_value(x["amount"]) for x in edited)
        amount_total = money_value(car.get("amount"))
        remain_now = max(0.0, amount_total - total_now)
        st.caption("Введено: " + fmt_money(total_now) + " ₽ · остаток: "
                   + fmt_money(remain_now) + " ₽")

        b1, b2 = st.columns(2)
        save = b1.form_submit_button("💾 Сохранить платежи", use_container_width=True)
        cancel = b2.form_submit_button("Отмена", use_container_width=True)

    if cancel:
        st.session_state.pop(key, None)
        st.rerun()
    if not save:
        return

    try:
        parsed = parse_car_payments_ui(edited)
    except ValueError as ex:
        st.error(str(ex))
        return

    total = money_value(sum(x["amount"] for x in parsed))
    amount_total = money_value(car.get("amount"))
    if amount_total > 0.01 and total > amount_total + 0.01:
        st.error("Сумма платежей больше стоимости перевозки на "
                 + fmt_money(total - amount_total) + " ₽")
        return

    ok, err = save_car_payments(
        sid, parsed, role=ctx["role"], user_login=ctx["user_login"])
    if not ok:
        st.error(err)
        return

    st.session_state.pop(key, None)
    st.success("Платежи сохранены. Долг пересчитан автоматически.")
    st.rerun()


def render_trip_payment_section(ctx):
    """Раздел 3. Детальная история оплаты по каждому автомобилю."""
    st.markdown("### 💳 Оплата")
    st.caption(
        "По каждому автомобилю можно сохранить до трёх платежей: сумма, дата, "
        "кому оплатили и комментарий. Остаток долга рассчитывается автоматически."
    )

    # Компактный итог по рейсу без крупных metric-карточек.
    st.markdown(
        '<div style="display:flex; gap:8px; flex-wrap:wrap; margin:4px 0 10px 0;">'
        '<div style="padding:6px 9px; border:1px solid #ddd; border-radius:8px; font-size:13px;">'
        'Сумма: <b>' + fmt_money(ctx["total"]) + ' ₽</b></div>'
        '<div style="padding:6px 9px; border:1px solid #ddd; border-radius:8px; font-size:13px;">'
        'Оплачено: <b>' + fmt_money(ctx["total_paid_value"]) + ' ₽</b></div>'
        '<div style="padding:6px 9px; border:1px solid #ddd; border-radius:8px; font-size:13px;">'
        'Долг: <b>' + fmt_money(ctx["total_debt"]) + ' ₽</b></div>'
        + (('<div style="padding:6px 9px; border:1px solid #ddd; border-radius:8px; font-size:13px;">'
            'НДС 22%: <b>' + fmt_money(ctx["total_nds"]) + ' ₽</b></div>')
           if ctx["total_nds"] > 0.01 else '')
        + '</div>',
        unsafe_allow_html=True,
    )

    if ctx["has_nds"] and ctx["view_mode"] in ("active", "completed"):
        with st.expander("Безнал с НДС — оплата всего рейса", expanded=False):
            render_trip_payment_button(
                ctx["trip_id"], ctx["cars"], ctx["role"], ctx["user_login"],
                trip_label=ctx["tractor"] + " " + ctx["driver"], container=st,
            )

            if can("split_payment", ctx["role"]):
                info = ctx["payments_info"] or {}
                parts_count = info.get("parts_count", 0)
                split_label = "🧾 Разделить счёт"
                if parts_count:
                    split_label += " · частей: " + s(parts_count)
                if st.button(split_label, key="v216_split_btn_" + s(ctx["trip_id"]),
                             use_container_width=True):
                    k = "open_split_" + s(ctx["trip_id"])
                    st.session_state[k] = not st.session_state.get(k, False)
                    st.rerun()
                if st.session_state.get("open_split_" + s(ctx["trip_id"])):
                    render_trip_split_form(
                        ctx["trip_id"], ctx["trip"], info.get("nds_debt", 0.0),
                        info.get("nds_total", 0.0), ctx["user_login"], ctx["role"],
                    )

            if can("edit_payment", ctx["role"]):
                _render_trip_payment_meta_form(ctx)

    st.divider()
    st.markdown("#### Платежи по автомобилям")
    cars = _trip_normal_cars(ctx["cars"])
    if not cars:
        st.info("Нет автомобилей для оплаты.")
        return

    for car in sorted(cars, key=lambda z: int(to_float(z.get("position")))):
        fin = calculate_financials(car)
        payments = get_car_payments(car)
        recorded = money_value(sum(p["amount"] for p in payments))
        title_status = "✅ оплачено" if fin["debt"] <= 0.01 else "🔴 долг " + fmt_money(fin["debt"]) + " ₽"
        with st.expander(
            "Поз. " + s(car.get("position", "")) + " · " + s(car.get("car_model", ""))
            + " · " + title_status,
            expanded=False,
        ):
            st.markdown(
                '<div style="font-size:13px; line-height:1.45; margin-bottom:6px;">'
                '<b>Стоимость:</b> ' + fmt_money(fin["amount"]) + ' ₽'
                ' &nbsp;·&nbsp; <b>Оплачено:</b> ' + fmt_money(fin["paid_value"]) + ' ₽'
                ' &nbsp;·&nbsp; <b>Остаток:</b> ' + fmt_money(fin["debt"]) + ' ₽'
                '<br><b>Тип оплаты:</b> ' + html.escape(s(car.get("payer_type", "")) or "—")
                + '</div>',
                unsafe_allow_html=True,
            )

            shown = 0
            for idx, payment in enumerate(payments, start=1):
                line = _payment_line_html(idx, payment)
                if line:
                    st.markdown(line, unsafe_allow_html=True)
                    shown += 1
            if shown == 0:
                st.caption("Платежей пока нет.")

            # Старый флаг полной оплаты/пакетная оплата НДС может существовать без
            # детальной расшифровки последнего платежа — показываем это явно.
            if check_paid(car.get("paid", "0")) and recorded < fin["amount"] - 0.01:
                st.warning(
                    "Автомобиль отмечен полностью оплаченным, но в детализации платежей "
                    "указано только " + fmt_money(recorded) + " ₽. "
                    "Откройте редактирование платежей и заполните недостающий платёж."
                )

            if ctx["view_mode"] in ("active", "completed"):
                _render_car_payment_editor(ctx, car)


def _build_trip_print_rows(ctx):
    rows = []
    for car in sorted(ctx["cars"], key=lambda z: int(to_float(z.get("position")))):
        if is_effectively_issued(car):
            continue
        fin = calculate_financials(car)
        tr_to = s(car.get("transferred_to_trip", ""))
        tr_from = s(car.get("transferred_from_trip", ""))
        note = ""
        if is_trace_row(car):
            target_id = find_target_trip_for_parent(tr_to, ctx["shipments"])
            note = "Перенесён в: " + resolve_target_trip_label(
                target_id, ctx["trips"], ctx["transfer_history"])
        elif tr_from or tr_to:
            note = "Перенесён из: " + resolve_source_trip_label_for_car(
                car, ctx["trips"], ctx["transfer_history"])
        rows.append({
            "tractor": ctx["tractor"], "driver": ctx["driver"],
            "loading_from": ctx["loading_from"],
            "loading_contract_number": ctx.get("loading_contract_number", ""),
            "route": ctx["route"],
            "dep": ctx["dep"], "position": s(car.get("position", "")),
            "car_model": s(car.get("car_model", "")), "vin": s(car.get("vin", "")),
            "client": s(car.get("client", "")), "customer": s(car.get("customer", "")),
            "delivery_city": s(car.get("delivery_city", "")), "debt": fin["debt"], "note": note,
        })
    return rows


def _render_invoice_form(ctx):
    trip_id = ctx["trip_id"]
    key = "v214_invoice_form_" + s(trip_id)
    label = "✏ Изменить счёт" if (ctx["trip_inv_num"] or ctx["trip_inv_date"]) else "📄 Выставить счёт"
    if st.button(label, key="v214_invoice_btn_" + s(trip_id), use_container_width=True):
        st.session_state[key] = not st.session_state.get(key, False)
        st.rerun()
    if not st.session_state.get(key):
        return

    with st.form("v214_invoice_form_inner_" + s(trip_id)):
        c1, c2 = st.columns(2)
        num = c1.text_input("№ счёта", value=ctx["trip_inv_num"])
        date = c2.text_input("Дата счёта (ДД.ММ.ГГГГ)", value=ctx["trip_inv_date"])
        save = st.form_submit_button("💾 Сохранить")
        clear = st.form_submit_button("🗑 Очистить счёт")
        cancel = st.form_submit_button("Отмена")
    if cancel:
        st.session_state.pop(key, None)
        st.rerun()
    if clear:
        update_trip_invoice(trip_id, "", "")
        log_action(ctx["user_login"], ctx["role"], "clear_trip_invoice", s(trip_id))
        st.session_state.pop(key, None)
        st.rerun()
    if save:
        try:
            date_fmt = parse_date_ui(date) if s(date).strip() else ""
        except ValueError as ex:
            st.error(str(ex))
        else:
            update_trip_invoice(trip_id, num, date_fmt)
            log_action(ctx["user_login"], ctx["role"], "edit_trip_invoice", s(trip_id) + " №" + s(num))
            st.session_state.pop(key, None)
            st.rerun()


def render_trip_documents_section(ctx):
    """Раздел 4. Счёт, печать списка и акты."""
    st.markdown("### 📄 Документы")
    st.caption("Все документы рейса и автомобилей находятся здесь.")

    if ctx["has_nds"]:
        st.markdown("#### Счёт по рейсу")
        if ctx["trip_inv_num"] or ctx["trip_inv_date"]:
            st.success("Счёт: № " + (ctx["trip_inv_num"] or "—") + " от " + (ctx["trip_inv_date"] or "—"))
        else:
            st.info("Счёт по рейсу пока не указан.")
        if can("edit_invoice", ctx["role"]):
            _render_invoice_form(ctx)
        st.divider()

    if ctx["cars"]:
        if st.button("🖨 Печать списка по рейсу", key="v214_print_trip_" + s(ctx["trip_id"]), use_container_width=True):
            st.session_state["show_print_trip"] = _build_trip_print_rows(ctx)
            st.rerun()

    st.markdown("#### Акты приёма-передачи")
    cars = _trip_normal_cars(ctx["cars"])
    if not cars:
        st.info("Нет автомобилей для формирования актов.")
        return
    for car in sorted(cars, key=lambda z: int(to_float(z.get("position")))):
        c1, c2 = st.columns([4, 1])
        c1.markdown("**Поз. " + s(car.get("position", "")) + " · " + s(car.get("car_model", "")) + "**  \nVIN: " + (s(car.get("vin", ""))[:17] or "—"))
        if c2.button("📄 Открыть акт", key="v214_act_" + s(car.get("id")), use_container_width=True):
            st.session_state["show_act_for"] = car["id"]
            st.rerun()
        st.divider()


def _active_trip_choices(ctx, exclude_ids=None):
    exclude = {s(x) for x in (exclude_ids or [])}
    result = []
    for t in ctx["trips"]:
        tid = s(t.get("id"))
        if tid in exclude:
            continue
        if is_archived(t.get("archived", "0")) or is_deleted(t.get("deleted_at", "")):
            continue
        if check_completed(t.get("completed", "0")):
            continue
        result.append((describe_trip(t), t.get("id")))
    return result


def _render_transfer_new_form(ctx, car):
    sid = s(car.get("id"))
    key = "v214_open_transfer_" + sid
    if not st.session_state.get(key):
        return
    options = _active_trip_choices(ctx, exclude_ids=[ctx["trip_id"]])
    if not options:
        st.warning("Нет других активных рейсов для переноса.")
        return
    with st.form("v214_transfer_form_" + sid):
        labels = [o[0] for o in options]
        chosen = st.selectbox("Новый рейс", labels)
        chosen_id = next(o[1] for o in options if o[0] == chosen)
        target_cars = [c for c in ctx["shipments"] if s(c.get("trip_id")) == s(chosen_id)]
        pos = st.number_input("Позиция", min_value=1, max_value=MAX_CARS,
                              value=next_free_position(target_cars) or 1, step=1)
        ok = st.form_submit_button("📦 Перенести")
        cancel = st.form_submit_button("Отмена")
    if cancel:
        st.session_state.pop(key, None)
        st.rerun()
    if ok:
        transfer_shipment_to_trip(sid, chosen_id, pos, ctx["user_login"])
        log_action(ctx["user_login"], ctx["role"], "transfer_ship",
                   "shipment " + sid + " → trip " + s(chosen_id))
        st.session_state.pop(key, None)
        st.success("Автомобиль перенесён")
        st.rerun()


def _render_transfer_reassign_form(ctx, car_id, current_target_id, key_suffix):
    key = "v214_open_reassign_" + key_suffix
    if not st.session_state.get(key):
        return
    options = _active_trip_choices(ctx, exclude_ids=[current_target_id])
    if not options:
        st.warning("Нет другого активного рейса для переназначения.")
        return
    with st.form("v214_reassign_form_" + key_suffix):
        labels = [o[0] for o in options]
        chosen = st.selectbox("Новый рейс", labels)
        chosen_id = next(o[1] for o in options if o[0] == chosen)
        target_cars = [c for c in ctx["shipments"] if s(c.get("trip_id")) == s(chosen_id)]
        pos = st.number_input("Позиция", min_value=1, max_value=MAX_CARS,
                              value=next_free_position(target_cars) or 1, step=1)
        ok = st.form_submit_button("💾 Переназначить")
        cancel = st.form_submit_button("Отмена")
    if cancel:
        st.session_state.pop(key, None)
        st.rerun()
    if ok:
        done, err = reassign_transfer_for_car(car_id, chosen_id, pos, ctx["user_login"], ctx["role"])
        st.session_state.pop(key, None)
        if done:
            log_action(ctx["user_login"], ctx["role"], "reassign_transfer",
                       "shipment " + s(car_id) + " → trip " + s(chosen_id))
            st.success("Перенос переназначен")
        else:
            st.error("Не удалось: " + s(err))
        st.rerun()


def _render_cancel_transfer_confirmation(ctx, car_id, source_label, key_suffix):
    key = "v214_cancel_transfer_" + key_suffix
    if not st.session_state.get(key):
        return
    st.warning("Вернуть автомобиль обратно в рейс: **" + (source_label or "исходный рейс") + "**?")
    y, n = st.columns(2)
    if y.button("↩ Да, вернуть", key="v214_cancel_yes_" + key_suffix, use_container_width=True):
        ok, err = cancel_transfer(car_id, ctx["user_login"], ctx["role"])
        st.session_state.pop(key, None)
        if ok:
            log_action(ctx["user_login"], ctx["role"], "cancel_transfer", "shipment " + s(car_id))
            st.success("Перенос отменён")
        else:
            st.error("Не удалось: " + s(err))
        st.rerun()
    if n.button("Отмена", key="v214_cancel_no_" + key_suffix, use_container_width=True):
        st.session_state.pop(key, None)
        st.rerun()


def render_trip_transfers_section(ctx):
    """Раздел 5. Переносы и история — отдельно от обычной карточки авто."""
    st.markdown("### 📦 Переносы")
    st.caption("Перенос на другой рейс, отмена переноса, переназначение и история собраны здесь.")

    cars = ctx["cars"]
    if not cars:
        st.info("В рейсе пока нет автомобилей.")
        return

    for car in sorted(cars, key=lambda z: int(to_float(z.get("position")))):
        sid = s(car.get("id"))
        is_trace = is_trace_row(car)

        if is_trace:
            parent_id = (s(car.get("transferred_to_trip", ""))
                         or s(car.get("transferred_from_trip", ""))
                         or find_parent_for_trace(car, ctx["shipments"]))
            target_id = find_target_trip_for_parent(parent_id, ctx["shipments"]) if parent_id else ""
            target_label = resolve_target_trip_label(target_id, ctx["trips"], ctx["transfer_history"]) if target_id else "—"
            with st.expander("↗ " + s(car.get("car_model", "")) + " · перенесён в " + target_label, expanded=False):
                st.markdown("**Дата переноса:** " + (s(car.get("transferred_at", "")) or "—"))
                if ctx["view_mode"] == "active" and parent_id:
                    c1, c2 = st.columns(2)
                    if can("cancel_transfer", ctx["role"]):
                        if c1.button("↩ Вернуть автомобиль", key="v214_trace_cancel_" + sid, use_container_width=True):
                            st.session_state["v214_cancel_transfer_trace_" + sid] = True
                            st.rerun()
                    if can("edit_trace", ctx["role"]):
                        if c2.button("✏ Переназначить", key="v214_trace_reassign_" + sid, use_container_width=True):
                            st.session_state["v214_open_reassign_trace_" + sid] = True
                            st.rerun()
                    _render_cancel_transfer_confirmation(ctx, parent_id, ctx["tractor"], "trace_" + sid)
                    _render_transfer_reassign_form(ctx, parent_id, target_id, "trace_" + sid)
            continue

        tr_from = s(car.get("transferred_from_trip", "")).strip()
        tr_to = s(car.get("transferred_to_trip", "")).strip()
        origin_label = resolve_source_trip_label_for_car(car, ctx["trips"], ctx["transfer_history"]) if (tr_from or tr_to) else ""

        title = "Поз. " + s(car.get("position", "")) + " · " + s(car.get("car_model", ""))
        if origin_label:
            title += " · ↩ из " + origin_label
        with st.expander(title, expanded=False):
            if origin_label:
                st.info("Перенесён из рейса: " + origin_label)
            else:
                st.caption("Автомобиль пока не переносился между рейсами.")

            if ctx["view_mode"] == "active":
                cols = st.columns(3)
                if can("transfer_ship", ctx["role"]):
                    if cols[0].button("📦 Перенести", key="v214_transfer_btn_" + sid, use_container_width=True):
                        st.session_state["v214_open_transfer_" + sid] = True
                        st.rerun()
                if tr_from and can("cancel_transfer", ctx["role"]):
                    if cols[1].button("↩ Отменить перенос", key="v214_cancel_btn_" + sid, use_container_width=True):
                        st.session_state["v214_cancel_transfer_" + sid] = True
                        st.rerun()
                if tr_to and can("edit_trace", ctx["role"]):
                    if cols[2].button("✏ Переназначить", key="v214_reassign_btn_" + sid, use_container_width=True):
                        st.session_state["v214_open_reassign_" + sid] = True
                        st.rerun()

            if st.button("📜 История перемещений", key="v214_history_" + sid, use_container_width=True):
                st.session_state["show_history_for"] = car["id"]
                st.rerun()

            _render_transfer_new_form(ctx, car)
            if tr_from:
                source_label = resolve_target_trip_label(tr_from, ctx["trips"], ctx["transfer_history"])
                _render_cancel_transfer_confirmation(ctx, sid, source_label, sid)
            if tr_to:
                _render_transfer_reassign_form(ctx, sid, tr_to, sid)


def render_trip_workspace(ctx):
    """Главная навигация внутри раскрытой карточки рейса."""
    options = ["trip", "cars", "payment", "documents", "transfers"]
    section = st.radio(
        "Раздел карточки рейса",
        options=options,
        format_func=lambda x: TRIP_SECTION_LABELS[x],
        horizontal=True,
        key="trip_section_" + s(ctx["trip_id"]),
        label_visibility="collapsed",
    )
    st.divider()

    if section == "trip":
        render_trip_overview_section(ctx)
    elif section == "cars":
        render_trip_cars_section(ctx)
    elif section == "payment":
        render_trip_payment_section(ctx)
    elif section == "documents":
        render_trip_documents_section(ctx)
    else:
        render_trip_transfers_section(ctx)


# ============================================================
# v2.15.0 — АДАПТИВНЫЙ ИНТЕРФЕЙС
# ============================================================
# БИЗНЕС-АНАЛИТИКА ПО МЕСЯЦАМ И ЗА ГОД — v2.17.2
# ============================================================
# ВАЖНО:
# - KPI «оплаты» строятся только по явно сохранённым суммам платежей;
# - флаг paid=1 сам по себе НЕ считается денежным поступлением;
# - платеж без даты НЕ относится к году/месяцу и НЕ входит в KPI;
# - технические значения старых версий не должны дублировать оплату.

ANALYTICS_MONTHS = [
    (1, "Январь", "Янв"),
    (2, "Февраль", "Фев"),
    (3, "Март", "Мар"),
    (4, "Апрель", "Апр"),
    (5, "Май", "Май"),
    (6, "Июнь", "Июн"),
    (7, "Июль", "Июл"),
    (8, "Август", "Авг"),
    (9, "Сентябрь", "Сен"),
    (10, "Октябрь", "Окт"),
    (11, "Ноябрь", "Ноя"),
    (12, "Декабрь", "Дек"),
]


def _parse_analytics_date(value):
    """Безопасно превращает дату приложения в datetime или возвращает None."""
    t = s(value).strip()
    if not t:
        return None
    for fmt in (
        "%d.%m.%Y", "%Y-%m-%d",
        "%Y-%m-%d %H:%M:%S", "%d.%m.%Y %H:%M:%S",
    ):
        try:
            return datetime.strptime(t, fmt)
        except ValueError:
            continue
    try:
        return datetime.fromisoformat(t.replace("Z", "+00:00"))
    except Exception:
        return None


def _analytics_trip_year(trip):
    dt = _parse_analytics_date((trip or {}).get("date_departure", ""))
    return dt.year if dt else None


def _analytics_archive_fallback_date(row, trip):
    """Дата для старой архивной оплаты, когда детальная история ещё не велась.

    Приоритет максимально близок к фактической оплате:
    1) дата оплаты автомобиля;
    2) дата оплаты рейса;
    3) дата возврата;
    4) дата завершения;
    5) дата выезда — только последний резерв для исторического архива.

    Функция используется ТОЛЬКО для архивных рейсов и только когда новой
    детальной даты платежа нет.
    """
    row = row or {}
    trip = trip or {}
    candidates = [
        row.get("date_pay", ""),
        trip.get("payment_date", ""),
        trip.get("date_return", ""),
        trip.get("completed_at", ""),
        trip.get("date_departure", ""),
    ]
    for value in candidates:
        dt = _parse_analytics_date(value)
        if dt is not None:
            return dt
    return None


def _analytics_car_payments(row):
    """Платежи автомобиля для управленческой аналитики.

    Новая схема car_pay1..3 имеет приоритет. Старые технические резервные поля
    не считаются отдельной оплатой. Любые старые дубли/переплаты автоматически
    обрезаются до стоимости конкретной перевозки и нигде отдельно не показываются.
    """
    amount_limit = max(0.0, money_value((row or {}).get("amount", 0)))

    if has_explicit_car_payments(row):
        raw = get_car_payments(row)
    else:
        # Старые данные до v2.16.0.
        adv1 = money_value(row.get("advance", 0))
        adv2 = money_value(row.get("advance_2", 0))
        paid_amount = money_value(row.get("paid_amount", 0))

        # v2.12.8 могла хранить в paid_amount резервную копию старого аванса,
        # одновременно заменив advance полной суммой. Это не третий платёж.
        old_adv1, old_adv2, legacy = _legacy_original_advances(row)
        if legacy:
            raw = [
                {
                    "amount": old_adv1,
                    "date": date_to_display_safe(row.get("advance_date", "")),
                    "paid_to": s(row.get("paid_to", "")).strip(),
                    "comment": "",
                },
                {
                    "amount": old_adv2,
                    "date": date_to_display_safe(row.get("advance_date_2", "")),
                    "paid_to": s(row.get("paid_to_2", "")).strip(),
                    "comment": "",
                },
                {"amount": 0.0, "date": "", "paid_to": "", "comment": ""},
            ]
        else:
            raw = [
                {
                    "amount": adv1,
                    "date": date_to_display_safe(row.get("advance_date", "")),
                    "paid_to": s(row.get("paid_to", "")).strip(),
                    "comment": "",
                },
                {
                    "amount": adv2,
                    "date": date_to_display_safe(row.get("advance_date_2", "")),
                    "paid_to": s(row.get("paid_to_2", "")).strip(),
                    "comment": "",
                },
                {
                    "amount": paid_amount,
                    "date": date_to_display_safe(row.get("date_pay", "")),
                    "paid_to": "",
                    "comment": "",
                },
            ]

    # Защита от дублей/ошибок старых строк: не больше стоимости перевозки.
    remaining = amount_limit
    result = []
    for p in normalize_car_payments(raw):
        original = max(0.0, money_value(p.get("amount", 0)))
        if amount_limit <= 0.01:
            accepted = 0.0
        else:
            accepted = min(original, max(0.0, remaining))
        remaining = max(0.0, remaining - accepted)
        result.append({
            "amount": money_value(accepted),
            "date": date_to_display_safe(p.get("date", "")),
            "paid_to": s(p.get("paid_to", "")).strip(),
            "comment": s(p.get("comment", "")).strip(),
        })

    return result


def _analytics_available_years(trips, shipments):
    years = {datetime.now().year}
    trip_by_id = {
        s(t.get("id")): t for t in trips
        if not is_deleted(t.get("deleted_at", ""))
    }

    for t in trip_by_id.values():
        dt = _parse_analytics_date(t.get("date_departure", ""))
        if dt:
            years.add(dt.year)
        for p in extract_trip_payments(t):
            pdt = _parse_analytics_date(p.get("date", ""))
            if pdt and to_float(p.get("amount")) > 0.01:
                years.add(pdt.year)

    for x in shipments:
        if is_trace_row(x):
            continue
        parts = _analytics_car_payments(x)
        for p in parts:
            pdt = _parse_analytics_date(p.get("date", ""))
            if pdt and to_float(p.get("amount")) > 0.01:
                years.add(pdt.year)

    return sorted(years, reverse=True)


def _analytics_transport_identity(row):
    """Ключ автомобиля для защиты аналитики от дублей внутри одного рейса.

    Приоритет:
    1) VIN — самый надёжный идентификатор;
    2) если VIN нет, используем комбинацию позиции, модели, клиента и суммы;
    3) в крайнем случае — ID строки.

    Ключ применяется ТОЛЬКО внутри одного рейса, поэтому повторная перевозка
    того же VIN в другом отдельном рейсе не будет ошибочно потеряна.
    """
    vin = re.sub(r"[^A-Z0-9]", "", s(row.get("vin", "")).upper())
    if vin:
        return ("vin", vin)

    pos = s(row.get("position", "")).strip()
    model = " ".join(s(row.get("car_model", "")).lower().split())
    client = " ".join(
        (s(row.get("client", "")) or s(row.get("customer", ""))).lower().split()
    )
    amount = money_value(row.get("amount", 0))

    if pos or model or client:
        return ("fallback", pos, model, client, amount)

    sid = s(row.get("id", "")).strip()
    if sid:
        return ("id", sid)

    return ("row", id(row))


def _analytics_choose_duplicate_row(old_row, new_row):
    """Выбирает наиболее информативную строку, если один автомобиль задублирован."""
    def score(row):
        explicit = 1 if has_explicit_car_payments(row) else 0
        pay_total = 0.0
        try:
            parts = _analytics_car_payments(row)
            pay_total = sum(to_float(p.get("amount")) for p in parts)
        except Exception:
            pass
        has_client = 1 if (s(row.get("client", "")).strip()
                           or s(row.get("customer", "")).strip()) else 0
        has_vin = 1 if s(row.get("vin", "")).strip() else 0
        return (explicit, pay_total, has_vin, has_client,
                max(0.0, money_value(row.get("amount", 0))))

    return new_row if score(new_row) > score(old_row) else old_row


def _analytics_unique_shipments_by_trip(shipments):
    """Возвращает реальные уникальные автомобили по каждому рейсу.

    Технические строки переноса исключаются. Дубликаты внутри одного рейса
    не суммируются второй раз.
    """
    grouped = {}
    duplicate_rows = 0
    duplicate_amount = 0.0

    for x in shipments:
        if is_trace_row(x):
            continue

        tid = s(x.get("trip_id", "")).strip()
        if not tid:
            continue

        key = _analytics_transport_identity(x)
        bucket = grouped.setdefault(tid, {})
        if key not in bucket:
            bucket[key] = x
            continue

        duplicate_rows += 1
        old = bucket[key]
        old_amount = max(0.0, money_value(old.get("amount", 0)))
        new_amount = max(0.0, money_value(x.get("amount", 0)))

        chosen = _analytics_choose_duplicate_row(old, x)
        bucket[key] = chosen

        # В контроль выводим сумму той строки, которая не была использована.
        if chosen is old:
            duplicate_amount += new_amount
        else:
            duplicate_amount += old_amount

    result = {
        tid: list(bucket.values())
        for tid, bucket in grouped.items()
    }
    return result, int(duplicate_rows), money_value(duplicate_amount)


def build_business_analytics(trips, shipments, year):
    """Собирает управленческую аналитику за один год.

    Правила v2.17.6:
    - рейсы считаются по уникальному ID и дате выезда;
    - сумма перевозок строится СНАЧАЛА по каждому рейсу;
    - внутри рейса один автомобиль учитывается только один раз;
    - технические строки переноса не участвуют;
    - подтверждённые оплаты считаются отдельно по фактическим датам;
    - активные, завершённые и архивные рейсы участвуют в графиках;
    - для СТАРЫХ архивных строк paid=1 восстанавливает только недостающую
      часть оплаты, чтобы архив не выпадал из аналитики и не было дублей.
    """
    year = int(year)
    months = {
        num: {
            "month_num": num,
            "month": name,
            "short": short,
            "trips": 0,
            "transport_amount": 0.0,
            "cash_acq": 0.0,
            "beznal_nds": 0.0,
        }
        for num, name, short in ANALYTICS_MONTHS
    }

    # Канонический список рейсов: одна строка на один ID.
    valid_trips = [t for t in trips if not is_deleted(t.get("deleted_at", ""))]
    trip_by_id = {}
    for t in valid_trips:
        tid = s(t.get("id", "")).strip()
        if not tid:
            continue
        if tid not in trip_by_id:
            trip_by_id[tid] = t

    trip_year = {tid: _analytics_trip_year(t) for tid, t in trip_by_id.items()}

    # Уникальные автомобили по каждому рейсу. Это единый источник для
    # графика суммы перевозок и для проверки сумм безнал-НДС.
    unique_by_trip, _duplicate_rows, _duplicate_amount = (
        _analytics_unique_shipments_by_trip(shipments)
    )

    # Количество рейсов по месяцам.
    for tid, t in trip_by_id.items():
        dt = _parse_analytics_date(t.get("date_departure", ""))
        if dt and dt.year == year:
            months[dt.month]["trips"] += 1

    # ------------------------------------------------------------
    # СУММА ПЕРЕВОЗОК: сначала итог каждого рейса, потом месяц.
    # ------------------------------------------------------------
    transport_trip_rows = []

    for tid, trip in trip_by_id.items():
        dep_dt = _parse_analytics_date(trip.get("date_departure", ""))
        if not dep_dt or dep_dt.year != year:
            continue

        cars = unique_by_trip.get(tid, [])
        trip_amount = 0.0
        counted_cars = 0

        for x in cars:
            amount = max(0.0, money_value(x.get("amount", 0)))
            if amount <= 0.01:
                continue
            trip_amount += amount
            counted_cars += 1

        trip_amount = money_value(trip_amount)
        months[dep_dt.month]["transport_amount"] += trip_amount

        transport_trip_rows.append({
            "month_num": dep_dt.month,
            "month": ANALYTICS_MONTHS[dep_dt.month - 1][1],
            "date": dep_dt.strftime("%d.%m.%Y"),
            "trip_id": tid,
            "tractor": s(trip.get("tractor_number", "")).strip(),
            "driver": s(trip.get("driver", "")).strip(),
            "route": s(trip.get("route", "")).strip(),
            "cars": counted_cars,
            "amount": trip_amount,
        })

    # Платежи без даты не включаем в месячные KPI.
    undated_cash = 0.0
    undated_nds = 0.0

    # Стоимость безнал-НДС по каждому рейсу — по тем же уникальным авто,
    # чтобы дубли не увеличивали допустимый лимит оплаты.
    nds_contract_by_trip = {}
    for tid, cars in unique_by_trip.items():
        total = 0.0
        for x in cars:
            if not _is_beznal_nds(x):
                continue
            total += max(0.0, money_value(x.get("amount", 0)))
        nds_contract_by_trip[tid] = money_value(total)

    # Валидные платежи безнал-НДС, введённые на уровне рейса.
    nds_split_trip_ids = set()
    for tid, t in trip_by_id.items():
        parts = extract_trip_payments(t)
        positive_parts = [p for p in parts if to_float(p.get("amount")) > 0.01]
        if not positive_parts:
            continue

        contract_total = money_value(nds_contract_by_trip.get(tid, 0.0))
        split_total = money_value(sum(to_float(p.get("amount")) for p in positive_parts))

        if contract_total <= 0.01 or split_total > contract_total + 0.01:
            # Некорректное старое разбиение просто игнорируем; ниже используем
            # допустимые платежи по автомобилям.
            continue

        nds_split_trip_ids.add(tid)
        for p in positive_parts:
            amount = money_value(p.get("amount"))
            dt = _parse_analytics_date(p.get("date", ""))
            if dt is None and is_archived(t.get("archived", "0")):
                # Старые архивные безнал-платежи могли хранить сумму без даты.
                # Для графика используем безопасную историческую дату рейса.
                dt = _analytics_archive_fallback_date({}, t)
            if dt is None:
                if trip_year.get(tid) == year:
                    undated_nds += amount
                continue
            if dt.year == year:
                months[dt.month]["beznal_nds"] += amount

    # Платежи по автомобилям — также только по уникальным строкам.
    for tid, cars in unique_by_trip.items():
        for x in cars:
            payer_type = s(x.get("payer_type", "")).strip()

            if payer_type in ("нал", "эквайринг"):
                target_key = "cash_acq"
            elif payer_type == NDS_PAYER:
                if tid in nds_split_trip_ids:
                    continue
                target_key = "beznal_nds"
            else:
                continue

            payments = _analytics_car_payments(x)

            trip = trip_by_id.get(tid, {})
            archived_trip = is_archived(trip.get("archived", "0"))
            fallback_dt = (
                _analytics_archive_fallback_date(x, trip)
                if archived_trip else None
            )

            # Для архива старого формата не теряем оплату:
            # 1) существующие суммы без даты получают архивную fallback-дату;
            # 2) если paid=1, но сохранённых частей меньше стоимости перевозки,
            #    добавляем только НЕДОСТАЮЩУЮ часть, а не всю сумму повторно.
            prepared_payments = []
            recorded_total = 0.0
            for p in payments:
                p2 = dict(p)
                amount = max(0.0, money_value(p2.get("amount", 0)))
                if amount > 0.01:
                    recorded_total += amount
                    if (archived_trip and fallback_dt is not None
                            and _parse_analytics_date(p2.get("date", "")) is None):
                        p2["date"] = fallback_dt.strftime("%d.%m.%Y")
                prepared_payments.append(p2)

            amount_limit = max(0.0, money_value(x.get("amount", 0)))
            if (archived_trip and check_paid(x.get("paid", "0"))
                    and amount_limit > 0.01):
                missing_paid = max(0.0, amount_limit - min(recorded_total, amount_limit))
                if missing_paid > 0.01 and fallback_dt is not None:
                    prepared_payments.append({
                        "amount": money_value(missing_paid),
                        "date": fallback_dt.strftime("%d.%m.%Y"),
                        "paid_to": "",
                        "comment": "Архивная оплата старого формата",
                    })

            for p in prepared_payments:
                amount = money_value(p.get("amount"))
                if amount <= 0.01:
                    continue
                dt = _parse_analytics_date(p.get("date", ""))
                if dt is None:
                    if trip_year.get(tid) == year:
                        if target_key == "cash_acq":
                            undated_cash += amount
                        else:
                            undated_nds += amount
                    continue
                if dt.year == year:
                    months[dt.month][target_key] += amount

    rows = []
    for num, name, short in ANALYTICS_MONTHS:
        row = months[num]
        row["transport_amount"] = money_value(row["transport_amount"])
        row["cash_acq"] = money_value(row["cash_acq"])
        row["beznal_nds"] = money_value(row["beznal_nds"])
        row["total_payments"] = money_value(row["cash_acq"] + row["beznal_nds"])
        rows.append(row)

    total_trips = sum(r["trips"] for r in rows)
    total_transport_amount = money_value(sum(r["transport_amount"] for r in rows))
    dated_cash = money_value(sum(r["cash_acq"] for r in rows))
    dated_nds = money_value(sum(r["beznal_nds"] for r in rows))

    transport_trip_rows.sort(
        key=lambda r: (r["month_num"], _parse_analytics_date(r["date"]) or datetime.min,
                       r["trip_id"])
    )

    return {
        "year": year,
        "rows": rows,
        "total_trips": total_trips,
        "total_transport_amount": total_transport_amount,
        "transport_trip_rows": transport_trip_rows,
        "dated_cash_acq": dated_cash,
        "dated_beznal_nds": dated_nds,
        "undated_cash_acq": money_value(undated_cash),
        "undated_beznal_nds": money_value(undated_nds),
        "total_cash_acq": dated_cash,
        "total_beznal_nds": dated_nds,
        "total_payments": money_value(dated_cash + dated_nds),
    }

def render_business_analytics(trips, shipments):
    """Рисует аналитику, включая архивные нал/эквайринг/безнал оплаты."""
    st.markdown("---")
    st.subheader("📊 Аналитика бизнеса")
    st.caption(
        "Рейсы считаются по дате выезда. В денежные показатели попадают только "
        "реально записанные суммы платежей с указанной датой. Флаг «Оплачен» сам "
        "по себе больше не создаёт выручку."
    )

    years = _analytics_available_years(trips, shipments)
    current_year = datetime.now().year
    default_idx = years.index(current_year) if current_year in years else 0

    c_year, c_note = st.columns([1, 3])
    with c_year:
        selected_year = st.selectbox(
            "Год аналитики",
            options=years,
            index=default_idx,
            key="business_analytics_year",
        )
    with c_note:
        st.caption(
            "Активные, завершённые и архивные рейсы учитываются в графиках; "
            "удалённые — нет. Старые технические резервные поля автоматически "
            "исключаются из расчёта. Для старого архива без детальной истории оплаты "
            "используется сохранённая дата оплаты рейса/авто, а при её отсутствии — "
            "историческая дата завершения/рейса."
        )

    data = build_business_analytics(trips, shipments, selected_year)

    st.markdown(
        '<div class="analytics-kpi-grid">'
        '<div class="analytics-kpi-card"><div class="analytics-kpi-label">Рейсов за год</div>'
        '<div class="analytics-kpi-value">' + s(data["total_trips"]) + '</div></div>'
        '<div class="analytics-kpi-card"><div class="analytics-kpi-label">Нал + эквайринг</div>'
        '<div class="analytics-kpi-value">' + fmt_money(data["total_cash_acq"]) + ' ₽</div></div>'
        '<div class="analytics-kpi-card"><div class="analytics-kpi-label">Безнал с НДС</div>'
        '<div class="analytics-kpi-value">' + fmt_money(data["total_beznal_nds"]) + ' ₽</div></div>'
        '<div class="analytics-kpi-card"><div class="analytics-kpi-label">Всего подтверждено оплат</div>'
        '<div class="analytics-kpi-value">' + fmt_money(data["total_payments"]) + ' ₽</div></div>'
        '</div>',
        unsafe_allow_html=True,
    )

    # Единый порядок категорий для ВСЕХ графиков: январь → декабрь слева направо.
    month_order = [short for _, _, short in ANALYTICS_MONTHS]

    st.markdown("#### 🚛 Количество рейсов по месяцам")
    trips_df = pd.DataFrame([
        {"Месяц": r["short"], "Рейсы": r["trips"]}
        for r in data["rows"]
    ])
    trips_chart = (
        alt.Chart(trips_df)
        .mark_bar()
        .encode(
            x=alt.X("Месяц:N", sort=month_order, title=None),
            y=alt.Y("Рейсы:Q", title="Количество рейсов"),
            tooltip=[
                alt.Tooltip("Месяц:N", title="Месяц"),
                alt.Tooltip("Рейсы:Q", title="Рейсов"),
            ],
        )
        .properties(height=260)
    )
    st.altair_chart(trips_chart, use_container_width=True)

    st.markdown("#### 🚚 Сумма перевозок по месяцам")
    transport_df = pd.DataFrame([
        {"Месяц": r["short"], "Сумма перевозок": r["transport_amount"]}
        for r in data["rows"]
    ])
    transport_chart = (
        alt.Chart(transport_df)
        .mark_bar()
        .encode(
            x=alt.X("Месяц:N", sort=month_order, title=None),
            y=alt.Y("Сумма перевозок:Q", title="Сумма, ₽"),
            tooltip=[
                alt.Tooltip("Месяц:N", title="Месяц"),
                alt.Tooltip("Сумма перевозок:Q", title="Сумма перевозок", format=",.2f"),
            ],
        )
        .properties(height=300)
    )
    st.altair_chart(transport_chart, use_container_width=True)
    st.caption(
        "Сумма перевозок относится к месяцу по дате выезда рейса. "
        "Сначала считается сумма каждого рейса из уникальных автомобилей, "
        "затем складываются рейсы месяца."
    )

    with st.expander("🔎 Проверить сумму перевозок по рейсам", expanded=False):
        st.caption(
            "Здесь видно, из каких рейсов складывается каждый месяц. "
            "Если сумма сентября или октября кажется большой, можно сразу увидеть конкретный рейс."
        )
        detail_rows = []
        for r in data.get("transport_trip_rows", []):
            detail_rows.append({
                "Месяц": r["month"],
                "Дата выезда": r["date"],
                "Рейс": r["trip_id"],
                "Тягач": r["tractor"],
                "Водитель": r["driver"],
                "Маршрут": r["route"],
                "Авто": r["cars"],
                "Сумма рейса": fmt_money(r["amount"]) + " ₽",
            })
        if detail_rows:
            st.dataframe(
                pd.DataFrame(detail_rows),
                use_container_width=True,
                hide_index=True,
            )
        else:
            st.info("За выбранный год рейсы с суммами перевозок не найдены.")

    st.markdown("#### 💳 Подтверждённые оплаты по месяцам")
    money_df = pd.DataFrame([
        {
            "Месяц": r["short"],
            "Нал + эквайринг": r["cash_acq"],
            "Безнал с НДС": r["beznal_nds"],
        }
        for r in data["rows"]
    ])
    money_long = money_df.melt(
        id_vars=["Месяц"],
        value_vars=["Нал + эквайринг", "Безнал с НДС"],
        var_name="Тип оплаты",
        value_name="Сумма",
    )
    money_chart = (
        alt.Chart(money_long)
        .mark_bar()
        .encode(
            x=alt.X("Месяц:N", sort=month_order, title=None),
            xOffset=alt.XOffset("Тип оплаты:N"),
            y=alt.Y("Сумма:Q", title="Подтверждённые оплаты, ₽"),
            color=alt.Color("Тип оплаты:N", title="Тип оплаты"),
            tooltip=[
                alt.Tooltip("Месяц:N", title="Месяц"),
                alt.Tooltip("Тип оплаты:N", title="Тип оплаты"),
                alt.Tooltip("Сумма:Q", title="Сумма", format=",.2f"),
            ],
        )
        .properties(height=320)
    )
    st.altair_chart(money_chart, use_container_width=True)

    st.markdown("#### 📋 По месяцам")
    table_rows = []
    for r in data["rows"]:
        table_rows.append({
            "Месяц": r["month"],
            "Рейсов": r["trips"],
            "Сумма перевозок": fmt_money(r["transport_amount"]) + " ₽",
            "Нал + эквайринг": fmt_money(r["cash_acq"]) + " ₽",
            "Безнал с НДС": fmt_money(r["beznal_nds"]) + " ₽",
            "Всего оплат": fmt_money(r["total_payments"]) + " ₽",
        })

    table_rows.append({
        "Месяц": "ИТОГО ЗА ГОД",
        "Рейсов": data["total_trips"],
        "Сумма перевозок": fmt_money(data["total_transport_amount"]) + " ₽",
        "Нал + эквайринг": fmt_money(data["total_cash_acq"]) + " ₽",
        "Безнал с НДС": fmt_money(data["total_beznal_nds"]) + " ₽",
        "Всего оплат": fmt_money(data["total_payments"]) + " ₽",
    })
    st.dataframe(
        pd.DataFrame(table_rows),
        use_container_width=True,
        hide_index=True,
    )

    undated_total = money_value(
        data["undated_cash_acq"] + data["undated_beznal_nds"]
    )
    if undated_total > 0.01:
        st.warning(
            "Есть введённые суммы без даты на " + fmt_money(undated_total)
            + " ₽. Они НЕ включены в годовой итог. Укажите дату платежа, "
              "чтобы сумма попала в нужный месяц и год."
        )

    # Технические резервные поля и старые превышения намеренно не показываем:
    # они уже исключены из расчётов и не являются реальными деньгами бизнеса.

# ============================================================

def inject_responsive_ui_css():
    """Только визуальное оформление: логика и структура приложения не меняются."""
    st.markdown(
        """
        <style>
        :root {
            --ui-bg: #f4f7fb;
            --ui-card: #ffffff;
            --ui-border: #dbe5f0;
            --ui-text: #0f172a;
            --ui-muted: #64748b;
            --ui-blue: #2563eb;
        }
        html, body, [class*="css"] { font-family: Inter, "Segoe UI", Arial, sans-serif; }
        .stApp {
            background:
                radial-gradient(circle at 14% -8%, rgba(37,99,235,.10), transparent 30rem),
                linear-gradient(180deg, #f8fbff 0px, var(--ui-bg) 320px, var(--ui-bg) 100%);
            color: var(--ui-text);
        }
        header[data-testid="stHeader"] { background: rgba(244,247,251,.84); }
        .block-container {
            max-width: 1500px;
            padding-top: 0.65rem;
            padding-bottom: 2.2rem;
        }
        .app-title-wrap {
            background: rgba(255,255,255,.97);
            border: 1px solid var(--ui-border);
            border-left: 5px solid var(--ui-blue);
            border-radius: 14px;
            padding: 15px 18px 14px 18px;
            margin: 0 0 12px 0;
            box-shadow: 0 5px 18px rgba(15,23,42,.055);
        }
        .app-title {
            color: #0b1f3a;
            font-size: 1.85rem;
            font-weight: 800;
            line-height: 1.12;
            letter-spacing: -.02em;
        }
        .app-title-sub { color: var(--ui-muted); font-size: .88rem; margin-top: 5px; }
        h1 { font-size: 1.75rem !important; color: var(--ui-text) !important; font-weight: 800 !important; }
        h2 { font-size: 1.35rem !important; color: var(--ui-text) !important; }
        h3 { font-size: 1.15rem !important; color: var(--ui-text) !important; }
        h4 { font-size: 1.05rem !important; color: var(--ui-text) !important; }

        section[data-testid="stSidebar"] {
            background: #f8fbff;
            border-right: 1px solid var(--ui-border);
        }
        section[data-testid="stSidebar"] > div { padding-top: 1rem; }

        .stButton > button, .stDownloadButton > button {
            border-radius: 9px !important;
            border: 1px solid #cbd9e8 !important;
            background: #ffffff !important;
            color: #17324d !important;
            box-shadow: 0 1px 2px rgba(15,23,42,.025);
            transition: all .13s ease;
        }
        .stButton > button:hover, .stDownloadButton > button:hover {
            border-color: #6fa1ef !important;
            color: #174ea6 !important;
            background: #f6faff !important;
            box-shadow: 0 2px 7px rgba(37,99,235,.09);
        }
        .stButton button p, .stDownloadButton button p { font-size: 0.88rem; }

        div[data-testid="stExpander"] {
            background: rgba(255,255,255,.94);
            border: 1px solid var(--ui-border) !important;
            border-radius: 11px !important;
            box-shadow: 0 1px 4px rgba(15,23,42,.025);
        }
        div[data-testid="stForm"] {
            background: rgba(255,255,255,.94);
            border-color: var(--ui-border) !important;
            border-radius: 12px !important;
        }
        div[data-baseweb="input"] > div,
        div[data-baseweb="select"] > div,
        textarea { background: #ffffff !important; border-color: #cfdae7 !important; }

        div[data-testid="stRadio"] div[role="radiogroup"] {
            display: flex;
            flex-wrap: wrap;
            gap: 0.30rem 0.85rem;
            background: rgba(255,255,255,.88);
            border: 1px solid var(--ui-border);
            padding: 7px 10px;
            border-radius: 11px;
            width: fit-content;
            max-width: 100%;
        }
        div[data-testid="stRadio"] label p {
            font-size: 0.88rem !important;
            font-weight: 600;
            color: #27425f;
        }

        .trip-section-title {
            font-size: 1.08rem;
            font-weight: 750;
            line-height: 1.25;
            margin: 0.15rem 0 0.25rem 0;
            color: #102a43;
        }
        .trip-compact {
            margin-top: 0.35rem;
            margin-bottom: 0.35rem;
            background: rgba(255,255,255,.88);
            border: 1px solid var(--ui-border);
            border-radius: 12px;
            padding: 9px;
        }
        .trip-mini-grid {
            display: grid;
            grid-template-columns: repeat(4, minmax(0, 1fr));
            gap: 7px;
        }
        .trip-mini-card {
            border: 1px solid #dbe5f0;
            border-radius: 9px;
            padding: 8px 10px;
            min-width: 0;
            background: #ffffff;
        }
        .trip-mini-label { font-size: 0.70rem; line-height: 1.15; color: #718096; margin-bottom: 3px; }
        .trip-mini-value {
            font-size: 0.90rem;
            line-height: 1.25;
            font-weight: 700;
            color: #172b4d;
            overflow-wrap: anywhere;
        }
        .trip-mini-debt .trip-mini-value { color: #c53030; }
        .trip-route-line {
            display: flex;
            flex-wrap: wrap;
            gap: 5px 18px;
            font-size: 0.82rem;
            line-height: 1.35;
            padding: 8px 3px;
            color: #425466;
        }
        .trip-money-grid { margin-top: 1px; }

        .analytics-kpi-grid {
            display: grid;
            grid-template-columns: repeat(4, minmax(0, 1fr));
            gap: 8px;
            margin: 0.35rem 0 0.85rem 0;
        }
        .analytics-kpi-card {
            border: 1px solid var(--ui-border);
            border-radius: 11px;
            padding: 10px 12px;
            background: #ffffff;
            min-width: 0;
            box-shadow: 0 2px 8px rgba(15,23,42,.035);
        }
        .analytics-kpi-label { font-size: 0.72rem; color: #718096; margin-bottom: 3px; }
        .analytics-kpi-value {
            font-size: 1.03rem;
            font-weight: 750;
            color: #102a43;
            line-height: 1.2;
            overflow-wrap: anywhere;
        }
        div[data-testid="stDataFrame"] {
            background: #ffffff;
            border: 1px solid var(--ui-border);
            border-radius: 11px;
            overflow: hidden;
        }
        div[data-testid="stAlert"] { border-radius: 10px; }
        hr { border-color: #dde6ef !important; }

        @media (max-width: 768px) {
            .block-container { padding-left: 0.65rem; padding-right: 0.65rem; padding-top: 0.45rem; }
            .app-title-wrap { padding: 12px 13px; border-radius: 11px; }
            .app-title { font-size: 1.38rem; }
            .app-title-sub { font-size: .78rem; }
            h1 { font-size: 1.32rem !important; }
            h2 { font-size: 1.18rem !important; }
            h3 { font-size: 1.06rem !important; }
            .trip-section-title { font-size: 1rem; }
            .trip-mini-grid { grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 6px; }
            .trip-mini-card { padding: 6px 7px; }
            .trip-mini-label { font-size: 0.66rem; }
            .trip-mini-value { font-size: 0.84rem; }
            .trip-route-line { font-size: 0.77rem; gap: 4px 12px; }
            .analytics-kpi-grid { grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 6px; }
            .analytics-kpi-card { padding: 7px 8px; }
            .analytics-kpi-label { font-size: 0.67rem; }
            .analytics-kpi-value { font-size: 0.90rem; }
            .stButton > button, .stDownloadButton > button {
                min-height: 2.25rem;
                padding: 0.30rem 0.42rem;
                white-space: normal;
            }
            div[data-testid="stRadio"] label p { font-size: 0.80rem !important; }
            div[data-testid="stHorizontalBlock"] { gap: 0.35rem; }
        }
        @media (max-width: 430px) {
            .trip-mini-grid { grid-template-columns: 1fr; }
            .analytics-kpi-grid { grid-template-columns: 1fr; }
            .trip-route-line { display: grid; grid-template-columns: 1fr; }
            div[data-testid="stRadio"] div[role="radiogroup"] { gap: 0.1rem 0.5rem; width: 100%; }
        }
        </style>
        """,
        unsafe_allow_html=True,
    )


# ============================================================
# ЧАСТЬ 2/2: главная страница и точка входа
# v2.15.0: верхняя навигация + компактная мобильная карточка
# ============================================================

def main_page():
    inject_responsive_ui_css()

    u = st.session_state["user"]
    role = u["role"]
    role_label = ROLE_LABELS.get(role, role)

    st.sidebar.markdown("## 🚛 Учёт перевозок")
    st.sidebar.markdown(
        "**" + s(u["full_name"]) + "**  \n"
        "Роль: **" + role_label + "**"
    )

    current_view = st.session_state.get("view_mode", "active")
    view_options = ["active", "completed", "archive"]
    if current_view not in view_options:
        current_view = "active"

    with st.sidebar.expander("ℹ️ Что доступно моей роли", expanded=False):
        if role in ("admin", "director"):
            st.write("Можно управлять рейсами, автомобилями, оплатами и безналом с НДС.")
        elif role == "dispatcher":
            st.write("Можно работать с рейсами и автомобилями. Безнал с НДС оплачивает директор или администратор.")
        else:
            st.write("Можно создавать и редактировать рейсы/авто, переносить машины и работать с доступными оплатами.")
        st.caption("Недоступные действия скрыты или отключены автоматически.")

    if role in ("admin", "director", "dispatcher"):
        with st.sidebar.expander("⚙️ Управление", expanded=False):
            admin_panel()

    if st.sidebar.button("🚪 Выйти", key="btn_logout", use_container_width=True):
        logout()

    st.markdown(
        '<div class="app-title-wrap">'
        '<div class="app-title">🚛 Учёт рейсов и перевозок</div>'
        '<div class="app-title-sub">Рейсы · автомобили · оплаты · документы · аналитика</div>'
        '</div>',
        unsafe_allow_html=True,
    )

    # Основные разделы снова находятся сверху, а не в боковом меню.
    selected_view = st.radio(
        "Основной раздел",
        options=view_options,
        index=view_options.index(current_view),
        format_func=lambda key: VIEW_LABELS[key],
        horizontal=True,
        key="top_view_mode",
        label_visibility="collapsed",
    )
    if selected_view != current_view:
        st.session_state["view_mode"] = selected_view
        st.rerun()
    view_mode = selected_view

    st.caption(
        "Откройте карточку рейса и выберите: Рейс, Автомобили, Оплата, Документы или Переносы."
    )

    # ============================================================
    # ПЕЧАТЬ ОБЩЕГО СПИСКА
    # ============================================================
    if st.session_state.get("show_print_all"):
        if st.button("Назад к списку", key="btn_back_from_print"):
            st.session_state.pop("show_print_all", None)
            st.rerun()
        st.subheader("🖨 Печать списка автомобилей")
        print_rows = []
        all_trips_list = get_trips()
        all_hist_list = get_transfer_history()
        active_trips = [t for t in all_trips_list
                        if not is_archived(t.get("archived", "0"))
                        and not is_deleted(t.get("deleted_at", ""))
                        and not check_completed(t.get("completed", "0"))]
        active_trips = sorted(
            active_trips,
            key=lambda t: date_sort_key(t.get("date_departure", "")),
            reverse=True,
        )
        all_ships = get_shipments()
        for t in active_trips:
            tid = s(t.get("id"))
            cars = [x for x in all_ships if s(x.get("trip_id")) == tid]
            for x in sorted(cars, key=lambda z: int(to_float(z.get("position")))):
                if is_effectively_issued(x):
                    continue
                fin = calculate_financials(x)
                tr_to = s(x.get("transferred_to_trip", ""))
                tr_from = s(x.get("transferred_from_trip", ""))
                note = ""
                if is_trace_row(x):
                    target_id = find_target_trip_for_parent(tr_to, all_ships)
                    note = "Перенесён в: " + resolve_target_trip_label(
                        target_id, all_trips_list, all_hist_list)
                elif tr_from or tr_to:
                    src_lbl = resolve_source_trip_label_for_car(
                        x, all_trips_list, all_hist_list)
                    note = "Перенесён из: " + src_lbl
                print_rows.append({
                    "tractor": s(t.get("tractor_number", "")),
                    "driver": s(t.get("driver", "")),
                    "loading_from": s(t.get("loading_from", "")),
                    "loading_contract_number": s(t.get("loading_contract_number", "")),
                    "route": s(t.get("route", "")),
                    "dep": date_to_display_safe(t.get("date_departure", "")),
                    "position": s(x.get("position", "")),
                    "car_model": s(x.get("car_model", "")),
                    "vin": s(x.get("vin", "")),
                    "client": s(x.get("client", "")),
                    "customer": s(x.get("customer", "")),
                    "delivery_city": s(x.get("delivery_city", "")),
                    "debt": fin["debt"],
                    "note": note,
                })
        if not print_rows:
            st.info("Нет активных авто для печати.")
        else:
            show_print_list(print_rows)
        return

    # ============================================================
    # АКТ
    # ============================================================
    if st.session_state.get("show_act_for"):
        sid = st.session_state["show_act_for"]
        shipments = get_shipments(fresh=True)
        all_trips_now = get_trips(fresh=True)
        ship = next((x for x in shipments if s(x.get("id")) == s(sid)), None)
        if ship:
            trip = find_trip_by_id(all_trips_now, ship.get("trip_id"))
            if trip:
                if st.button("Назад к списку", key="btn_back_from_act"):
                    st.session_state.pop("show_act_for", None)
                    st.rerun()
                st.subheader("Акт приема-передачи")
                show_act(trip, ship)
                return
        st.session_state.pop("show_act_for", None)

    # ============================================================
    # ИСТОРИЯ
    # ============================================================
    if st.session_state.get("show_history_for"):
        sid = st.session_state["show_history_for"]
        shipments = get_shipments(fresh=True)
        ship = next((x for x in shipments if s(x.get("id")) == s(sid)), None)
        if st.button("Назад", key="btn_back_from_hist"):
            st.session_state.pop("show_history_for", None)
            st.rerun()
        st.subheader("📜 История перемещений автомобиля")
        if ship:
            st.markdown("**Авто:** " + s(ship.get("car_model", ""))
                        + " · VIN: " + s(ship.get("vin", "")))
        hist = get_shipment_history(sid)
        if not hist:
            st.info("Перемещений не найдено.")
        else:
            for h in hist:
                cancelled = s(h.get("cancelled", "")) == "1"
                prefix = "🔴 " if cancelled else ""
                suffix = "  ❌ отменён" if cancelled else ""
                st.markdown(
                    prefix + "**" + s(h.get("ts")) + "** — "
                    + "Рейс #" + s(h.get("from_trip"))
                    + " (поз. " + s(h.get("from_position")) + ")  →  "
                    + "Рейс #" + s(h.get("to_trip"))
                    + " (поз. " + s(h.get("to_position")) + ")"
                    + ("  · " + s(h.get("note")) if h.get("note") else "")
                    + ("  · " + s(h.get("user_login")) if h.get("user_login") else "")
                    + suffix
                )
        return

    # ============================================================
    # ПЕЧАТЬ ПО РЕЙСУ
    # ============================================================
    if st.session_state.get("show_print_trip"):
        rows = st.session_state["show_print_trip"]
        if st.button("Назад к списку", key="btn_back_from_print_trip"):
            st.session_state.pop("show_print_trip", None)
            st.rerun()
        st.subheader("🖨 Печать списка по рейсу")
        show_print_list(rows)
        return

    # ============================================================
    # ОСНОВНОЙ СПИСОК
    # ============================================================
    trips = get_trips()
    shipments = get_shipments()
    transfer_history = get_transfer_history()

    col_p1, col_p2, col_p3 = st.columns([1, 1, 4])
    with col_p1:
        if st.button("🖨 Печать списка авто", key="btn_print_all",
                     use_container_width=True):
            st.session_state["show_print_all"] = True
            st.rerun()

    if view_mode == "archive":
        filtered = [t for t in trips
                    if is_archived(t.get("archived", "0"))
                    and not is_deleted(t.get("deleted_at", ""))]
        st.info("Показаны архивные рейсы")
    elif view_mode == "completed":
        filtered = [t for t in trips
                    if check_completed(t.get("completed", "0"))
                    and not is_archived(t.get("archived", "0"))
                    and not is_deleted(t.get("deleted_at", ""))]
        st.info("Показаны завершённые рейсы")
    else:
        filtered = [t for t in trips
                    if not is_archived(t.get("archived", "0"))
                    and not is_deleted(t.get("deleted_at", ""))]
        filtered = [t for t in filtered if not check_completed(t.get("completed", "0"))]

    with st.expander("🔎 Поиск и сортировка рейсов", expanded=False):
        c1, c2, c3 = st.columns(3)
        sort_by = c1.selectbox("Сортировать по", [
            "Дата выезда (новые сверху)", "Дата выезда (старые сверху)",
            "Гос номер тягача (А-Я)", "Гос номер тягача (Я-А)",
        ])
        search_tractor = c2.text_input("Поиск по гос номеру тягача")
        search_driver = c3.text_input("Поиск по водителю")

    if search_tractor:
        filtered = [t for t in filtered
                    if search_tractor.lower() in s(t.get("tractor_number", "")).lower()]
    if search_driver:
        filtered = [t for t in filtered
                    if search_driver.lower() in s(t.get("driver", "")).lower()]

    if sort_by.startswith("Дата выезда"):
        filtered = sorted(filtered,
                          key=lambda t: date_sort_key(t.get("date_departure", "")),
                          reverse=("новые" in sort_by))
    else:
        filtered = sorted(filtered, key=lambda t: s(t.get("tractor_number", "")),
                          reverse=("Я-А" in sort_by))

    if view_mode == "active":
        col1, col2 = st.columns([3, 1])
        with col2:
            if can("create_trip", role) and st.button("➕ Добавить новый рейс",
                    key="btn_show_new_trip", use_container_width=True):
                st.session_state["show_new_trip"] = True

        if st.session_state.get("show_new_trip"):
            with st.form("new_trip"):
                st.subheader("➕ Новый рейс")
                c1, c2 = st.columns(2)
                tractor = c1.text_input("Гос номер тягача")
                driver = c2.text_input("Водитель ФИО")
                c3, c4 = st.columns(2)
                route = c3.text_input("Маршрут")
                dep = c4.text_input("Дата выезда (ДД.ММ.ГГГГ)", placeholder="12.05.2026")
                c5, c6 = st.columns(2)
                loading_from = c5.text_input(
                    "Погрузка от (пример: Рейл Трейн Сервис)",
                    placeholder="Рейл Трейн Сервис"
                )
                loading_contract_number = c6.text_input(
                    "Договор №",
                    placeholder="например 154/26"
                )
                ret = st.text_input("Дата возвращения (можно пусто)", placeholder="20.05.2026")
                ok = st.form_submit_button("Сохранить рейс")
                cancel = st.form_submit_button("Отмена")
            if cancel:
                st.session_state.pop("show_new_trip", None)
                st.rerun()
            if ok:
                if not tractor or not driver or not dep:
                    st.error("Заполните: гос номер, водителя, дату выезда")
                else:
                    try:
                        dep_fmt = parse_date_ui(dep)
                        ret_fmt = parse_date_ui(ret)
                    except ValueError as e:
                        st.error(str(e))
                    else:
                        new_trip_number = create_trip(
                            tractor, driver, route, dep_fmt, ret_fmt, u["login"],
                            loading_from=loading_from,
                            loading_contract_number=loading_contract_number,
                        )
                        log_action(u["login"], role, "create_trip",
                                   "рейс № " + s(new_trip_number) + " | "
                                   + s(tractor) + " " + s(driver) + " " + s(dep_fmt)
                                   + (" | погрузка от " + s(loading_from)
                                      if s(loading_from) else "")
                                   + (" | договор № " + s(loading_contract_number)
                                      if s(loading_contract_number) else ""))
                        st.session_state.pop("show_new_trip", None)
                        st.success("Рейс № " + s(new_trip_number) + " добавлен")
                        st.rerun()

    st.subheader(VIEW_LABELS.get(view_mode, "Рейсы"))

    if not filtered:
        st.info("Ничего не найдено.")
    else:
        for t in filtered:
            trip_id = t.get("id")
            tractor = s(t.get("tractor_number", ""))
            driver = s(t.get("driver", ""))
            route = s(t.get("route", ""))
            dep = date_to_display_safe(t.get("date_departure", ""))
            ret = date_to_display_safe(t.get("date_return", ""))
            loading_from = s(t.get("loading_from", ""))
            loading_contract_number = s(t.get("loading_contract_number", ""))
            trip_number = s(t.get("trip_number", "")).strip()
            payment_date = s(t.get("payment_date", ""))
            payment_comment = s(t.get("payment_comment", ""))
            trip_completed = check_completed(t.get("completed", "0"))
            trip_completed_at = date_to_display_safe(t.get("completed_at", ""))
            trip_inv_num = s(t.get("invoice_number", ""))
            trip_inv_date = date_to_display_safe(t.get("invoice_date", ""))

            cars = [x for x in shipments if s(x.get("trip_id")) == s(trip_id)]

            total = 0.0
            total_advance = 0.0
            total_debt = 0.0
            total_nds = 0.0
            for x in cars:
                f = calculate_financials(x)
                total += f["amount"]
                total_advance += f["advance"]
                total_debt += f["debt"]
                total_nds += f["nds"]

            active_count = count_active_cars(cars)
            issued_count = count_issued_cars(cars)
            outgoing_count = count_outgoing_transfers(cars)
            incoming_count = count_incoming_transfers(cars)
            incoming_on_route = count_incoming_on_route(cars)
            incoming_amount, incoming_advance, incoming_debt = sum_incoming_transfers(cars)
            free_pos = next_free_position(cars)

            total_in_route = active_count + incoming_on_route

            has_transfers = outgoing_count > 0

            has_nds = is_trip_beznal_nds(trip_id, cars)

            payments_info = compute_trip_nds_debt(t, cars) if has_nds else None

            incoming_from_labels = []
            for x in cars:
                if is_trace_row(x):
                    continue
                if not is_transferred_car(x):
                    continue
                lbl = resolve_source_trip_label_for_car(x, trips, transfer_history)
                if lbl and lbl != "—" and lbl not in incoming_from_labels:
                    incoming_from_labels.append(lbl)

            header_html = render_trip_header(
                trip_id, tractor, driver, route, dep, ret,
                trip_completed, trip_completed_at,
                active_count, issued_count,
                total, total_advance, total_debt, total_nds,
                has_nds=has_nds,
                trip_invoice_number=trip_inv_num,
                trip_invoice_date=trip_inv_date,
                has_transfers=has_transfers,
                transfers_count=outgoing_count,
                incoming_count=incoming_count,
                incoming_from_labels=incoming_from_labels,
                incoming_total_amount=incoming_amount,
                incoming_total_advance=incoming_advance,
                incoming_total_debt=incoming_debt,
                total_in_route=total_in_route,
                loading_from=loading_from,
                loading_contract_number=loading_contract_number,
                payment_date=payment_date,
                payment_comment=payment_comment,
                payments_info=payments_info,
            )

            open_key = "open_details_" + s(trip_id)
            is_open = st.session_state.get(open_key, False)

            # v2.17.9 — отдельный столбец слева с постоянным номером рейса.
            col_no, col_hdr, col_btn = st.columns([1.15, 11.0, 1.0])
            with col_no:
                st.markdown(
                    '<div style="min-height:76px; display:flex; flex-direction:column; '
                    'align-items:center; justify-content:center; background:#eef5ff; '
                    'border:1px solid #bfd3f2; border-radius:10px; padding:8px 4px; '
                    'box-sizing:border-box; margin-top:0;">'
                    '<div style="font-size:11px; color:#5d6b7e; font-weight:600;">№ рейса</div>'
                    '<div style="font-size:24px; line-height:1.1; color:#1558b0; font-weight:800;">'
                    + (trip_number or "—") + '</div></div>',
                    unsafe_allow_html=True,
                )
            with col_hdr:
                st.markdown(header_html, unsafe_allow_html=True)
            with col_btn:
                if st.button("▾" if is_open else "▸",
                             key="btn_toggle_details_" + s(trip_id),
                             use_container_width=True,
                             help="Показать / скрыть подробности"):
                    st.session_state[open_key] = not is_open
                    st.rerun()

            if not st.session_state.get(open_key, False):
                continue

            # ====================================================
            # РАСКРЫТОЕ СОДЕРЖИМОЕ РЕЙСА
            # ====================================================


            total_paid_value = 0.0
            for _car in cars:
                if not is_trace_row(_car):
                    total_paid_value += calculate_financials(_car)["paid_value"]

            trip_ctx = {
                "trip": t,
                "trip_id": trip_id,
                "trip_number": trip_number,
                "tractor": tractor,
                "driver": driver,
                "route": route,
                "dep": dep,
                "ret": ret,
                "loading_from": loading_from,
                "loading_contract_number": loading_contract_number,
                "payment_date": payment_date,
                "payment_comment": payment_comment,
                "trip_completed": trip_completed,
                "trip_completed_at": trip_completed_at,
                "trip_inv_num": trip_inv_num,
                "trip_inv_date": trip_inv_date,
                "cars": cars,
                "trips": trips,
                "shipments": shipments,
                "transfer_history": transfer_history,
                "total": total,
                "total_advance": total_advance,
                "total_paid_value": total_paid_value,
                "total_debt": total_debt,
                "total_nds": total_nds,
                "active_count": active_count,
                "issued_count": issued_count,
                "outgoing_count": outgoing_count,
                "incoming_count": incoming_count,
                "incoming_on_route": incoming_on_route,
                "total_in_route": total_in_route,
                "free_pos": free_pos,
                "has_nds": has_nds,
                "payments_info": payments_info,
                "view_mode": view_mode,
                "role": role,
                "user_login": u["login"],
            }

            render_trip_workspace(trip_ctx)

            st.markdown("---")

    # ============================================================
    # ИТОГИ
    # ============================================================
    st.markdown("---")

    totals_data = compute_grand_totals(trips, shipments)

    spacer, totals = st.columns([1, 2])
    with totals:
        debt_nal = totals_data["debt_nal"]
        debt_beznal = totals_data["debt_beznal"]
        nds_beznal = totals_data["nds_beznal"]

        st.markdown(
            '<div style="background:#f5f5f5; border:1px solid #cccccc; '
            'border-radius:8px; padding:14px 18px; font-size:14px;">'

            '<div style="font-size:18px; font-weight:bold; margin-bottom:8px;">'
            'Итого</div>'

            '<div style="display:flex; justify-content:space-between; margin:4px 0; flex-wrap:wrap;">'
            '<span>Активных рейсов:</span><b>'
            + s(totals_data["active_trips"]) + '</b></div>'

            '<div style="display:flex; justify-content:space-between; margin:4px 0; flex-wrap:wrap;">'
            '<span>Авто:</span><b>'
            + s(totals_data["active_cars"]) + '</b></div>'

            '<div style="display:flex; justify-content:space-between; margin:4px 0; flex-wrap:wrap;">'
            '<span>Общая оплата (по активным рейсам):</span><b style="color:#2e7d32;">'
            + fmt_money(totals_data["total_paid"]) + ' ₽</b></div>'

            '<div style="display:flex; justify-content:space-between; margin:4px 0; '
            'border-top:1px solid #cccccc; padding-top:6px; flex-wrap:wrap;">'
            '<span><b>Задолженность нал / эквайринг</b> (активные):</span>'
            '<b style="color:' + ("#c62828" if debt_nal > 0.01 else "#2e7d32") + ';">'
            + fmt_money(debt_nal) + ' ₽</b></div>'

            '<div style="display:flex; justify-content:space-between; margin:4px 0; flex-wrap:wrap;">'
            '<span><b>Задолженность безнал (с НДС)</b> '
            '(активные + завершённые неоплаченные):</span>'
            '<b style="color:' + ("#c62828" if debt_beznal > 0.01 else "#2e7d32") + ';">'
            + fmt_money(debt_beznal) + ' ₽</b></div>'

            '<div style="display:flex; justify-content:space-between; margin:4px 0; flex-wrap:wrap;">'
            '<span><b>НДС 22%</b> (по безналу):</span>'
            '<b style="color:' + ("#c62828" if nds_beznal > 0.01 else "#2e7d32") + ';">'
            + fmt_money(nds_beznal) + ' ₽</b></div>'

            '</div>',
            unsafe_allow_html=True,
        )

    # Управленческая аналитика — ниже итогов.
    render_business_analytics(trips, shipments)


# ============================================================
# ТОЧКА ВХОДА
# ============================================================

def main():
    st.set_page_config(page_title="Учёт рейсов", page_icon="🚛", layout="wide")

    if not st.session_state.get("sheets_ready"):
        try:
            _gspread_call_with_retry(
                ensure_sheets_once,
                operation="проверка структуры Google Sheets",
                attempts=5,
            )
            st.session_state["sheets_ready"] = True
        except gspread.exceptions.APIError as e:
            st.error(_google_api_help_text(e, "проверка структуры Google Sheets"))
            st.info("Данные в Google Sheets не изменялись и не удалялись.")
            st.stop()
        except Exception as e:
            st.error("Ошибка подключения к Google Sheets: "
                     + type(e).__name__ + ": " + s(e))
            st.stop()

    try:
        ensure_first_admin()
        if "user" not in st.session_state:
            login_page()
        else:
            main_page()
    except gspread.exceptions.APIError as e:
        st.error(_google_api_help_text(e, "работа с Google Sheets"))
        st.info("Это ошибка доступа/квоты Google API, а не потеря данных приложения.")
        st.stop()


if __name__ == "__main__":
    main()

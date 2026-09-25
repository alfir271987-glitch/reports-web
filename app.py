# ============================================================
# УЧЁТ РЕЙСОВ И ПЕРЕВОЗОК — v2.9.7
# Streamlit + Google Sheets
# ЧАСТЬ 1/2
# ============================================================

import streamlit as st
import gspread
from oauth2client.service_account import ServiceAccountCredentials
from datetime import datetime, timedelta
import hashlib
import hmac
import uuid
import os
import re

SHEET_ID = "1MHslz3VRowoOLtS_AAQ5h-tFm-fIXBN8noqtyQpmKLw"
CRED_FILE = "gifted-mountain-508410-s3-96f38b5a0f63.json"

SCOPES = [
    "https://spreadsheets.google.com/feeds",
    "https://www.googleapis.com/auth/drive",
]

SHEET_SCHEMAS = {
    "users": ["login", "password_hash", "role", "full_name", "active",
              "failed_attempts", "locked_until"],
    "trips": ["id", "tractor_number", "driver", "route",
              "date_departure", "date_return", "created_by", "created_at",
              "archived", "completed", "completed_at",
              "invoice_number", "invoice_date", "deleted_at"],
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
                  "transferred_from_trip"],
    "transfer_history": ["id", "ts", "shipment_id", "from_trip", "to_trip",
                         "from_position", "to_position", "user_login", "note",
                         "cancelled"],
    "audit_log": ["id", "ts", "login", "role", "action", "details"],
    "act_log": ["id", "ts", "login", "trip_id", "shipment_id", "client"],
}

ROLES = ["admin", "director", "logist", "dispatcher"]
PAYER_TYPES = ["нал", "эквайринг", "безнал с НДС 22%"]
NDS_RATE = 0.22
NDS_PAYER = "безнал с НДС 22%"
MAX_CARS = 8
CACHE_TTL = 90
LOGIN_MAX_ATTEMPTS = 5
LOGIN_LOCK_MINUTES = 15


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
    return s(v).strip() != ""


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
        return bcrypt.hashpw(p.encode("utf-8"), bcrypt.gensalt(rounds=12)).decode("utf-8")
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

@st.cache_resource
def get_book():
    if os.path.exists(CRED_FILE):
        creds = ServiceAccountCredentials.from_json_keyfile_name(CRED_FILE, SCOPES)
    else:
        creds_dict = dict(st.secrets["gcp_service_account"])
        creds = ServiceAccountCredentials.from_json_keyfile_dict(creds_dict, SCOPES)
    client = gspread.authorize(creds)
    return client.open_by_key(SHEET_ID)


@st.cache_resource
def get_ws_cached(name):
    return get_book().worksheet(name)


@st.cache_data(ttl=CACHE_TTL, show_spinner=False)
def read_all_cached(name):
    ws = get_ws_cached(name)
    return ws.get_all_records()


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


@st.cache_data(ttl=30, show_spinner=False)
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


def ensure_sheets_once():
    book = get_book()
    existing = {ws.title for ws in book.worksheets()}
    for name, headers in SHEET_SCHEMAS.items():
        if name not in existing:
            ws = book.add_worksheet(title=name, rows=1000, cols=len(headers))
            ws.append_row(headers)
        else:
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


def next_shipment_id():
    return new_uuid()


# ============================================================
# ФИНАНСЫ И СТАТУСЫ
# ============================================================

def calculate_financials(row):
    amount = to_float(row.get("amount"))
    advance = to_float(row.get("advance"))
    paid_amount = to_float(row.get("paid_amount"))
    paid_flag = check_paid(row.get("paid", "0"))
    payer_type = s(row.get("payer_type"))

    debt = amount - advance
    if debt < 0.01 or paid_flag:
        debt = 0.0

    if payer_type == NDS_PAYER:
        nds = amount * NDS_RATE / (1 + NDS_RATE)
        amount_no_nds = amount - nds
    else:
        nds = 0.0
        amount_no_nds = amount

    if debt <= 0.01:
        status_text = "🟡 Оплачен"
    else:
        status_text = "🔴 Долг"

    return {
        "amount": amount,
        "advance": advance,
        "paid_amount": paid_amount,
        "debt": debt,
        "nds": nds,
        "amount_no_nds": amount_no_nds,
        "status_text": status_text,
    }


def is_car_active_on_avtovoz(x):
    """Авто «в пути»: не выдано и не помечено как перенесённое."""
    if check_issued(s(x.get("issued", "0"))):
        return False
    if s(x.get("transferred_to_trip", "")):
        return False
    return True


def count_active_cars(cars):
    return sum(1 for x in cars if is_car_active_on_avtovoz(x))


def count_issued_cars(cars):
    return sum(1 for x in cars if check_issued(s(x.get("issued", "0"))))


def is_trace_row(x):
    return (bool(s(x.get("transferred_to_trip", "")))
            and s(x.get("car_model", "")).startswith("Перенесён:"))


def is_transferred_car(x):
    """
    Авто считается перенесённым в текущий рейс, если:
    - это не trace-строка, И
    - у него заполнено либо AC (transferred_from_trip = id исходного рейса),
      либо AA (transferred_to_trip = id текущего рейса) — fallback.
    """
    if is_trace_row(x):
        return False
    ac = s(x.get("transferred_from_trip", "")).strip()
    aa = s(x.get("transferred_to_trip", "")).strip()
    return bool(ac or aa)


def count_outgoing_transfers(cars):
    """Сколько авто УШЛО из этого рейса = число trace-строк."""
    return sum(1 for x in cars if is_trace_row(x))


def count_incoming_transfers(cars):
    """Сколько авто ПРИНЯТО в этот рейс (по AC, fallback по AA)."""
    return sum(1 for x in cars if is_transferred_car(x))


def count_incoming_on_route(cars):
    """
    Сколько перенесённых авто РЕАЛЬНО ЕЩЁ В ПУТИ (не выданы).
    Используется для подсчёта общего числа авто в рейсе.
    """
    return sum(
        1 for x in cars
        if is_transferred_car(x)
        and not check_issued(s(x.get("issued", "0")))
    )


def sum_incoming_transfers(cars):
    """
    Возвращает (amount, advance, debt) — суммарные показатели
    всех авто, пришедших в рейс переносом ИЗ других рейсов.
    """
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


def next_free_position(cars):
    occupied = set()
    for x in cars:
        if not is_car_active_on_avtovoz(x):
            continue
        try:
            occupied.add(int(to_float(x.get("position"))))
        except Exception:
            pass
    for p in range(1, MAX_CARS + 1):
        if p not in occupied:
            return p
    return None


def describe_trip(trip):
    if not trip:
        return ""
    parts = []
    if s(trip.get("tractor_number", "")):
        parts.append(s(trip.get("tractor_number", "")))
    if s(trip.get("driver", "")):
        parts.append(s(trip.get("driver", "")))
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
    """По id родительского авто находит id рейса, куда его перенесли."""
    pid = s(parent_ship_id).strip()
    if not pid:
        return ""
    for c in all_shipments:
        if s(c.get("id")) == pid:
            return s(c.get("trip_id", ""))
    return ""


def resolve_source_trip_label_for_car(x, all_trips, transfer_history):
    """
    Для авто, перенесённого в текущий рейс, возвращает название рейса-источника.
    Основной путь — AC (transferred_from_trip = id исходного рейса).
    Fallback — через transfer_history по id авто.
    """
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
# CRUD: РЕЙСЫ
# ============================================================

def create_trip(tractor, driver, route, dep, ret, created_by):
    append_row("trips", {
        "id": next_trip_id(),
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
    })


def update_trip(trip_id, tractor, driver, route, dep, ret):
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

def create_shipment(trip_id, position, car_model, client, amount,
                    date_pay, paid_to, delivery_city, vin,
                    advance, advance_date,
                    payer_type, customer, contract_number,
                    nds_amount, amount_no_nds, created_by):
    append_row("shipments", {
        "id": next_shipment_id(),
        "trip_id": trip_id,
        "position": position,
        "car_model": s(car_model),
        "client": s(client),
        "amount": money_value(amount),
        "date_pay": s(date_pay),
        "paid_to": s(paid_to),
        "delivery_city": s(delivery_city),
        "vin": s(vin),
        "advance": money_value(advance),
        "advance_date": s(advance_date),
        "payer_type": s(payer_type),
        "customer": s(customer),
        "contract_number": s(contract_number),
        "nds_amount": money_value(nds_amount),
        "amount_no_nds": money_value(amount_no_nds),
        "created_by": s(created_by),
        "created_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "issued": "0",
        "issued_ever": "0",
        "paid": "0",
        "archived": "0",
        "paid_amount": 0,
        "invoice_number": "",
        "invoice_date": "",
        "transferred_to_trip": "",
        "transferred_at": "",
        "transferred_from_trip": "",
    })


def update_shipment(shipment_id, position, car_model, client, amount,
                    date_pay, paid_to, delivery_city, vin, advance, advance_date,
                    payer_type, customer, contract_number,
                    nds_amount, amount_no_nds):
    ws = get_ws_cached("shipments")
    row_idx = _find_row_index_by_id("shipments", shipment_id)
    if row_idx is None:
        return False
    payload = [
        {"range": "C" + str(row_idx), "values": [[position]]},
        {"range": "D" + str(row_idx), "values": [[s(car_model)]]},
        {"range": "E" + str(row_idx), "values": [[s(client)]]},
        {"range": "F" + str(row_idx), "values": [[money_value(amount)]]},
        {"range": "G" + str(row_idx), "values": [[s(date_pay)]]},
        {"range": "H" + str(row_idx), "values": [[s(paid_to)]]},
        {"range": "I" + str(row_idx), "values": [[s(delivery_city)]]},
        {"range": "J" + str(row_idx), "values": [[s(vin)]]},
        {"range": "K" + str(row_idx), "values": [[money_value(advance)]]},
        {"range": "L" + str(row_idx), "values": [[s(advance_date)]]},
        {"range": "M" + str(row_idx), "values": [[s(payer_type)]]},
        {"range": "N" + str(row_idx), "values": [[s(customer)]]},
        {"range": "O" + str(row_idx), "values": [[s(contract_number)]]},
        {"range": "P" + str(row_idx), "values": [[money_value(nds_amount)]]},
        {"range": "Q" + str(row_idx), "values": [[money_value(amount_no_nds)]]},
    ]
    ws.batch_update(payload, value_input_option="USER_ENTERED")
    invalidate_cache("shipments")
    return True


def toggle_issued(shipment_id, current_value, current_ever):
    ws = get_ws_cached("shipments")
    row_idx = _find_row_index_by_id("shipments", shipment_id)
    if row_idx is None:
        return current_value
    was_issued = s(current_value) == "1"
    if not was_issued:
        ws.update_cell(row_idx, 20, "1")
        ws.update_cell(row_idx, 21, "1")
    else:
        ws.update_cell(row_idx, 20, "0")
        ws.update_cell(row_idx, 22, "0")
    invalidate_cache("shipments")
    return "0" if was_issued else "1"


def toggle_paid(shipment_id, current_paid, current_amount, current_advance):
    ws = get_ws_cached("shipments")
    row_idx = _find_row_index_by_id("shipments", shipment_id)
    if row_idx is None:
        return current_paid
    was_paid = s(current_paid) == "1"
    amount_val = money_value(current_amount)
    advance_val = money_value(current_advance)

    if not was_paid:
        payload = [
            {"range": "X" + str(row_idx), "values": [[advance_val]]},
            {"range": "K" + str(row_idx), "values": [[amount_val]]},
            {"range": "V" + str(row_idx), "values": [["1"]]},
        ]
        ws.batch_update(payload, value_input_option="USER_ENTERED")
    else:
        payload = [
            {"range": "K" + str(row_idx), "values": [[advance_val]]},
            {"range": "X" + str(row_idx), "values": [[0]]},
            {"range": "V" + str(row_idx), "values": [["0"]]},
        ]
        ws.batch_update(payload, value_input_option="USER_ENTERED")
    invalidate_cache("shipments")
    return "1" if not was_paid else "0"


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

def pay_entire_trip_nds(trip_id, user_login, role):
    shipments = read_all("shipments", fresh=True)
    cars = [x for x in shipments
            if s(x.get("trip_id")) == s(trip_id)
            and s(x.get("payer_type", "")).strip() == NDS_PAYER]

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

        was_paid = check_paid(x.get("paid", "0"))
        amount_val = money_value(x.get("amount"))
        advance_val = money_value(x.get("advance"))
        debt_val = amount_val - advance_val
        if debt_val < 0:
            debt_val = 0.0

        if was_paid and debt_val <= 0.01:
            skipped_count += 1
            continue

        payload = [
            {"range": "X" + str(row_idx), "values": [[advance_val]]},
            {"range": "K" + str(row_idx), "values": [[amount_val]]},
            {"range": "V" + str(row_idx), "values": [["1"]]},
        ]
        ws.batch_update(payload, value_input_option="USER_ENTERED")
        paid_count += 1
        closed_sum += debt_val

    if paid_count > 0:
        invalidate_cache("shipments")
        log_action(user_login, role, "pay_entire_trip_nds",
                   "trip " + s(trip_id) + " | оплачено " + s(paid_count)
                   + " авто | закрыто " + fmt_money(closed_sum) + " ₽")

    return paid_count, skipped_count, closed_sum


def unpay_entire_trip_nds(trip_id, user_login, role):
    shipments = read_all("shipments", fresh=True)
    cars = [x for x in shipments
            if s(x.get("trip_id")) == s(trip_id)
            and s(x.get("payer_type", "")).strip() == NDS_PAYER]

    unpay_count = 0
    ws = get_ws_cached("shipments")

    for x in cars:
        sid = s(x.get("id"))
        row_idx = _find_row_index_by_id("shipments", sid)
        if row_idx is None:
            continue

        was_paid = check_paid(x.get("paid", "0"))
        if not was_paid:
            continue

        old_adv = money_value(x.get("paid_amount"))
        payload = [
            {"range": "K" + str(row_idx), "values": [[old_adv]]},
            {"range": "X" + str(row_idx), "values": [[0]]},
            {"range": "V" + str(row_idx), "values": [["0"]]},
        ]
        ws.batch_update(payload, value_input_option="USER_ENTERED")
        unpay_count += 1

    if unpay_count > 0:
        invalidate_cache("shipments")
        log_action(user_login, role, "unpay_entire_trip_nds",
                   "trip " + s(trip_id) + " | снято с " + s(unpay_count) + " авто")

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
            if len(row) < 29:
                row = row + [""] * (29 - len(row))
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
            if len(row) < 29:
                row = row + [""] * (29 - len(row))
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
        "Маршрут: " + route, "", "",
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
# ПЕЧАТЬ СПИСКА — v2.9.7 (один рейс = один альбомный лист)
# ============================================================

def render_print_list_doc(rows, title="Список перевозимых автомобилей"):
    """
    Формирует HTML для печати. Каждый рейс — на отдельном
    альбомном листе A4. Всё сжато так, чтобы 8 машин + итог
    гарантированно помещались на один лист.
    """
    groups = []
    cur_key = None
    cur_group = None
    for r in rows:
        key = (s(r.get("tractor")), s(r.get("driver")),
               s(r.get("route")), s(r.get("dep")))
        if key != cur_key:
            if cur_group:
                groups.append(cur_group)
            cur_key = key
            cur_group = {
                "tractor": s(r.get("tractor")),
                "driver": s(r.get("driver")),
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
    # --- Параметры листа: A4 Landscape, узкие поля ---
    p.append("@page { size: A4 landscape; margin: 0.7cm 0.8cm 0.7cm 0.8cm; }")
    p.append("html, body { margin: 0; padding: 0; }")
    p.append('body { font-family: "Times New Roman", Times, serif; '
             'font-size: 9pt; line-height: 1.15; color: #000; }')
    p.append("h1 { text-align: center; font-size: 12pt; "
             "text-transform: uppercase; margin: 0 0 6px 0; "
             "font-weight: bold; }")

    # --- Один рейс = один лист ---
    p.append(".trip-block { page-break-after: always; }")
    p.append(".trip-block:last-child { page-break-after: auto; }")

    # --- Компактная шапка рейса ---
    p.append(".header-trip { "
             "background: #f0f0f0; border: 1px solid #333; "
             "padding: 3px 6px; margin-bottom: 3px; "
             "font-size: 9.5pt; line-height: 1.2; }")
    p.append(".header-trip b { font-weight: bold; }")

    # --- Таблица ---
    p.append("table { width: 100%; border-collapse: collapse; "
             "table-layout: fixed; }")
    p.append("th, td { border: 1px solid #333; "
             "padding: 2px 4px; vertical-align: top; "
             "font-size: 9pt; line-height: 1.15; "
             "overflow: hidden; }")
    p.append("th { background: #e0e0e0; font-weight: bold; "
             "text-align: left; padding: 2px 4px; }")

    # --- Ширины колонок ---
    p.append("col.c-num    { width: 3%; }")
    p.append("col.c-pos    { width: 4%; }")
    p.append("col.c-model  { width: 18%; }")
    p.append("col.c-vin    { width: 15%; }")
    p.append("col.c-fio    { width: 14%; }")
    p.append("col.c-city   { width: 12%; }")
    p.append("col.c-debt   { width: 11%; }")
    p.append("col.c-note   { width: 23%; }")

    p.append(".num, .pos { text-align: center; }")
    p.append(".debt { text-align: right; font-weight: bold; "
             "white-space: nowrap; }")
    p.append(".vin { font-family: 'Courier New', monospace; "
             "font-size: 8pt; word-break: break-all; }")
    p.append(".note { font-size: 8pt; color: #222; }")
    p.append("tr.trace td { background: #f5f5f5; font-style: italic; }")
    p.append("tr.total td { background: #f0f0f0; font-weight: bold; "
             "border-top: 1.5px solid #333; }")
    p.append("</style></head><body>")
    p.append("<h1>" + title + "</h1>")

    for g in groups:
        p.append('<div class="trip-block">')

        # ---- Шапка рейса (одна строка) ----
        p.append('<div class="header-trip">')
        p.append('<b>Дата выезда:</b> ' + s(g["dep"]))
        p.append(' &nbsp;·&nbsp; <b>Тягач:</b> ' + s(g["tractor"]))
        p.append(' &nbsp;·&nbsp; <b>Водитель:</b> ' + s(g["driver"]))
        if s(g["route"]):
            p.append(' &nbsp;·&nbsp; <b>Маршрут:</b> ' + s(g["route"]))
        p.append("</div>")

        # ---- Таблица ----
        p.append("<table>")
        p.append("<colgroup>")
        p.append('<col class="c-num">')
        p.append('<col class="c-pos">')
        p.append('<col class="c-model">')
        p.append('<col class="c-vin">')
        p.append('<col class="c-fio">')
        p.append('<col class="c-city">')
        p.append('<col class="c-debt">')
        p.append('<col class="c-note">')
        p.append("</colgroup>")

        p.append("<thead><tr>")
        p.append('<th class="num">№</th>')
        p.append('<th class="pos">Поз</th>')
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
            p.append('<td class="pos">' + s(r.get("position", "")) + "</td>")
            p.append("<td>" + s(r.get("car_model", "")) + "</td>")
            p.append('<td class="vin">' + (s(r.get("vin", ""))[:17] or "—")
                     + "</td>")
            p.append("<td>" + fio + "</td>")
            p.append("<td>" + s(r.get("delivery_city", "")) + "</td>")
            p.append('<td class="debt">' + fmt_money(debt) + "</td>")
            p.append('<td class="note">' + note + "</td>")
            p.append("</tr>")

        # Итог по рейсу
        p.append('<tr class="total">')
        p.append('<td colspan="6" style="text-align:right;">Итого по рейсу:</td>')
        p.append('<td class="debt">' + fmt_money(total_debt) + "</td>")
        p.append("<td></td>")
        p.append("</tr>")

        p.append("</tbody></table>")
        p.append("</div>")  # /trip-block

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
                         default_position=None):
    defaults = {
        "position": default_position if default_position else 1,
        "car_model": "", "client": "", "vin": "",
        "delivery_city": "", "amount": "", "date_pay": "", "paid_to": "",
        "advance": "", "advance_date": "", "payer_type": "нал",
        "customer": "", "contract_number": "",
    }
    if c:
        try:
            defaults["position"] = int(to_float(c.get("position")) or 1)
        except Exception:
            defaults["position"] = 1
        defaults["car_model"] = s(c.get("car_model", ""))
        defaults["client"] = s(c.get("client", ""))
        defaults["vin"] = s(c.get("vin", ""))[:17]
        defaults["delivery_city"] = s(c.get("delivery_city", ""))
        ar = c.get("amount", "")
        defaults["amount"] = "" if ar in ("", None) else "{:.2f}".format(to_float(ar)).replace(".", ",")
        defaults["date_pay"] = date_to_display_safe(c.get("date_pay", ""))
        defaults["paid_to"] = s(c.get("paid_to", ""))
        av = c.get("advance", "")
        defaults["advance"] = "" if av in ("", None) else "{:.2f}".format(to_float(av)).replace(".", ",")
        defaults["advance_date"] = date_to_display_safe(c.get("advance_date", ""))
        defaults["payer_type"] = s(c.get("payer_type", "нал")) or "нал"
        defaults["customer"] = s(c.get("customer", ""))
        defaults["contract_number"] = s(c.get("contract_number", ""))

    try:
        payer_index = PAYER_TYPES.index(defaults["payer_type"])
    except ValueError:
        payer_index = 0

    with st.form(form_key, clear_on_submit=False):
        st.markdown("**Данные автомобиля**")

        col1, col2 = st.columns(2)
        position = col1.number_input("Позиция (1–8)", min_value=1, max_value=MAX_CARS,
                                     step=1, value=defaults["position"],
                                     key=form_key + "_pos")
        car_model = col2.text_input("Марка / модель авто", value=defaults["car_model"],
                                     key=form_key + "_car")

        col3, col4 = st.columns(2)
        client = col3.text_input("ФИО клиента", value=defaults["client"],
                                  key=form_key + "_client")
        customer = col4.text_input("Заказчик (если нет ФИО клиента)",
                                    value=defaults["customer"], key=form_key + "_customer")

        col5, col6 = st.columns(2)
        vin = col5.text_input("VIN (до 17 символов)", value=defaults["vin"],
                              key=form_key + "_vin")
        delivery_city = col6.text_input("Город доставки",
                                         value=defaults["delivery_city"],
                                         key=form_key + "_city")

        col7, col8 = st.columns(2)
        amount = col7.text_input("Сумма за перевозку (пример: 18333,33)",
                                  value=defaults["amount"], key=form_key + "_amount")
        payer_type = col8.selectbox("Способ оплаты", PAYER_TYPES, index=payer_index,
                                     key=form_key + "_payer")

        col9, col10 = st.columns(2)
        date_pay = col9.text_input("Дата оплаты (ДД.ММ.ГГГГ)",
                                    value=defaults["date_pay"], key=form_key + "_dpay")
        paid_to = col10.text_input("Кому произведён перевод",
                                    value=defaults["paid_to"], key=form_key + "_paidto")

        col11, col12 = st.columns(2)
        advance = col11.text_input("Аванс (сумма)", value=defaults["advance"],
                                    key=form_key + "_adv")
        advance_date = col12.text_input("Дата аванса (ДД.ММ.ГГГГ)",
                                         value=defaults["advance_date"],
                                         key=form_key + "_advd")

        contract_number = st.text_input("№ договора",
                                         value=defaults["contract_number"],
                                         key=form_key + "_contract")

        save = st.form_submit_button(submit_label)
        cancel = st.form_submit_button("Отмена")

    return {
        "position": position, "car_model": s(car_model), "client": s(client),
        "vin": s(vin), "delivery_city": s(delivery_city),
        "amount": amount, "date_pay": s(date_pay), "paid_to": s(paid_to),
        "advance": advance, "advance_date": s(advance_date),
        "payer_type": s(payer_type), "customer": s(customer),
        "contract_number": s(contract_number),
        "save": save, "cancel": cancel,
    }


# ============================================================
# ШАПКА РЕЙСА (v2.9.7)
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
                       total_in_route=None):
    # Цвет шапки: красный — только при задолженности.
    if active_count == 0 and issued_count == 0 and not has_transfers and incoming_count == 0:
        bg, bd = "#fafafa", "#dddddd"
    elif total_debt > 0.01:
        bg, bd = "#ffcdd2", "#e53935"
    else:
        bg, bd = "#c8e6c9", "#43a047"

    title = s(tractor) + " — " + s(driver) + " — " + s(route) + " — выезд " + s(dep)
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

    st.markdown(
        '<div style="background-color:' + bg +
        '; border:2px solid ' + bd +
        '; border-radius:8px; padding:10px 14px; margin-bottom:6px; '
        'font-size:14px; word-wrap:break-word;">'
        '<div style="display:flex; justify-content:space-between; '
        'align-items:center; gap:10px; flex-wrap:wrap;">'
        '<div style="font-weight:bold; flex:1; min-width:200px;">'
        + title +
        '</div>'
        '<div>' + badges + completed_html + '</div>'
        '</div>'
        '<div style="color:#333; margin-top:4px; display:flex; '
        'align-items:center; flex-wrap:wrap;">'
        '<div>' + stats + '</div>'
        '<div>' + inv_html + '</div>'
        '</div>'
        + incoming_stats_html
        + incoming_html +
        '</div>',
        unsafe_allow_html=True,
    )


# ============================================================
# БЛОК ОПЛАТЫ РЕЙСА (безнал с НДС)
# ============================================================

def render_trip_payment_button(trip_id, cars, role, user_login, trip_label=""):
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
            if st.button(
                "💵 Оплатить весь рейс (безнал с НДС) — "
                + fmt_money(unpaid_debt) + " ₽ / " + s(unpaid_count) + " авто",
                key="btn_pay_trip_nds_" + s(trip_id),
                use_container_width=True,
            ):
                st.session_state[key_confirm_pay] = True
                st.rerun()
        else:
            st.warning(
                "Оплатить **" + s(unpaid_count) + "** авто на сумму **"
                + fmt_money(unpaid_debt) + " ₽**? Долг будет обнулён — "
                "аванс поднимется до полной суммы."
            )
            c1, c2 = st.columns(2)
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
            if st.button(
                "↩ Снять оплату с рейса (безнал с НДС)",
                key="btn_unpay_trip_nds_" + s(trip_id),
                use_container_width=True,
            ):
                st.session_state[key_confirm_unpay] = True
                st.rerun()
        else:
            st.warning(
                "Снять оплату с **" + s(paid_count) + "** авто? "
                "Аванс вернётся к исходному значению, долг появится снова."
            )
            c1, c2 = st.columns(2)
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
# АВТОРИЗАЦИЯ
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
    users = read_all("users", fresh=True)
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


def can(action, role):
    rights = {
        "admin":      {"create_trip", "edit_trip", "delete_trip",
                       "create_ship", "edit_ship", "delete_ship",
                       "export", "print_act", "manage_users", "view_log",
                       "archive", "complete_trip", "transfer_ship",
                       "cancel_transfer", "edit_invoice", "edit_trace"},
        "director":   {"create_trip", "edit_trip", "delete_trip",
                       "create_ship", "edit_ship", "delete_ship",
                       "export", "print_act", "archive", "complete_trip",
                       "transfer_ship", "cancel_transfer", "edit_invoice",
                       "edit_trace"},
        "logist":     {"create_trip", "edit_trip", "create_ship", "edit_ship",
                       "export", "print_act", "archive", "complete_trip",
                       "transfer_ship", "cancel_transfer", "edit_invoice",
                       "edit_trace"},
        "dispatcher": {"create_trip", "edit_trip", "delete_trip",
                       "create_ship", "edit_ship", "delete_ship",
                       "export", "print_act", "archive", "complete_trip",
                       "transfer_ship", "cancel_transfer", "edit_invoice",
                       "edit_trace"},
    }
    return action in rights.get(role, set())


# ============================================================
# АДМИН-ПАНЕЛЬ
# ============================================================

def admin_panel():
    st.header("Админ-панель")
    tab1, tab2, tab3 = st.tabs(["Пользователи", "Журнал действий", "Журнал печати актов"])
    with tab1:
        st.subheader("Пользователи")
        users = read_all("users", fresh=True)
        for u in users:
            c1, c2, c3, c4, c5 = st.columns([2, 2, 2, 2, 2])
            c1.write(s(u.get("login")))
            c2.write(s(u.get("role")))
            c3.write(s(u.get("full_name")))
            active = is_active(u)
            c4.write("активен" if active else "отключён")
            if c5.button("Отключить" if active else "Включить",
                         key="toggle_user_" + s(u.get('login'))):
                ws = get_ws_cached("users")
                all_rows = ws.get_all_values()
                for i, row in enumerate(all_rows[1:], start=2):
                    if row[0] == u["login"]:
                        new_val = "0" if active else "1"
                        ws.update_cell(i, 5, new_val)
                        invalidate_cache("users")
                        log_action(st.session_state["user"]["login"],
                                   st.session_state["user"]["role"],
                                   "toggle_user", s(u['login']) + " -> " + new_val)
                        st.rerun()
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
                log_action(st.session_state["user"]["login"],
                           st.session_state["user"]["role"],
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
            st.info("Пока пусто")# ============================================================
# ЧАСТЬ 2/2: главная страница и точка входа
# ============================================================

def main_page():
    u = st.session_state["user"]
    role = u["role"]

    st.sidebar.markdown("**" + s(u["full_name"]) + "**  \nроль: `" + s(role) + "`")
    if st.sidebar.button("Выйти", key="btn_logout", use_container_width=True):
        logout()

    if role == "admin":
        with st.sidebar.expander("Админ-панель"):
            admin_panel()

    view_mode = st.session_state.get("view_mode", "active")
    colA, colB, colC, colD = st.columns([1, 1, 1, 3])
    with colA:
        if st.button("Активные", key="btn_view_active", use_container_width=True):
            st.session_state["view_mode"] = "active"
            st.rerun()
    with colB:
        if st.button("Завершённые", key="btn_view_completed", use_container_width=True):
            st.session_state["view_mode"] = "completed"
            st.rerun()
    with colC:
        if st.button("Архив", key="btn_view_archive", use_container_width=True):
            st.session_state["view_mode"] = "archive"
            st.rerun()

    st.title("Учёт рейсов и перевозок")

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
                if check_issued(s(x.get("issued", "0"))):
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

    with st.expander("Сортировка и поиск", expanded=False):
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
            if can("create_trip", role) and st.button("Добавить рейс",
                    key="btn_show_new_trip", use_container_width=True):
                st.session_state["show_new_trip"] = True

        if st.session_state.get("show_new_trip"):
            with st.form("new_trip"):
                st.subheader("Новый рейс")
                c1, c2 = st.columns(2)
                tractor = c1.text_input("Гос номер тягача")
                driver = c2.text_input("Водитель ФИО")
                c3, c4 = st.columns(2)
                route = c3.text_input("Маршрут")
                dep = c4.text_input("Дата выезда (ДД.ММ.ГГГГ)", placeholder="12.05.2026")
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
                        create_trip(tractor, driver, route, dep_fmt, ret_fmt, u["login"])
                        log_action(u["login"], role, "create_trip",
                                   s(tractor) + " " + s(driver) + " " + s(dep_fmt))
                        st.session_state.pop("show_new_trip", None)
                        st.success("Рейс добавлен")
                        st.rerun()

    st.subheader("Рейсы")

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

            # Общее число авто «в пути» = обычные активные + перенесённые невыданные
            total_in_route = active_count + incoming_on_route

            has_transfers = outgoing_count > 0

            has_nds = any(s(x.get("payer_type", "")).strip() == NDS_PAYER
                          for x in cars)

            # Собираем названия рейсов-источников для этого рейса
            incoming_from_labels = []
            for x in cars:
                if is_trace_row(x):
                    continue
                if not is_transferred_car(x):
                    continue
                lbl = resolve_source_trip_label_for_car(x, trips, transfer_history)
                if lbl and lbl != "—" and lbl not in incoming_from_labels:
                    incoming_from_labels.append(lbl)

            render_trip_header(trip_id, tractor, driver, route, dep, ret,
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
                               total_in_route=total_in_route)

            if has_nds and can("edit_ship", role) and view_mode in ("active", "completed"):
                render_trip_payment_button(trip_id, cars, role, u["login"],
                                           trip_label=tractor + " " + driver)

            if has_nds and can("edit_invoice", role) and view_mode in ("active", "completed"):
                inv_key = "show_inv_form_" + s(trip_id)
                label = ("✏ Изменить счёт" if (trip_inv_num or trip_inv_date)
                         else "📄 Выставить счёт")
                if st.button(label, key="btn_inv_" + s(trip_id)):
                    st.session_state[inv_key] = not st.session_state.get(inv_key, False)
                    st.rerun()

                if st.session_state.get(inv_key):
                    with st.form("inv_form_" + s(trip_id)):
                        st.markdown("**Счёт по рейсу (для безнала с НДС)**")
                        c1, c2 = st.columns(2)
                        new_num = c1.text_input("№ счёта", value=trip_inv_num,
                                                 key="invnum_trip_" + s(trip_id))
                        new_date = c2.text_input("Дата счёта (ДД.ММ.ГГГГ)",
                                                  value=trip_inv_date,
                                                  key="invdate_trip_" + s(trip_id))
                        save_inv = st.form_submit_button("💾 Сохранить счёт")
                        clear_inv = st.form_submit_button("🗑 Очистить счёт")
                        cancel_inv = st.form_submit_button("Отмена")
                    if cancel_inv:
                        st.session_state.pop(inv_key, None)
                        st.rerun()
                    if clear_inv:
                        update_trip_invoice(trip_id, "", "")
                        log_action(u["login"], role, "clear_trip_invoice",
                                   s(tractor) + " " + s(driver))
                        st.session_state.pop(inv_key, None)
                        st.success("Счёт удалён")
                        st.rerun()
                    if save_inv:
                        try:
                            date_fmt = parse_date_ui(new_date) if s(new_date).strip() else ""
                        except ValueError as ex:
                            st.error(str(ex))
                        else:
                            update_trip_invoice(trip_id, new_num, date_fmt)
                            log_action(u["login"], role, "edit_trip_invoice",
                                       s(tractor) + " " + s(driver) + " №" + s(new_num))
                            st.session_state.pop(inv_key, None)
                            st.success("Счёт сохранён")
                            st.rerun()

            with st.expander("Подробнее ▾", expanded=False):
                if view_mode == "active":
                    bc1, bc2, bc3, bc4 = st.columns([1, 1, 1, 1])
                    if can("create_ship", role):
                        add_label = "Добавить авто"
                        if free_pos:
                            add_label += " (поз. " + s(free_pos) + ")"
                        if bc1.button(add_label, key="btn_show_addcar_" + s(trip_id),
                                      use_container_width=True):
                            st.session_state["open_addcar_" + s(trip_id)] = True
                    if can("edit_trip", role):
                        if bc2.button("Рейс ✏", key="btn_show_edittrip_" + s(trip_id),
                                      use_container_width=True):
                            st.session_state["open_edittrip_" + s(trip_id)] = True
                    if can("complete_trip", role):
                        if trip_completed:
                            if bc3.button("↩ Отменить завершение",
                                          key="btn_uncomplete_" + s(trip_id),
                                          use_container_width=True):
                                uncomplete_trip(trip_id)
                                log_action(u["login"], role, "uncomplete_trip",
                                           s(tractor) + " " + s(driver))
                                st.rerun()
                        else:
                            if bc3.button("✅ Завершить рейс",
                                          key="btn_complete_" + s(trip_id),
                                          use_container_width=True):
                                st.session_state["open_complete_" + s(trip_id)] = True
                    if can("archive", role):
                        if bc4.button("📦 В архив", key="btn_arch_trip_" + s(trip_id),
                                      use_container_width=True):
                            archive_trip(trip_id)
                            log_action(u["login"], role, "archive_trip",
                                       s(tractor) + " " + s(driver))
                            st.success("Рейс отправлен в архив")
                            st.rerun()
                elif view_mode == "archive":
                    bc1, bc2 = st.columns([1, 1])
                    if can("archive", role):
                        if bc1.button("♻ Вернуть из архива",
                                      key="btn_unarch_trip_" + s(trip_id),
                                      use_container_width=True):
                            unarchive_trip(trip_id)
                            log_action(u["login"], role, "unarchive_trip",
                                       s(tractor) + " " + s(driver))
                            st.success("Рейс возвращён из архива")
                            st.rerun()
                else:
                    if can("complete_trip", role):
                        if st.button("↩ Вернуть в активные",
                                     key="btn_uncomplete_c_" + s(trip_id),
                                     use_container_width=True):
                            uncomplete_trip(trip_id)
                            log_action(u["login"], role, "uncomplete_trip",
                                       s(tractor) + " " + s(driver))
                            st.rerun()

                if can("delete_trip", role) and view_mode == "active":
                    dc1, dc2 = st.columns([5, 1])
                    with dc2:
                        if st.button("🗑 Удалить рейс",
                                     key="btn_show_deltrip_" + s(trip_id),
                                     use_container_width=True):
                            st.session_state["open_deltrip_" + s(trip_id)] = True

                if st.session_state.get("open_deltrip_" + s(trip_id)):
                    st.warning("Удалить рейс «" + s(tractor) + " — " + s(driver)
                               + "» вместе со всеми авто? Действие необратимо.")
                    cc1, cc2 = st.columns(2)
                    if cc1.button("Да, удалить",
                                  key="btn_yes_del_trip_" + s(trip_id),
                                  use_container_width=True):
                        delete_trip(trip_id)
                        log_action(u["login"], role, "delete_trip",
                                   s(tractor) + " " + s(driver))
                        st.session_state.pop("open_deltrip_" + s(trip_id), None)
                        st.success("Рейс удалён")
                        st.rerun()
                    if cc2.button("Отмена",
                                  key="btn_no_del_trip_" + s(trip_id),
                                  use_container_width=True):
                        st.session_state.pop("open_deltrip_" + s(trip_id), None)
                        st.rerun()

                if cars:
                    if st.button("🖨 Печать списка по рейсу",
                                 key="btn_print_trip_" + s(trip_id),
                                 use_container_width=True):
                        rows = []
                        for x in sorted(cars,
                                        key=lambda z: int(to_float(z.get("position")))):
                            if check_issued(s(x.get("issued", "0"))):
                                continue
                            fin = calculate_financials(x)
                            tr_to_x = s(x.get("transferred_to_trip", ""))
                            tr_from_x = s(x.get("transferred_from_trip", ""))
                            note_x = ""
                            if is_trace_row(x):
                                target_id = find_target_trip_for_parent(
                                    tr_to_x, shipments)
                                note_x = "Перенесён в: " + resolve_target_trip_label(
                                    target_id, trips, transfer_history)
                            elif tr_from_x or tr_to_x:
                                src_lbl = resolve_source_trip_label_for_car(
                                    x, trips, transfer_history)
                                note_x = "Перенесён из: " + src_lbl
                            rows.append({
                                "tractor": tractor,
                                "driver": driver,
                                "route": route,
                                "dep": dep,
                                "position": s(x.get("position", "")),
                                "car_model": s(x.get("car_model", "")),
                                "vin": s(x.get("vin", "")),
                                "client": s(x.get("client", "")),
                                "customer": s(x.get("customer", "")),
                                "delivery_city": s(x.get("delivery_city", "")),
                                "debt": fin["debt"],
                                "note": note_x,
                            })
                        st.session_state["show_print_trip"] = rows
                        st.rerun()

                if st.session_state.get("open_complete_" + s(trip_id)):
                    with st.form("complete_" + s(trip_id)):
                        st.markdown("**Завершение рейса**")
                        default_date = trip_completed_at if trip_completed_at else datetime.now().strftime("%d.%m.%Y")
                        comp_date = st.text_input("Дата завершения (ДД.ММ.ГГГГ)",
                                                   value=default_date)
                        c_ok = st.form_submit_button("Завершить")
                        c_no = st.form_submit_button("Отмена")
                    if c_no:
                        st.session_state.pop("open_complete_" + s(trip_id), None)
                        st.rerun()
                    if c_ok:
                        try:
                            comp_fmt = parse_date_ui(comp_date)
                        except ValueError as ex:
                            st.error(str(ex))
                        else:
                            complete_trip(trip_id, comp_fmt)
                            log_action(u["login"], role, "complete_trip",
                                       s(tractor) + " " + s(driver) + " " + s(comp_fmt))
                            st.session_state.pop("open_complete_" + s(trip_id), None)
                            st.success("Рейс завершён")
                            st.rerun()

                if st.session_state.get("open_edittrip_" + s(trip_id)):
                    with st.form("edit_trip_" + s(trip_id)):
                        st.markdown("**Редактировать рейс**")
                        ec1, ec2 = st.columns(2)
                        e_tractor = ec1.text_input("Гос номер тягача", value=s(tractor))
                        e_driver = ec2.text_input("Водитель ФИО", value=s(driver))
                        ec3, ec4 = st.columns(2)
                        e_route = ec3.text_input("Маршрут", value=s(route))
                        e_dep = ec4.text_input("Дата выезда (ДД.ММ.ГГГГ)", value=s(dep))
                        e_ret = st.text_input("Дата возвращения (можно пусто)", value=s(ret))
                        e_ok = st.form_submit_button("Сохранить рейс")
                        e_cancel = st.form_submit_button("Отмена")
                    if e_cancel:
                        st.session_state.pop("open_edittrip_" + s(trip_id), None)
                        st.rerun()
                    if e_ok:
                        if not e_tractor or not e_driver or not e_dep:
                            st.error("Заполните: гос номер, водителя, дату выезда")
                        else:
                            try:
                                e_dep_fmt = parse_date_ui(e_dep)
                                e_ret_fmt = parse_date_ui(e_ret)
                            except ValueError as ex:
                                st.error(str(ex))
                            else:
                                update_trip(trip_id, e_tractor, e_driver, e_route,
                                            e_dep_fmt, e_ret_fmt)
                                log_action(u["login"], role, "edit_trip",
                                           s(e_tractor) + " " + s(e_driver) + " " + s(e_dep_fmt))
                                st.session_state.pop("open_edittrip_" + s(trip_id), None)
                                st.success("Рейс обновлён")
                                st.rerun()

                if role == "admin" and view_mode == "active":
                    with st.expander("Опасная зона"):
                        st.warning("Рейс будет помечен как удалённый. Данные сохранятся.")
                        if st.button("🗑 Пометить рейс удалённым",
                                     key="btn_softdel_" + s(trip_id)):
                            soft_delete_trip(trip_id)
                            log_action(u["login"], role, "soft_delete_trip",
                                       s(tractor) + " " + s(driver))
                            st.success("Рейс помечен удалённым")
                            st.rerun()

                if view_mode == "active" and st.session_state.get("open_addcar_" + s(trip_id)):
                    f = render_shipment_form("newcar_" + s(trip_id), c=None,
                                             submit_label="Сохранить авто",
                                             default_position=free_pos or 1)
                    if f["cancel"]:
                        st.session_state.pop("open_addcar_" + s(trip_id), None)
                        st.rerun()
                    if f["save"]:
                        if total_in_route >= MAX_CARS:
                            st.error("На автовозе " + s(MAX_CARS)
                                     + " активных авто. Освободите место, сняв статус «Выдан».")
                        elif not f["client"] and not f["customer"]:
                            st.error("Заполните хотя бы одно: ФИО клиента или Заказчик")
                        else:
                            try:
                                dp = parse_date_ui(f["date_pay"])
                                dpa = parse_date_ui(f["advance_date"])
                            except ValueError as e:
                                st.error(str(e))
                            else:
                                amt_val = money_value(f["amount"])
                                adv_val = money_value(f["advance"])
                                if f["payer_type"] == NDS_PAYER:
                                    nds_val = amt_val * NDS_RATE / (1 + NDS_RATE)
                                    no_nds_val = amt_val - nds_val
                                else:
                                    nds_val = 0.0
                                    no_nds_val = amt_val
                                create_shipment(trip_id, f["position"], f["car_model"],
                                                f["client"], amt_val, dp, f["paid_to"],
                                                f["delivery_city"], f["vin"][:17],
                                                adv_val, dpa, f["payer_type"],
                                                f["customer"], f["contract_number"],
                                                nds_val, no_nds_val, u["login"])
                                log_action(u["login"], role, "create_ship",
                                           "рейс " + s(trip_id) + ", поз " + s(f["position"]))
                                st.session_state.pop("open_addcar_" + s(trip_id), None)
                                st.success("Авто добавлено")
                                st.rerun()

                if cars:
                    st.markdown("**Список автомобилей в рейсе**")

                    for x in sorted(cars, key=lambda z: int(to_float(z.get("position")))):
                        f = calculate_financials(x)
                        amount_val = f["amount"]
                        advance_val = f["advance"]
                        debt_val = f["debt"]
                        nds_val = f["nds"]

                        issued_val = s(x.get("issued", "0")).strip()
                        paid_val = s(x.get("paid", "0")).strip()
                        is_paid_flag = check_paid(paid_val)
                        is_issued_flag = check_issued(issued_val)
                        has_debt = debt_val > 0.01

                        tr_to = s(x.get("transferred_to_trip", ""))
                        tr_at = s(x.get("transferred_at", ""))
                        tr_from = s(x.get("transferred_from_trip", ""))
                        is_trace = is_trace_row(x)
                        parent_id = tr_from if is_trace else ""
                        can_cancel_parent = bool(tr_from) and not is_trace

                        src_trip_label = ""
                        if is_transferred_car(x):
                            src_trip_label = resolve_source_trip_label_for_car(
                                x, trips, transfer_history)

                        target_trip_label = ""
                        if tr_to:
                            target_trip_label = resolve_target_trip_label(
                                tr_to, trips, transfer_history)

                        # ---------- Логика цвета и статуса ----------
                        advance_covers = (amount_val > 0.01
                                          and advance_val >= amount_val - 0.01)

                        if is_trace:
                            bg = "#eeeeee"; bd = "#bdbdbd"
                            status_text = "⚪ Перенесён"
                        elif not has_debt and is_issued_flag:
                            bg = "#c8e6c9"; bd = "#4caf50"
                            status_text = "🟢 Выдан"
                        elif not has_debt and is_paid_flag:
                            bg = "#c8e6c9"; bd = "#4caf50"
                            status_text = "🟢 Оплачен"
                        elif not has_debt and advance_covers:
                            bg = "#c8e6c9"; bd = "#4caf50"
                            status_text = "🟢 Аванс закрыт"
                        elif not has_debt:
                            bg = "#c8e6c9"; bd = "#4caf50"
                            status_text = "🟢 Без долга"
                        elif has_debt and is_issued_flag:
                            bg = "#ffcdd2"; bd = "#f44336"
                            status_text = "🔴 Выдан с долгом"
                        else:
                            bg = "#ffcdd2"; bd = "#f44336"
                            status_text = "🔴 Долг"

                        st.markdown(
                            '<div style="background-color:' + bg +
                            '; border:1px solid ' + bd +
                            '; border-radius:6px; padding:8px; margin-bottom:6px; '
                            'font-size:14px; word-wrap:break-word;">',
                            unsafe_allow_html=True)

                        if is_trace:
                            # ---------- TRACE-СТРОКА ----------
                            parent_ship_id = (s(x.get("transferred_to_trip", ""))
                                              or s(x.get("transferred_from_trip", "")))
                            target_trip_id = find_target_trip_for_parent(
                                parent_ship_id, shipments)
                            if target_trip_id:
                                target_label = resolve_target_trip_label(
                                    target_trip_id, trips, transfer_history)
                            else:
                                target_label = "—"

                            st.markdown(
                                "**" + s(x.get("car_model", "")) + "**  \n"
                                "↗ **Перенесён в рейс:** " + target_label
                                + ("  \nДата переноса: **" + s(tr_at) + "**" if tr_at else "")
                            )

                            actual_parent_id = parent_ship_id
                            if not actual_parent_id:
                                actual_parent_id = find_parent_for_trace(x, shipments)
                                if actual_parent_id:
                                    try:
                                        ws_fix = get_ws_cached("shipments")
                                        fix_row = _find_row_index_by_id(
                                            "shipments", s(x["id"]))
                                        if fix_row:
                                            ws_fix.batch_update(
                                                [{"range": "AC" + str(fix_row),
                                                  "values": [[actual_parent_id]]}],
                                                value_input_option="USER_ENTERED")
                                            invalidate_cache("shipments")
                                    except Exception as e_fix:
                                        _log_system_error(
                                            "fix_parent_on_trace", s(x["id"]), e_fix)

                            if view_mode == "active":
                                tc1, tc2 = st.columns(2)

                                if can("cancel_transfer", role):
                                    if actual_parent_id:
                                        if tc1.button("↩ Вернуть авто в рейс",
                                                      key="btn_cancel_trace_" + s(x["id"]),
                                                      use_container_width=True):
                                            ok_flag, err = cancel_transfer(
                                                actual_parent_id, u["login"], role
                                            )
                                            if ok_flag:
                                                log_action(u["login"], role,
                                                           "cancel_transfer",
                                                           "trace " + s(x["id"])
                                                           + " → parent "
                                                           + actual_parent_id)
                                                st.success(
                                                    "Перенос отменён, авто вернулось")
                                            else:
                                                st.error("Не удалось: " + s(err))
                                            st.rerun()
                                    else:
                                        tc1.button("↩ Вернуть авто в рейс",
                                                   key="btn_cancel_trace_na_"
                                                   + s(x["id"]),
                                                   disabled=True,
                                                   use_container_width=True,
                                                   help="Не найден исходный автомобиль.")

                                if can("edit_trace", role):
                                    if actual_parent_id:
                                        if tc2.button("✏ Редактировать перенос",
                                                      key="btn_edit_transfer_"
                                                      + s(x["id"]),
                                                      use_container_width=True):
                                            st.session_state[
                                                "open_edit_transfer_" + s(x["id"])
                                            ] = True
                                    else:
                                        tc2.button("✏ Редактировать перенос",
                                                   key="btn_edit_transfer_na_"
                                                   + s(x["id"]),
                                                   disabled=True,
                                                   use_container_width=True,
                                                   help="Не найден исходный автомобиль.")

                                if actual_parent_id and st.session_state.get(
                                        "open_edit_transfer_" + s(x["id"])):
                                    other_trips = [tt for tt in get_trips()
                                                   if not is_archived(tt.get("archived", "0"))
                                                   and not is_deleted(tt.get("deleted_at", ""))
                                                   and not check_completed(tt.get("completed", "0"))]
                                    if not other_trips:
                                        st.warning("Нет активных рейсов для выбора.")
                                    else:
                                        options = []
                                        for tt in other_trips:
                                            options.append((describe_trip(tt),
                                                            tt.get("id")))
                                        current_target_label = resolve_target_trip_label(
                                            tr_to, trips, transfer_history)
                                        with st.form("edit_transfer_form_"
                                                     + s(x["id"])):
                                            st.markdown(
                                                "**Редактировать перенос вручную**  \n"
                                                "Сейчас авто в рейсе: **"
                                                + current_target_label + "**"
                                            )
                                            labels = [o[0] for o in options]
                                            chosen_label = st.selectbox(
                                                "Новый рейс для авто",
                                                labels,
                                                key="edt_sel_" + s(x["id"]))
                                            chosen_id = next(
                                                o[1] for o in options
                                                if o[0] == chosen_label)
                                            target_cars = [c for c in shipments
                                                           if s(c.get("trip_id"))
                                                           == s(chosen_id)]
                                            suggested_pos = (
                                                next_free_position(target_cars)
                                                or 1)
                                            new_pos = st.number_input(
                                                "Позиция на новом рейсе",
                                                min_value=1,
                                                max_value=MAX_CARS,
                                                value=suggested_pos,
                                                step=1,
                                                key="edt_pos_" + s(x["id"]))
                                            e_ok = st.form_submit_button(
                                                "💾 Сохранить перенос")
                                            e_no = st.form_submit_button("Отмена")
                                        if e_no:
                                            st.session_state.pop(
                                                "open_edit_transfer_"
                                                + s(x["id"]), None)
                                            st.rerun()
                                        if e_ok:
                                            ok_flag, err = reassign_transfer_for_car(
                                                actual_parent_id, chosen_id,
                                                new_pos, u["login"], role)
                                            st.session_state.pop(
                                                "open_edit_transfer_"
                                                + s(x["id"]), None)
                                            if ok_flag:
                                                log_action(
                                                    u["login"], role,
                                                    "edit_transfer_manual",
                                                    "shipment "
                                                    + actual_parent_id
                                                    + " → trip " + s(chosen_id))
                                                st.success(
                                                    "Перенос обновлён: авто "
                                                    "теперь в выбранном рейсе.")
                                            else:
                                                st.error("Не удалось: " + s(err))
                                            st.rerun()
                        else:
                            # ---------- ОБЫЧНОЕ АВТО ----------
                            client_display = s(x.get("client", "")) or s(x.get("customer", ""))
                            st.markdown(
                                "**Поз. " + s(x.get("position", "")) + "** · "
                                + s(x.get("car_model", "")) + " · "
                                + client_display + " · VIN: "
                                + (s(x.get("vin", ""))[:17] or "—") + "  \n"
                                "Сумма: **" + fmt_money(amount_val) + "** | "
                                "Аванс: **" + fmt_money(advance_val) + "** | "
                                "Задолж.: **" + fmt_money(debt_val) + "**" +
                                (" | НДС: " + fmt_money(nds_val) if nds_val > 0 else "") +
                                "  \nСтатус: **" + status_text + "**"
                            )
                            if is_transferred_car(x):
                                st.markdown(
                                    '<div style="background:#ede7f6; '
                                    'border-left:4px solid #5e35b1; '
                                    'padding:6px 10px; margin-top:6px; '
                                    'border-radius:4px; font-size:13px; '
                                    'word-wrap:break-word;">'
                                    '↩ <b>Перенесён из рейса:</b> '
                                    + src_trip_label +
                                    '</div>',
                                    unsafe_allow_html=True,
                                )
                            details = []
                            if s(x.get("delivery_city", "")):
                                details.append("Город: " + s(x.get("delivery_city", "")))
                            if s(x.get("advance_date", "")):
                                details.append("Дата аванса: " + date_to_display_safe(x.get("advance_date", "")))
                            if s(x.get("payer_type", "")):
                                details.append("Оплата: " + s(x.get("payer_type", "")))
                            if s(x.get("paid_to", "")):
                                details.append("Кому: " + s(x.get("paid_to", "")))
                            if s(x.get("contract_number", "")):
                                details.append("№ договора: " + s(x.get("contract_number", "")))
                            if details:
                                st.markdown(
                                    '<div style="font-weight:bold; font-size:14px; '
                                    'margin-top:4px; color:#222; word-wrap:break-word;">'
                                    + " · ".join(details) +
                                    '</div>',
                                    unsafe_allow_html=True,
                                )

                        # ---------- КНОПКИ В АКТИВНОМ РЕЙСЕ ----------
                        if view_mode == "active" and not is_trace:
                            bc1, bc2, bc3, bc4 = st.columns(4)
                            paid_label = "❌ Снять оплату" if is_paid_flag else "✅ Оплачен"
                            if bc1.button(paid_label, key="btn_paid_" + s(x["id"]),
                                          use_container_width=True):
                                toggle_paid(x["id"], paid_val, amount_val, advance_val)
                                log_action(u["login"], role, "toggle_paid",
                                           "рейс " + s(trip_id) + ", поз " + s(x.get("position")))
                                st.rerun()

                            if is_issued_flag:
                                if bc2.button("↩ Снять выдан", key="btn_issued_" + s(x["id"]),
                                              use_container_width=True):
                                    toggle_issued(x["id"], issued_val, "0")
                                    log_action(u["login"], role, "toggle_issued",
                                               "рейс " + s(trip_id) + ", поз " + s(x.get("position")))
                                    st.rerun()
                            else:
                                if bc2.button("✅ Выдан", key="btn_issued_" + s(x["id"]),
                                              use_container_width=True):
                                    toggle_issued(x["id"], issued_val, "0")
                                    log_action(u["login"], role, "toggle_issued",
                                               "рейс " + s(trip_id) + ", поз " + s(x.get("position")))
                                    st.rerun()

                            if bc3.button("📄 Акт", key="btn_act_inline_" + s(x["id"]),
                                          use_container_width=True):
                                st.session_state["show_act_for"] = x["id"]
                                st.rerun()

                            if bc4.button("✏ Изменить", key="btn_edit_" + s(x["id"]),
                                          use_container_width=True):
                                st.session_state["edit_ship_" + s(x["id"])] = True

                            tc1, tc2, tc3, tc4 = st.columns(4)
                            if can("transfer_ship", role):
                                if tc1.button("📦 Перенести",
                                              key="btn_transfer_" + s(x["id"]),
                                              use_container_width=True):
                                    st.session_state["open_transfer_" + s(x["id"])] = True
                            if can("cancel_transfer", role) and can_cancel_parent:
                                if tc2.button("↩ Отменить перенос",
                                              key="btn_cancel_transfer_" + s(x["id"]),
                                              use_container_width=True):
                                    st.session_state["open_cancel_transfer_" + s(x["id"])] = True
                            if can("edit_trace", role) and tr_to:
                                if tc2.button("✏ Переназначить",
                                              key="btn_reassign_" + s(x["id"]),
                                              use_container_width=True):
                                    st.session_state["open_reassign_" + s(x["id"])] = True
                            if tc3.button("📜 История",
                                          key="btn_hist_" + s(x["id"]),
                                          use_container_width=True):
                                st.session_state["show_history_for"] = x["id"]
                                st.rerun()
                            if can("delete_ship", role):
                                if tc4.button("🗑 Удалить авто", key="btn_delship_" + s(x["id"]),
                                              use_container_width=True):
                                    delete_shipment(x["id"])
                                    log_action(u["login"], role, "delete_ship",
                                               "рейс " + s(trip_id) + ", поз " + s(x.get("position")))
                                    st.rerun()

                        # ---------- КНОПКИ В ЗАВЕРШЁННОМ РЕЙСЕ ----------
                        elif view_mode == "completed" and not is_trace:
                            bc1, bc2 = st.columns(2)
                            paid_label = "❌ Снять оплату" if is_paid_flag else "✅ Оплачен"
                            if bc1.button(paid_label,
                                          key="btn_paid_c_" + s(x["id"]),
                                          use_container_width=True):
                                toggle_paid(x["id"], paid_val, amount_val, advance_val)
                                log_action(u["login"], role, "toggle_paid",
                                           "рейс " + s(trip_id) + ", поз " + s(x.get("position")))
                                st.rerun()
                            if is_issued_flag:
                                if bc2.button("↩ Снять выдан",
                                              key="btn_issued_c_" + s(x["id"]),
                                              use_container_width=True):
                                    toggle_issued(x["id"], issued_val, "0")
                                    log_action(u["login"], role, "toggle_issued",
                                               "рейс " + s(trip_id) + ", поз " + s(x.get("position")))
                                    st.rerun()
                            else:
                                if bc2.button("✅ Выдан",
                                              key="btn_issued_c_" + s(x["id"]),
                                              use_container_width=True):
                                    toggle_issued(x["id"], issued_val, "0")
                                    log_action(u["login"], role, "toggle_issued",
                                               "рейс " + s(trip_id) + ", поз " + s(x.get("position")))
                                    st.rerun()

                            bc3, bc4, bc5 = st.columns(3)
                            if bc3.button("📄 Акт", key="btn_act_inline_" + s(x["id"]),
                                          use_container_width=True):
                                st.session_state["show_act_for"] = x["id"]
                                st.rerun()
                            if bc4.button("📜 История",
                                          key="btn_hist_" + s(x["id"]),
                                          use_container_width=True):
                                st.session_state["show_history_for"] = x["id"]
                                st.rerun()
                            if can("delete_ship", role):
                                if bc5.button("🗑 Удалить авто",
                                              key="btn_delship_" + s(x["id"]),
                                              use_container_width=True):
                                    delete_shipment(x["id"])
                                    log_action(u["login"], role, "delete_ship",
                                               "рейс " + s(trip_id) + ", поз " + s(x.get("position")))
                                    st.rerun()

                        st.markdown("</div>", unsafe_allow_html=True)

                        # ---- Переназначение переноса ----
                        if view_mode == "active" and not is_trace and tr_to and \
                                st.session_state.get("open_reassign_" + s(x["id"])):
                            other_trips = [tt for tt in get_trips()
                                           if not is_archived(tt.get("archived", "0"))
                                           and not is_deleted(tt.get("deleted_at", ""))
                                           and not check_completed(tt.get("completed", "0"))
                                           and s(tt.get("id")) != s(tr_to)]
                            if not other_trips:
                                st.warning("Нет других активных рейсов для переназначения.")
                            else:
                                options = []
                                for tt in other_trips:
                                    options.append((describe_trip(tt), tt.get("id")))
                                current_label = resolve_target_trip_label(
                                    tr_to, trips, transfer_history)
                                with st.form("reassign_form_" + s(x["id"])):
                                    st.markdown("**Переназначить перенос авто**  \n"
                                                "Сейчас: " + current_label)
                                    labels = [o[0] for o in options]
                                    chosen_label = st.selectbox("Новый рейс",
                                                                 labels,
                                                                 key="reas_sel_" + s(x["id"]))
                                    target_cars = [c for c in shipments
                                                   if s(c.get("trip_id")) == s(
                                                       next(o[1] for o in options if o[0] == chosen_label))]
                                    suggested_pos = next_free_position(target_cars) or 1
                                    new_pos = st.number_input("Позиция на новом рейсе",
                                                                min_value=1, max_value=MAX_CARS,
                                                                value=suggested_pos,
                                                                step=1,
                                                                key="reas_pos_" + s(x["id"]))
                                    r_ok = st.form_submit_button("Переназначить")
                                    r_no = st.form_submit_button("Отмена")
                                if r_no:
                                    st.session_state.pop("open_reassign_" + s(x["id"]), None)
                                    st.rerun()
                                if r_ok:
                                    chosen_id = next(o[1] for o in options
                                                     if o[0] == chosen_label)
                                    ok_flag, err = reassign_transfer_for_car(
                                        x["id"], chosen_id, new_pos, u["login"], role
                                    )
                                    st.session_state.pop("open_reassign_" + s(x["id"]), None)
                                    if ok_flag:
                                        log_action(u["login"], role, "reassign_transfer",
                                                   "shipment " + s(x["id"])
                                                   + " → trip " + s(chosen_id))
                                        st.success("Перенос переназначен")
                                    else:
                                        st.error("Не удалось: " + s(err))
                                    st.rerun()

                        # ---- Перенос в другой рейс ----
                        if view_mode == "active" and not is_trace and \
                                st.session_state.get("open_transfer_" + s(x["id"])):
                            other_trips = [tt for tt in get_trips()
                                           if not is_archived(tt.get("archived", "0"))
                                           and not is_deleted(tt.get("deleted_at", ""))
                                           and not check_completed(tt.get("completed", "0"))
                                           and s(tt.get("id")) != s(trip_id)]
                            if not other_trips:
                                st.warning("Нет других активных рейсов для переноса. "
                                           "Завершённые и архивные рейсы недоступны.")
                            else:
                                options = []
                                for tt in other_trips:
                                    options.append((describe_trip(tt), tt.get("id")))
                                with st.form("transfer_form_" + s(x["id"])):
                                    st.markdown("**Перенос авто на другой рейс**")
                                    labels = [o[0] for o in options]
                                    chosen_label = st.selectbox("Выберите рейс",
                                                                 labels,
                                                                 key="sel_" + s(x["id"]))
                                    target_cars = [c for c in shipments
                                                   if s(c.get("trip_id")) == s(
                                                       next(o[1] for o in options if o[0] == chosen_label))]
                                    suggested_pos = next_free_position(target_cars) or 1
                                    new_pos = st.number_input("Позиция на новом рейсе",
                                                                min_value=1, max_value=MAX_CARS,
                                                                value=suggested_pos,
                                                                step=1,
                                                                key="pos_" + s(x["id"]))
                                    tr_ok = st.form_submit_button("Перенести")
                                    tr_no = st.form_submit_button("Отмена")
                                if tr_no:
                                    st.session_state.pop("open_transfer_" + s(x["id"]), None)
                                    st.rerun()
                                if tr_ok:
                                    chosen_id = next(o[1] for o in options if o[0] == chosen_label)
                                    transfer_shipment_to_trip(x["id"], chosen_id, new_pos,
                                                              u["login"])
                                    log_action(u["login"], role, "transfer_ship",
                                               "shipment " + s(x["id"]) + " → trip " + s(chosen_id))
                                    st.session_state.pop("open_transfer_" + s(x["id"]), None)
                                    st.success("Авто перенесено")
                                    st.rerun()

                        # ---- Отмена переноса ----
                        if view_mode == "active" and not is_trace and can_cancel_parent and \
                                st.session_state.get("open_cancel_transfer_" + s(x["id"])):
                            src_label = resolve_target_trip_label(
                                tr_from, trips, transfer_history)
                            st.warning(
                                "Отменить перенос авто обратно на рейс: **"
                                + src_label + "**?"
                            )
                            cc1, cc2 = st.columns(2)
                            if cc1.button("↩ Да, вернуть",
                                          key="ok_cancel_transfer_" + s(x["id"]),
                                          use_container_width=True):
                                ok_flag, err = cancel_transfer(
                                    x["id"], u["login"], role
                                )
                                st.session_state.pop("open_cancel_transfer_" + s(x["id"]), None)
                                if ok_flag:
                                    log_action(u["login"], role, "cancel_transfer",
                                               "shipment " + s(x["id"])
                                               + " ← trip " + s(tr_from))
                                    st.success("Перенос отменён")
                                else:
                                    st.error("Не удалось отменить: " + s(err))
                                st.rerun()
                            if cc2.button("Отмена",
                                          key="no_cancel_transfer_" + s(x["id"]),
                                          use_container_width=True):
                                st.session_state.pop("open_cancel_transfer_" + s(x["id"]), None)
                                st.rerun()

                    st.markdown("---")
                    sum_cols = st.columns(4)
                    sum_cols[0].markdown("**Сумма:** " + fmt_money(total))
                    sum_cols[1].markdown("**Аванс:** " + fmt_money(total_advance))
                    sum_cols[2].markdown("**Задолженность:** " + fmt_money(total_debt))
                    sum_cols[3].markdown("**Авто:** " + s(total_in_route) + "/" + s(MAX_CARS))
                    if total_nds > 0:
                        st.markdown("**НДС 22%:** " + fmt_money(total_nds))

                    # ---- Авто, перенесённые в этот рейс ----
                    incoming_cars = [x for x in cars if is_transferred_car(x)]
                    if incoming_cars:
                        st.markdown(
                            '<div style="background:#ede7f6; '
                            'border-left:4px solid #5e35b1; '
                            'padding:8px 12px; margin-top:8px; '
                            'border-radius:4px; font-size:14px;">'
                            '<b>↘ Перенесено в этот рейс: '
                            + s(len(incoming_cars)) + ' авто</b>'
                            '</div>',
                            unsafe_allow_html=True,
                        )
                        for x in incoming_cars:
                            src_label = resolve_source_trip_label_for_car(
                                x, trips, transfer_history)
                            st.markdown(
                                "- " + s(x.get("car_model", ""))
                                + " · VIN: " + (s(x.get("vin", ""))[:17] or "—")
                                + "  \n  перенесён из рейса: **" + src_label + "**"
                            )

                    for x in sorted(cars, key=lambda z: int(to_float(z.get("position")))):
                        if st.session_state.get("edit_ship_" + s(x["id"])):
                            st.markdown("**Редактировать авто (позиция " + s(x.get("position")) + ")**")
                            fedit = render_shipment_form("editcar_" + s(x["id"]), c=x,
                                                         submit_label="Сохранить изменения")
                            if fedit["cancel"]:
                                st.session_state.pop("edit_ship_" + s(x["id"]), None)
                                st.rerun()
                            if fedit["save"]:
                                if not fedit["client"] and not fedit["customer"]:
                                    st.error("Заполните хотя бы одно: ФИО клиента или Заказчик")
                                else:
                                    try:
                                        e_dp = parse_date_ui(fedit["date_pay"])
                                        e_dpa = parse_date_ui(fedit["advance_date"])
                                    except ValueError as ex:
                                        st.error(str(ex))
                                    else:
                                        amt_val = money_value(fedit["amount"])
                                        adv_val = money_value(fedit["advance"])
                                        if fedit["payer_type"] == NDS_PAYER:
                                            nds_val = amt_val * NDS_RATE / (1 + NDS_RATE)
                                            no_nds_val = amt_val - nds_val
                                        else:
                                            nds_val = 0.0
                                            no_nds_val = amt_val
                                        update_shipment(x["id"], fedit["position"], fedit["car_model"],
                                                        fedit["client"], amt_val, e_dp, fedit["paid_to"],
                                                        fedit["delivery_city"], fedit["vin"][:17],
                                                        adv_val, e_dpa, fedit["payer_type"],
                                                        fedit["customer"], fedit["contract_number"],
                                                        nds_val, no_nds_val)
                                        log_action(u["login"], role, "edit_ship",
                                                   "рейс " + s(trip_id) + ", поз " + s(fedit["position"]))
                                        st.session_state.pop("edit_ship_" + s(x["id"]), None)
                                        st.success("Изменения сохранены")
                                        st.rerun()

                    if view_mode == "active" and can("create_ship", role) and total_in_route < MAX_CARS:
                        free_pos_btn = next_free_position(cars) or 1
                        if st.button("Добавить еще авто (позиция " + s(free_pos_btn) + ")",
                                     key="btn_more_addcar_" + s(trip_id),
                                     use_container_width=True):
                            st.session_state["open_addcar_" + s(trip_id)] = True
                            st.rerun()
                else:
                    st.info("В этом рейсе ещё нет авто.")

    # ============================================================
    # ИТОГИ ПО АКТИВНЫМ
    # ============================================================
    st.markdown("---")

    active_trip_ids = {s(t.get("id")) for t in trips
                       if not is_archived(t.get("archived", "0"))
                       and not is_deleted(t.get("deleted_at", ""))
                       and not check_completed(t.get("completed", "0"))}

    all_active_cars = [x for x in shipments
                       if s(x.get("trip_id")) in active_trip_ids]

    grand_total = 0.0
    grand_advance = 0.0
    grand_debt = 0.0
    grand_nds = 0.0
    for x in all_active_cars:
        if check_issued(s(x.get("issued", "0"))):
            continue
        f = calculate_financials(x)
        grand_total += f["amount"]
        grand_advance += f["advance"]
        grand_debt += f["debt"]
        grand_nds += f["nds"]

    spacer, totals = st.columns([1, 2])
    with totals:
        st.markdown(
            '<div style="background:#f5f5f5; border:1px solid #cccccc; '
            'border-radius:8px; padding:14px 18px; font-size:14px;">'
            '<div style="font-size:18px; font-weight:bold; margin-bottom:8px;">'
            'Итого (активные, без завершённых)</div>'
            '<div style="display:flex; justify-content:space-between; margin:4px 0; flex-wrap:wrap;">'
            '<span>Активных рейсов:</span><b>' + s(len(active_trip_ids)) + '</b></div>'
            '<div style="display:flex; justify-content:space-between; margin:4px 0; flex-wrap:wrap;">'
            '<span>Авто:</span><b>' + s(len(all_active_cars)) + '</b></div>'
            '<div style="display:flex; justify-content:space-between; margin:4px 0; flex-wrap:wrap;">'
            '<span>Общая сумма:</span><b>' + fmt_money(grand_total) + ' ₽</b></div>'
            '<div style="display:flex; justify-content:space-between; margin:4px 0; flex-wrap:wrap;">'
            '<span>Общая оплата:</span><b>' + fmt_money(grand_advance) + ' ₽</b></div>'
            '<div style="display:flex; justify-content:space-between; margin:4px 0; '
            'border-top:1px solid #cccccc; padding-top:6px; flex-wrap:wrap;">'
            '<span>Общая задолженность:</span>'
            '<b style="color:' + ("#c62828" if grand_debt > 0.01 else "#2e7d32") + ';">'
            + fmt_money(grand_debt) + ' ₽</b></div>'
            + ('<div style="display:flex; justify-content:space-between; margin:4px 0; flex-wrap:wrap;">'
               '<span>НДС 22%:</span><b>' + fmt_money(grand_nds) + ' ₽</b></div>'
               if grand_nds > 0 else '')
            + '</div>',
            unsafe_allow_html=True,
        )


# ============================================================
# ТОЧКА ВХОДА
# ============================================================

def main():
    st.set_page_config(page_title="Учёт рейсов", page_icon="🚛", layout="wide")

    if not st.session_state.get("sheets_ready"):
        try:
            ensure_sheets_once()
            st.session_state["sheets_ready"] = True
        except Exception as e:
            st.error("Ошибка подключения к Google Sheets: "
                     + type(e).__name__ + ": " + s(e))
            st.stop()

    ensure_first_admin()
    if "user" not in st.session_state:
        login_page()
    else:
        main_page()


if __name__ == "__main__":
    main()

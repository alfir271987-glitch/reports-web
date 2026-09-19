import streamlit as st
import gspread
from oauth2client.service_account import ServiceAccountCredentials
from datetime import datetime
import hashlib
import os

SHEET_ID = "1MHslz3VRowoOLtS_AAQ5h-tFm-fIXBN8noqtyQpmKLw"
CRED_FILE = "gifted-mountain-508410-s3-96f38b5a0f63.json"

SCOPES = [
    "https://spreadsheets.google.com/feeds",
    "https://www.googleapis.com/auth/drive",
]

SHEET_SCHEMAS = {
    "users": ["login", "password_hash", "role", "full_name", "active"],
    "trips": ["id", "tractor_number", "driver", "route",
              "date_departure", "date_return", "created_by", "created_at",
              "archived"],
    "shipments": ["id", "trip_id", "position", "car_model", "client",
                  "amount", "date_pay", "paid_to",
                  "delivery_city", "vin",
                  "advance", "advance_date",
                  "payer_type", "customer", "contract_number",
                  "nds_amount", "amount_no_nds",
                  "created_by", "created_at", "issued", "issued_ever",
                  "paid", "archived", "advance_before_paid"],
    "audit_log": ["id", "ts", "login", "role", "action", "details"],
    "act_log": ["id", "ts", "login", "trip_id", "shipment_id", "client"],
}

ROLES = ["admin", "director", "logist", "dispatcher"]
PAYER_TYPES = ["нал", "эквайринг", "безнал с НДС 22%"]
NDS_RATE = 0.22


def s(x):
    if x is None:
        return ""
    return str(x)


def calc_nds(amount, payer_type):
    amount = to_float(amount)
    if payer_type == "безнал с НДС 22%":
        nds = amount * NDS_RATE / (1 + NDS_RATE)
        amount_no_nds = amount - nds
        return nds, amount_no_nds
    return 0.0, amount


def fmt_money(x):
    v = to_float(x)
    ss = "{:,.2f}".format(v)
    ss = ss.replace(",", " ")
    ss = ss.replace(".", ",")
    return ss


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


@st.cache_data(ttl=900, show_spinner=False)
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


@st.cache_resource
def ensure_sheets_once():
    """Создаёт отсутствующие листы и дописывает недостающие заголовки
    в существующие (например, advance_before_paid в shipments)."""
    book = get_book()
    existing = {ws.title for ws in book.worksheets()}
    for name, headers in SHEET_SCHEMAS.items():
        if name not in existing:
            ws = book.add_worksheet(title=name, rows=1000, cols=len(headers))
            ws.append_row(headers)
        else:
            ws = book.worksheet(name)
            try:
                current = ws.row_values(1)
            except Exception:
                current = []
            missing = [h for h in headers if h not in current]
            if missing:
                needed_cols = max(len(headers), len(current) + len(missing))
                if ws.col_count < needed_cols:
                    try:
                        ws.add_cols(needed_cols - ws.col_count)
                    except Exception:
                        pass
                start_col = len(current) + 1
                for idx, h in enumerate(missing):
                    try:
                        ws.update_cell(1, start_col + idx, h)
                    except Exception:
                        pass
    return True


def append_row(name, row_dict):
    ws = get_ws_cached(name)
    headers = SHEET_SCHEMAS[name]
    row = [row_dict.get(h, "") for h in headers]
    ws.append_row(row)
    invalidate_cache(name)


def next_id(name):
    rows = read_all_cached(name)
    return len(rows) + 2


def hash_password(p):
    return hashlib.sha256(p.encode("utf-8")).hexdigest()


def log_action(login, role, action, details=""):
    ws = get_ws_cached("audit_log")
    row = [
        str(int(datetime.now().timestamp())),
        datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        s(login), s(role), s(action), s(details),
    ]
    ws.append_row(row)
    invalidate_cache("audit_log")


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


def is_active(u):
    return s(u.get("active")).strip().lower() in ("1", "1.0", "true")


def is_archived(v):
    return s(v).strip().lower() in ("1", "1.0", "true")


def check_paid(v):
    return s(v).strip().lower() in ("1", "1.0", "true", "да", "yes")


def check_issued(v):
    return s(v).strip().lower() in ("1", "1.0", "true", "да", "yes")


def check_issued_ever(v):
    return s(v).strip().lower() in ("1", "1.0", "true", "да", "yes")


def safe_df(rows):
    if not rows:
        return rows
    return [{k: ("" if v is None else str(v)) for k, v in r.items()} for r in rows]


def ensure_first_admin():
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
                })
                st.success("Админ " + login + " создан. Войдите.")
                st.rerun()
        st.stop()


def can(action, role):
    rights = {
        "admin":      {"create_trip", "edit_trip", "delete_trip",
                       "create_ship", "edit_ship", "delete_ship",
                       "export", "print_act", "manage_users", "view_log",
                       "archive"},
        "director":   {"create_trip", "edit_trip", "delete_trip",
                       "create_ship", "edit_ship", "delete_ship",
                       "export", "print_act", "archive"},
        "logist":     {"create_trip", "edit_trip", "create_ship", "edit_ship",
                       "export", "print_act", "archive"},
        "dispatcher": {"create_trip", "edit_trip", "create_ship", "edit_ship",
                       "export", "print_act", "archive"},
    }
    return action in rights.get(role, set())


def login_page():
    st.title("Учёт рейсов — вход")
    with st.form("login"):
        login = st.text_input("Логин")
        pwd = st.text_input("Пароль", type="password")
        ok = st.form_submit_button("Войти")
    if ok:
        users = read_all("users")
        u = next((x for x in users if s(x.get("login")) == login), None)
        if not u:
            st.error("Пользователь не найден")
            log_action(login, "-", "login_fail", "не найден")
            return
        if not is_active(u):
            st.error("Учётная запись отключена")
            log_action(login, u.get("role", "-"), "login_fail", "отключён")
            return
        if u.get("password_hash") != hash_password(pwd):
            st.error("Неверный пароль")
            log_action(login, u.get("role", "-"), "login_fail", "неверный пароль")
            return
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


def admin_panel():
    st.header("Админ-панель")
    tab1, tab2, tab3 = st.tabs(["Пользователи", "Журнал действий", "Журнал печати актов"])
    with tab1:
        st.subheader("Пользователи")
        users = read_all("users")
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
            elif any(x["login"] == new_login for x in users):
                st.error("Такой логин уже есть")
            else:
                append_row("users", {
                    "login": new_login, "password_hash": hash_password(new_pwd),
                    "role": new_role, "full_name": new_name, "active": "1",
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
            st.info("Пока пусто")


def get_trips(fresh=False):
    return read_all("trips", fresh=fresh)


def get_shipments(fresh=False):
    return read_all("shipments", fresh=fresh)


def create_trip(tractor, driver, route, dep, ret, created_by):
    append_row("trips", {
        "id": next_id("trips"),
        "tractor_number": s(tractor), "driver": s(driver), "route": s(route),
        "date_departure": s(dep), "date_return": s(ret),
        "created_by": s(created_by),
        "created_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "archived": "0",
    })


def update_trip(trip_id, tractor, driver, route, dep, ret):
    ws = get_ws_cached("trips")
    all_rows = ws.get_all_values()
    headers = SHEET_SCHEMAS["trips"]
    last_col = chr(64 + len(headers))
    for i, row in enumerate(all_rows[1:], start=2):
        if s(row[0]) == s(trip_id):
            cb = row[6] if len(row) > 6 else ""
            ca = row[7] if len(row) > 7 else ""
            arch = row[8] if len(row) > 8 else "0"
            new_row = [trip_id, s(tractor), s(driver), s(route), s(dep), s(ret),
                       cb, ca, arch]
            ws.update("A" + str(i) + ":" + last_col + str(i), [new_row])
            invalidate_cache("trips")
            return True
    return False


def archive_trip(trip_id):
    ws = get_ws_cached("trips")
    all_rows = ws.get_all_values()
    headers = SHEET_SCHEMAS["trips"]
    last_col = chr(64 + len(headers))
    for i, row in enumerate(all_rows[1:], start=2):
        if s(row[0]) == s(trip_id):
            cb = row[6] if len(row) > 6 else ""
            ca = row[7] if len(row) > 7 else ""
            new_row = [trip_id, row[1], row[2], row[3], row[4], row[5], cb, ca, "1"]
            ws.update("A" + str(i) + ":" + last_col + str(i), [new_row])
            invalidate_cache("trips")
            return True
    return False


def unarchive_trip(trip_id):
    ws = get_ws_cached("trips")
    all_rows = ws.get_all_values()
    headers = SHEET_SCHEMAS["trips"]
    last_col = chr(64 + len(headers))
    for i, row in enumerate(all_rows[1:], start=2):
        if s(row[0]) == s(trip_id):
            cb = row[6] if len(row) > 6 else ""
            ca = row[7] if len(row) > 7 else ""
            new_row = [trip_id, row[1], row[2], row[3], row[4], row[5], cb, ca, "0"]
            ws.update("A" + str(i) + ":" + last_col + str(i), [new_row])
            invalidate_cache("trips")
            return True
    return False


def delete_trip(trip_id):
    ws = get_ws_cached("trips")
    for i, row in enumerate(ws.get_all_values()[1:], start=2):
        if s(row[0]) == s(trip_id):
            ws.delete_rows(i)
            invalidate_cache("trips")
            break
    ws2 = get_ws_cached("shipments")
    to_del = [i for i, row in enumerate(ws2.get_all_values()[1:], start=2)
              if s(row[1]) == s(trip_id)]
    for i in reversed(to_del):
        ws2.delete_rows(i)
    invalidate_cache("shipments")


def create_shipment(trip_id, position, car_model, client, amount,
                    date_pay, paid_to, delivery_city, vin,
                    advance, advance_date,
                    payer_type, customer, contract_number,
                    nds_amount, amount_no_nds, created_by):
    append_row("shipments", {
        "id": next_id("shipments"),
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
        "advance_before_paid": "",
    })


def update_shipment(shipment_id, position, car_model, client, amount,
                    date_pay, paid_to, delivery_city, vin, advance, advance_date,
                    payer_type, customer, contract_number,
                    nds_amount, amount_no_nds):
    ws = get_ws_cached("shipments")
    all_rows = ws.get_all_values()
    headers = SHEET_SCHEMAS["shipments"]
    last_col = chr(64 + len(headers))
    for i, row in enumerate(all_rows[1:], start=2):
        if s(row[0]) == s(shipment_id):
            trip_id = row[1] if len(row) > 1 else ""
            created_by = row[17] if len(row) > 17 else ""
            created_at = row[18] if len(row) > 18 else ""
            issued_val = row[19] if len(row) > 19 else "0"
            issued_ever_val = row[20] if len(row) > 20 else "0"
            paid_val = row[21] if len(row) > 21 else "0"
            archived_val = row[22] if len(row) > 22 else "0"
            adv_before_paid_val = row[23] if len(row) > 23 else ""
            new_row = [
                shipment_id, trip_id, position, s(car_model), s(client),
                money_value(amount), s(date_pay), s(paid_to),
                s(delivery_city), s(vin),
                money_value(advance), s(advance_date),
                s(payer_type), s(customer), s(contract_number),
                money_value(nds_amount), money_value(amount_no_nds),
                created_by, created_at, issued_val, issued_ever_val,
                paid_val, archived_val, adv_before_paid_val,
            ]
            ws.update("A" + str(i) + ":" + last_col + str(i), [new_row])
            invalidate_cache("shipments")
            return True
    return False


def toggle_issued(shipment_id, current_value, current_ever):
    """Ставит/снимает issued (T=20).
    При установке: issued=1, issued_ever=1.
    При снятии: issued=0, paid=0 (авто уходит в статус «Долг»)."""
    ws = get_ws_cached("shipments")
    for i, row in enumerate(ws.get_all_values()[1:], start=2):
        if s(row[0]) == s(shipment_id):
            was_issued = s(current_value) == "1"
            if not was_issued:
                ws.update_cell(i, 20, "1")
                ws.update_cell(i, 21, "1")   # issued_ever = 1
            else:
                ws.update_cell(i, 20, "0")
                ws.update_cell(i, 22, "0")   # paid = 0
            invalidate_cache("shipments")
            return "0" if was_issued else "1"
    return current_value


def toggle_paid(shipment_id, current_paid, current_amount, current_advance):
    """Ставит/снимает paid (V=22).
    При установке: advance := amount (задолженность = 0),
                   advance_before_paid := прежний аванс.
    При снятии: advance := advance_before_paid (задолженность возвращается)."""
    ws = get_ws_cached("shipments")
    try:
        if ws.col_count < 24:
            ws.add_cols(24 - ws.col_count)
            ws.update_cell(1, 24, "advance_before_paid")
    except Exception:
        pass

    for i, row in enumerate(ws.get_all_values()[1:], start=2):
        if s(row[0]) == s(shipment_id):
            was_paid = s(current_paid) == "1"
            amount_val = money_value(current_amount)
            advance_val = money_value(current_advance)
            prev_adv = row[23] if len(row) > 23 else ""

            if not was_paid:
                try:
                    ws.update_cell(i, 24, money_value(advance_val))
                except Exception:
                    pass
                ws.update_cell(i, 11, amount_val)
                ws.update_cell(i, 22, "1")
            else:
                restore = money_value(prev_adv) if s(prev_adv).strip() != "" else 0.0
                ws.update_cell(i, 11, restore)
                ws.update_cell(i, 22, "0")
                try:
                    ws.update_cell(i, 24, "")
                except Exception:
                    pass
            invalidate_cache("shipments")
            return "1" if not was_paid else "0"
    return current_paid


def delete_shipment(shipment_id):
    ws = get_ws_cached("shipments")
    for i, row in enumerate(ws.get_all_values()[1:], start=2):
        if s(row[0]) == s(shipment_id):
            ws.delete_rows(i)
            invalidate_cache("shipments")
            return


def log_act_print(login, trip_id, shipment_id, client):
    ws = get_ws_cached("act_log")
    row = [
        str(int(datetime.now().timestamp())),
        datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        s(login), s(trip_id), s(shipment_id), s(client),
    ]
    ws.append_row(row)
    invalidate_cache("act_log")


# ============================================================
# АКТ
# ============================================================

def render_act_html(trip, shipment):
    route = s(trip.get("route", ""))
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
    route = s(trip.get("route", ""))
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
        "При подписании акта приема-передачи на момент вручения груза Стороны каких-либо претензий друг к другу не имеют.",
    ]
    return "\n".join(lines)


def show_act(trip, shipment):
    u = st.session_state["user"]
    log_act_print(u["login"], trip.get("id"), shipment.get("id"), shipment.get("client"))
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
# ФОРМА АВТО
# ============================================================

def render_shipment_form(form_key, c=None, submit_label="Сохранить авто"):
    defaults = {
        "position": 1, "car_model": "", "client": "", "vin": "",
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
        position = col1.number_input("Позиция (1–8)", min_value=1, max_value=8, step=1,
                                     value=defaults["position"], key=form_key + "_pos")
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


def main_page():
    u = st.session_state["user"]
    role = u["role"]

    st.sidebar.markdown("**" + s(u["full_name"]) + "**  \nроль: `" + s(role) + "`")
    if st.sidebar.button("Выйти", key="btn_logout"):
        logout()

    if role == "admin":
        with st.sidebar.expander("Админ-панель"):
            admin_panel()

    view_mode = st.session_state.get("view_mode", "active")
    colA, colB, colC = st.columns([2, 1, 1])
    with colB:
        if st.button("Активные", key="btn_view_active", use_container_width=True):
            st.session_state["view_mode"] = "active"
            st.rerun()
    with colC:
        if st.button("Архив", key="btn_view_archive", use_container_width=True):
            st.session_state["view_mode"] = "archive"
            st.rerun()

    st.title("Учёт рейсов и перевозок")

    if st.session_state.get("show_act_for"):
        sid = st.session_state["show_act_for"]
        shipments = get_shipments(fresh=True)
        trips = get_trips(fresh=True)
        ship = next((x for x in shipments if s(x.get("id")) == s(sid)), None)
        if ship:
            trip = next((x for x in trips if s(x.get("id")) == s(ship.get("trip_id"))), None)
            if trip:
                if st.button("Назад к списку", key="btn_back_from_act"):
                    st.session_state.pop("show_act_for", None)
                    st.rerun()
                st.subheader("Акт приема-передачи")
                show_act(trip, ship)
                return
        st.session_state.pop("show_act_for", None)

    trips = get_trips(fresh=True)
    shipments = get_shipments(fresh=True)

    if view_mode == "archive":
        filtered = [t for t in trips if is_archived(t.get("archived", "0"))]
        st.info("Показаны архивные рейсы")
    else:
        filtered = [t for t in trips if not is_archived(t.get("archived", "0"))]

    with st.expander("Сортировка и поиск", expanded=False):
        c1, c2, c3 = st.columns(3)
        sort_by = c1.selectbox("Сортировать по", [
            "Дата выезда (новые сверху)", "Дата выезда (старые сверху)",
            "Гос номер тягача (А-Я)", "Гос номер тягача (Я-А)",
        ])
        search_tractor = c2.text_input("Поиск по гос номеру тягача")
        search_driver = c3.text_input("Поиск по водителю")

    if search_tractor:
        filtered = [t for t in filtered if search_tractor.lower() in s(t.get("tractor_number", "")).lower()]
    if search_driver:
        filtered = [t for t in filtered if search_driver.lower() in s(t.get("driver", "")).lower()]

    if sort_by.startswith("Дата выезда"):
        filtered = sorted(filtered, key=lambda t: date_sort_key(t.get("date_departure", "")),
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

    st.subheader("Рейсы" + (" (архив)" if view_mode == "archive" else ""))

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

            cars = [x for x in shipments if s(x.get("trip_id")) == s(trip_id)]
            total = sum(to_float(x.get("amount")) for x in cars)
            total_advance = sum(to_float(x.get("advance")) for x in cars)
            total_debt = total - total_advance
            total_nds = sum(to_float(x.get("nds_amount")) for x in cars)

            header = s(tractor) + " — " + s(driver) + " — " + s(route) + " — выезд " + s(dep)
            if ret:
                header += " — возврат " + s(ret)

            summary = ("авто: " + s(len(cars)) + "/8"
                       + "  |  сумма: " + fmt_money(total)
                       + "  |  аванс: " + fmt_money(total_advance)
                       + "  |  задолженность: " + fmt_money(total_debt))
            if total_nds > 0:
                summary += "  |  НДС: " + fmt_money(total_nds)

            with st.expander(header + "  |  " + summary):

                c1, c2, c3, c4 = st.columns(4)
                if view_mode == "active":
                    if can("create_ship", role):
                        if c1.button("Добавить авто", key="btn_show_addcar_" + s(trip_id)):
                            st.session_state["open_addcar_" + s(trip_id)] = True
                    if can("edit_trip", role):
                        if c2.button("Редактировать рейс", key="btn_show_edittrip_" + s(trip_id)):
                            st.session_state["open_edittrip_" + s(trip_id)] = True
                    if can("archive", role):
                        if c3.button("📦 В архив", key="btn_arch_trip_" + s(trip_id)):
                            archive_trip(trip_id)
                            log_action(u["login"], role, "archive_trip", s(tractor) + " " + s(driver))
                            st.success("Рейс отправлен в архив")
                            st.rerun()
                    if can("delete_trip", role):
                        if c4.button("Удалить рейс", key="btn_show_deltrip_" + s(trip_id)):
                            st.session_state["open_deltrip_" + s(trip_id)] = True
                else:
                    if can("archive", role):
                        if c1.button("♻ Вернуть из архива", key="btn_unarch_trip_" + s(trip_id)):
                            unarchive_trip(trip_id)
                            log_action(u["login"], role, "unarchive_trip", s(tractor) + " " + s(driver))
                            st.success("Рейс возвращён из архива")
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
                                update_trip(trip_id, e_tractor, e_driver, e_route, e_dep_fmt, e_ret_fmt)
                                log_action(u["login"], role, "edit_trip",
                                           s(e_tractor) + " " + s(e_driver) + " " + s(e_dep_fmt))
                                st.session_state.pop("open_edittrip_" + s(trip_id), None)
                                st.success("Рейс обновлён")
                                st.rerun()

                if st.session_state.get("open_deltrip_" + s(trip_id)):
                    st.warning("Удалить рейс «" + s(tractor) + " — " + s(driver) + "» вместе со всеми авто?")
                    cc1, cc2 = st.columns(2)
                    if cc1.button("Да, удалить", key="btn_yes_del_trip_" + s(trip_id)):
                        delete_trip(trip_id)
                        log_action(u["login"], role, "delete_trip", s(tractor) + " " + s(driver))
                        st.session_state.pop("open_deltrip_" + s(trip_id), None)
                        st.rerun()
                    if cc2.button("Отмена", key="btn_no_del_trip_" + s(trip_id)):
                        st.session_state.pop("open_deltrip_" + s(trip_id), None)
                        st.rerun()

                if view_mode == "active" and st.session_state.get("open_addcar_" + s(trip_id)):
                    f = render_shipment_form("newcar_" + s(trip_id), c=None,
                                             submit_label="Сохранить авто")
                    if f["cancel"]:
                        st.session_state.pop("open_addcar_" + s(trip_id), None)
                        st.rerun()
                    if f["save"]:
                        if len(cars) >= 8:
                            st.error("На автовозе максимум 8 авто")
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
                                nds_val, no_nds_val = calc_nds(amt_val, f["payer_type"])
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

                    hc = st.columns([1, 3, 3, 3, 2, 2, 2, 2, 2, 3, 2, 2, 1])
                    hc[0].markdown("**№**")
                    hc[1].markdown("**Марка / модель**")
                    hc[2].markdown("**Клиент / Заказчик**")
                    hc[3].markdown("**VIN**")
                    hc[4].markdown("**Сумма**")
                    hc[5].markdown("**Аванс**")
                    hc[6].markdown("**Задолж.**")
                    hc[7].markdown("**Город доставки**")
                    hc[8].markdown("**Дата аванса**")
                    hc[9].markdown("**Способ оплаты**")
                    hc[10].markdown("**Кому перевод**")
                    hc[11].markdown("**№ договора**")
                    hc[12].markdown("**НДС**")

                    for x in sorted(cars, key=lambda z: int(to_float(z.get("position")))):
                        amount_val = to_float(x.get("amount"))
                        advance_val = to_float(x.get("advance"))
                        debt_val = amount_val - advance_val
                        nds_val = to_float(x.get("nds_amount"))
                        issued_val = s(x.get("issued", "0")).strip()
                        issued_ever_val = s(x.get("issued_ever", "0")).strip()
                        paid_val = s(x.get("paid", "0")).strip()
                        is_paid_flag = check_paid(paid_val)
                        is_issued_flag = check_issued(issued_val)
                        is_ever_flag = check_issued_ever(issued_ever_val)
                        has_debt = debt_val > 0.01

                        if is_issued_flag:
                            bg = "#c8e6c9"; bd = "#4caf50"; status_text = "🟢 Выдан"
                        elif is_paid_flag:
                            bg = "#fff9c4"; bd = "#ffeb3b"; status_text = "🟡 Оплачен"
                        elif has_debt:
                            bg = "#ffcdd2"; bd = "#f44336"; status_text = "🔴 Долг"
                        else:
                            bg = "#ffffff"; bd = "#dddddd"; status_text = "⚪ —"

                        st.markdown(
                            '<div style="background-color:' + bg +
                            '; border:1px solid ' + bd +
                            '; border-radius:6px; padding:6px; margin-bottom:4px;">',
                            unsafe_allow_html=True)

                        client_display = s(x.get("client", "")) or s(x.get("customer", ""))
                        row_cols = st.columns([1, 3, 3, 3, 2, 2, 2, 2, 2, 3, 2, 2, 1])
                        row_cols[0].write(s(x.get("position", "")))
                        row_cols[1].write(s(x.get("car_model", "")))
                        row_cols[2].write(client_display)
                        row_cols[3].write(s(x.get("vin", ""))[:17])
                        row_cols[4].write(fmt_money(amount_val))
                        row_cols[5].write(fmt_money(advance_val))
                        row_cols[6].write(fmt_money(debt_val))
                        row_cols[7].write(s(x.get("delivery_city", "")))
                        row_cols[8].write(date_to_display_safe(x.get("advance_date", "")))
                        row_cols[9].write(s(x.get("payer_type", "")))
                        row_cols[10].write(s(x.get("paid_to", "")))
                        row_cols[11].write(s(x.get("contract_number", "")))
                        row_cols[12].write(fmt_money(nds_val) if nds_val > 0 else "—")

                        if view_mode == "active":
                            btn_cols = st.columns([2, 1, 1, 1, 1, 1, 5])

                            btn_cols[0].markdown(
                                '<div style="padding:6px 10px; background:#f0f0f0; '
                                'border-radius:6px; text-align:center; font-weight:bold;">'
                                + status_text + '</div>',
                                unsafe_allow_html=True)

                            paid_label = "❌ Снять" if is_paid_flag else "✅ Оплачен"
                            if btn_cols[1].button(paid_label, key="btn_paid_" + s(x["id"])):
                                toggle_paid(x["id"], paid_val, amount_val, advance_val)
                                log_action(u["login"], role, "toggle_paid",
                                           "рейс " + s(trip_id) + ", поз " + s(x.get("position")))
                                st.rerun()

                            if is_issued_flag:
                                if btn_cols[2].button("↩ Снять выдан",
                                                     key="btn_issued_" + s(x["id"])):
                                    toggle_issued(x["id"], issued_val, issued_ever_val)
                                    log_action(u["login"], role, "toggle_issued",
                                               "рейс " + s(trip_id) + ", поз " + s(x.get("position")))
                                    st.rerun()
                            else:
                                clicked_issued = btn_cols[2].button(
                                    "✅ Выдан",
                                    key="btn_issued_" + s(x["id"]),
                                    disabled=not is_paid_flag)
                                if clicked_issued and is_paid_flag:
                                    toggle_issued(x["id"], issued_val, issued_ever_val)
                                    log_action(u["login"], role, "toggle_issued",
                                               "рейс " + s(trip_id) + ", поз " + s(x.get("position")))
                                    st.rerun()

                            if btn_cols[3].button("📄 Акт", key="btn_act_inline_" + s(x["id"])):
                                st.session_state["show_act_for"] = x["id"]
                                st.rerun()

                            if btn_cols[4].button("✏", key="btn_edit_" + s(x["id"])):
                                st.session_state["edit_ship_" + s(x["id"])] = True

                            if can("delete_ship", role):
                                if btn_cols[5].button("🗑", key="btn_delship_" + s(x["id"])):
                                    delete_shipment(x["id"])
                                    log_action(u["login"], role, "delete_ship",
                                               "рейс " + s(trip_id) + ", поз " + s(x.get("position")))
                                    st.rerun()
                        else:
                            btn_cols = st.columns([2, 1, 5])
                            btn_cols[0].markdown(
                                '<div style="padding:6px 10px; background:#f0f0f0; '
                                'border-radius:6px; text-align:center; font-weight:bold;">'
                                + status_text + '</div>',
                                unsafe_allow_html=True)
                            if btn_cols[1].button("📄 Акт", key="btn_act_inline_" + s(x["id"])):
                                st.session_state["show_act_for"] = x["id"]
                                st.rerun()

                        st.markdown("</div>", unsafe_allow_html=True)

                    st.markdown("---")
                    sum_cols = st.columns(5)
                    sum_cols[0].markdown("**Сумма:** " + fmt_money(total))
                    sum_cols[1].markdown("**Аванс:** " + fmt_money(total_advance))
                    sum_cols[2].markdown("**Задолженность:** " + fmt_money(total_debt))
                    sum_cols[3].markdown("**НДС 22%:** " + fmt_money(total_nds))
                    sum_cols[4].markdown("**Авто:** " + s(len(cars)) + "/8")

                    if view_mode == "active" and can("archive", role):
                        all_paid = all(check_paid(x.get("paid", "0")) for x in cars)
                        if all_paid:
                            st.success("✅ Все авто оплачены — рейс можно отправить в архив")
                            if st.button("📦 Отправить рейс в архив", key="btn_arch_all_" + s(trip_id)):
                                archive_trip(trip_id)
                                log_action(u["login"], role, "archive_trip_full",
                                           s(tractor) + " " + s(driver))
                                st.success("Рейс отправлен в архив")
                                st.rerun()
                        else:
                            st.warning("⚠ Есть неоплаченные авто — рейс нельзя отправить в архив, пока не оплачены")

                    for x in sorted(cars, key=lambda z: int(to_float(z.get("position")))):
                        if st.session_state.get("edit_ship_" + s(x["id"])):
                            st.markdown("**Редактировать авто (позиция " + s(x.get("position")) + ")**")
                            f = render_shipment_form("editcar_" + s(x["id"]), c=x,
                                                     submit_label="Сохранить изменения")
                            if f["cancel"]:
                                st.session_state.pop("edit_ship_" + s(x["id"]), None)
                                st.rerun()
                            if f["save"]:
                                if not f["client"] and not f["customer"]:
                                    st.error("Заполните хотя бы одно: ФИО клиента или Заказчик")
                                else:
                                    try:
                                        e_dp = parse_date_ui(f["date_pay"])
                                        e_dpa = parse_date_ui(f["advance_date"])
                                    except ValueError as ex:
                                        st.error(str(ex))
                                    else:
                                        amt_val = money_value(f["amount"])
                                        adv_val = money_value(f["advance"])
                                        nds_val, no_nds_val = calc_nds(amt_val, f["payer_type"])
                                        update_shipment(x["id"], f["position"], f["car_model"],
                                                        f["client"], amt_val, e_dp, f["paid_to"],
                                                        f["delivery_city"], f["vin"][:17],
                                                        adv_val, e_dpa, f["payer_type"],
                                                        f["customer"], f["contract_number"],
                                                        nds_val, no_nds_val)
                                        log_action(u["login"], role, "edit_ship",
                                                   "рейс " + s(trip_id) + ", поз " + s(f["position"]))
                                        st.session_state.pop("edit_ship_" + s(x["id"]), None)
                                        st.success("Изменения сохранены")
                                        st.rerun()

                    if view_mode == "active" and can("create_ship", role) and len(cars) < 8:
                        if st.button("Добавить еще авто (позиция " + s(len(cars)+1) + ")",
                                     key="btn_more_addcar_" + s(trip_id)):
                            st.session_state["open_addcar_" + s(trip_id)] = True
                            st.rerun()
                else:
                    st.info("В этом рейсе ещё нет авто.")

    # ============================================================
    # ИТОГО (снизу справа) — только по АКТИВНЫМ рейсам
    # ============================================================
    st.markdown("---")

    active_trip_ids = {s(t.get("id")) for t in trips if not is_archived(t.get("archived", "0"))}
    all_active_cars = [x for x in shipments if s(x.get("trip_id")) in active_trip_ids]

    grand_total = sum(to_float(x.get("amount")) for x in all_active_cars)
    grand_advance = sum(to_float(x.get("advance")) for x in all_active_cars)
    grand_debt = grand_total - grand_advance
    grand_paid_amount = sum(
        to_float(x.get("amount")) for x in all_active_cars
        if check_paid(x.get("paid", "0"))
    )
    grand_nds = sum(to_float(x.get("nds_amount")) for x in all_active_cars)

    spacer, totals = st.columns([2, 1])
    with totals:
        st.markdown(
            '<div style="background:#f5f5f5; border:1px solid #cccccc; '
            'border-radius:8px; padding:14px 18px;">'
            '<div style="font-size:18px; font-weight:bold; margin-bottom:8px;">'
            'Итого (активные)</div>'
            '<div style="display:flex; justify-content:space-between; margin:4px 0;">'
            '<span>Активных рейсов:</span><b>' + s(len(active_trip_ids)) + '</b></div>'
            '<div style="display:flex; justify-content:space-between; margin:4px 0;">'
            '<span>Авто:</span><b>' + s(len(all_active_cars)) + '</b></div>'
            '<div style="display:flex; justify-content:space-between; margin:4px 0;">'
            '<span>Общая сумма:</span><b>' + fmt_money(grand_total) + ' ₽</b></div>'
            '<div style="display:flex; justify-content:space-between; margin:4px 0;">'
            '<span>Общая оплата:</span><b>' + fmt_money(grand_advance) + ' ₽</b></div>'
            '<div style="display:flex; justify-content:space-between; margin:4px 0; '
            'border-top:1px solid #cccccc; padding-top:6px;">'
            '<span>Общая задолженность:</span>'
            '<b style="color:' + ("#c62828" if grand_debt > 0.01 else "#2e7d32") + ';">'
            + fmt_money(grand_debt) + ' ₽</b></div>'
            + ('<div style="display:flex; justify-content:space-between; margin:4px 0;">'
               '<span>НДС 22%:</span><b>' + fmt_money(grand_nds) + ' ₽</b></div>'
               if grand_nds > 0 else '')
            + '<div style="display:flex; justify-content:space-between; margin:4px 0;">'
            '<span>Оплачено (по флагу):</span><b>' + fmt_money(grand_paid_amount) + ' ₽</b></div>'
            '</div>',
            unsafe_allow_html=True,
        )


def main():
    st.set_page_config(page_title="Учёт рейсов", page_icon="🚛", layout="wide")
    try:
        ensure_sheets_once()
    except Exception as e:
        st.error("Ошибка подключения к Google Sheets: " + type(e).__name__ + ": " + s(e))
        st.stop()
    ensure_first_admin()
    if "user" not in st.session_state:
        login_page()
    else:
        main_page()


if __name__ == "__main__":
    main()

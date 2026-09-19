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
                  "created_by", "created_at", "issued", "paid", "archived"],
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


def read_all(name):
    return read_all_cached(name)


def invalidate_cache(name=None):
    if name:
        read_all_cached.clear(name)
    else:
        read_all_cached.clear()


@st.cache_resource
def ensure_sheets_once():
    book = get_book()
    existing = {ws.title for ws in book.worksheets()}
    for name, headers in SHEET_SCHEMAS.items():
        if name not in existing:
            ws = book.add_worksheet(title=name, rows=1000, cols=len(headers))
            ws.append_row(headers)
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
    return s(v).strip().lower() in ("1", "1.0", "true")


def check_issued(v):
    return s(v).strip().lower() in ("1", "1.0", "true")


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


def get_trips():
    return read_all("trips")


def get_shipments():
    return read_all("shipments")


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
            created_by = row[6] if len(row) > 6 else ""
            created_at = row[7] if len(row) > 7 else ""
            archived = row[8] if len(row) > 8 else "0"
            new_row = [trip_id, s(tractor), s(driver), s(route), s(dep), s(ret),
                       created_by, created_at, archived]
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
        "paid": "0",
        "archived": "0",
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
            paid_val = row[20] if len(row) > 20 else "0"
            archived_val = row[21] if len(row) > 21 else "0"
            new_row = [
                shipment_id, trip_id, position, s(car_model), s(client),
                money_value(amount), s(date_pay), s(paid_to),
                s(delivery_city), s(vin),
                money_value(advance), s(advance_date),
                s(payer_type), s(customer), s(contract_number),
                money_value(nds_amount), money_value(amount_no_nds),
                created_by, created_at, issued_val, paid_val, archived_val,
            ]
            ws.update("A" + str(i) + ":" + last_col + str(i), [new_row])
            invalidate_cache("shipments")
            return True
    return False


def toggle_issued(shipment_id, current_value):
    ws = get_ws_cached("shipments")
    for i, row in enumerate(ws.get_all_values()[1:], start=2):
        if s(row[0]) == s(shipment_id):
            new_val = "0" if s(current_value) == "1" else "1"
            ws.update_cell(i, 20, new_val)
            invalidate_cache("shipments")
            return new_val
    return current_value


def toggle_paid(shipment_id, current_value):
    """При включении 'Оплачен' — весь остаток переносится в аванс (долг → 0).
    При отмене — аванс обнуляется."""
    ws = get_ws_cached("shipments")
    all_rows = ws.get_all_values()
    for i, row in enumerate(all_rows[1:], start=2):
        if s(row[0]) == s(shipment_id):
            amount_val = to_float(row[5]) if len(row) > 5 else 0.0
            current_paid = s(current_value)
            if current_paid == "1":
                # снимаем — обнуляем аванс
                new_paid = "0"
                new_advance = 0.0
            else:
                # ставим — весь остаток в аванс
                new_paid = "1"
                new_advance = amount_val
            ws.update_cell(i, 11, money_value(new_advance))   # колонка K = advance
            ws.update_cell(i, 21, new_paid)                    # колонка U = paid
            invalidate_cache("shipments")
            return new_paid
    return current_value


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
        amount = col7.text_input("Сумма за перевозку (пример

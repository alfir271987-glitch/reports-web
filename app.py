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
                  "cdek_track", "cdek_date",
                  "nds_amount", "amount_no_nds",
                  "created_by", "created_at", "issued", "archived"],
    "audit_log": ["id", "ts", "login", "role", "action", "details"],
    "act_log": ["id", "ts", "login", "trip_id", "shipment_id", "client"],
}

ROLES = ["admin", "director", "logist", "dispatcher"]

PAYER_TYPES = ["нал", "эквайринг", "безнал с НДС 22%"]
NDS_RATE = 0.22


def calc_nds(amount, payer_type):
    amount = to_float(amount)
    if payer_type == "безнал с НДС 22%":
        nds = amount * NDS_RATE / (1 + NDS_RATE)
        amount_no_nds = amount - nds
        return nds, amount_no_nds
    return 0.0, amount


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
        login,
        role,
        action,
        details,
    ]
    ws.append_row(row)
    invalidate_cache("audit_log")


def to_float(s):
    if s is None:
        return 0.0
    if isinstance(s, (int, float)):
        return float(s)
    try:
        s = str(s).strip()
    except Exception:
        return 0.0
    s = s.replace(" ", "").replace(",", ".")
    if not s:
        return 0.0
    try:
        return float(s)
    except ValueError:
        return 0.0


def fmt_money(x):
    return "{:,}".format(int(round(to_float(x)))).replace(",", " ")


def parse_date_ui(s):
    if s is None:
        return ""
    s = str(s).strip()
    if not s:
        return ""
    for fmt in ("%d.%m.%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(s, fmt).strftime("%d.%m.%Y")
        except ValueError:
            continue
    raise ValueError("Неверный формат даты: «" + s + "». Введите ДД.ММ.ГГГГ (например 12.05.2026)")


def date_to_display_safe(s):
    if s is None:
        return ""
    s = str(s).strip()
    if not s:
        return ""
    for fmt in ("%d.%m.%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(s, fmt).strftime("%d.%m.%Y")
        except ValueError:
            continue
    return s


def date_sort_key(s):
    if s is None:
        return datetime.min.date()
    s = str(s).strip()
    if not s:
        return datetime.min.date()
    for fmt in ("%d.%m.%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    return datetime.min.date()


def is_active(u):
    val = u.get("active")
    return str(val).strip().lower() in ("1", "1.0", "true")


def is_archived(v):
    return str(v).strip().lower() in ("1", "1.0", "true")


def safe_df(rows):
    if not rows:
        return rows
    clean = []
    for r in rows:
        clean.append({k: ("" if v is None else str(v)) for k, v in r.items()})
    return clean


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
                    "login": login,
                    "password_hash": hash_password(pwd),
                    "role": "admin",
                    "full_name": name,
                    "active": "1",
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
        "logist":     {"create_trip", "edit_trip",
                       "create_ship", "edit_ship",
                       "export", "print_act", "archive"},
        "dispatcher": {"create_trip", "edit_trip",
                       "create_ship", "edit_ship",
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
        u = next((x for x in users if str(x.get("login")) == login), None)
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
            "login": u["login"],
            "role": u["role"],
            "full_name": u["full_name"],
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
            c1.write(u["login"])
            c2.write(u["role"])
            c3.write(u["full_name"])
            active = is_active(u)
            c4.write("активен" if active else "отключён")
            if c5.button("Отключить" if active else "Включить",
                         key="toggle_user_" + str(u['login'])):
                ws = get_ws_cached("users")
                all_rows = ws.get_all_values()
                for i, row in enumerate(all_rows[1:], start=2):
                    if row[0] == u["login"]:
                        new_val = "0" if active else "1"
                        ws.update_cell(i, 5, new_val)
                        invalidate_cache("users")
                        log_action(st.session_state["user"]["login"],
                                   st.session_state["user"]["role"],
                                   "toggle_user", str(u['login']) + " -> " + new_val)
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
                    "login": new_login,
                    "password_hash": hash_password(new_pwd),
                    "role": new_role,
                    "full_name": new_name,
                    "active": "1",
                })
                log_action(st.session_state["user"]["login"],
                           st.session_state["user"]["role"],
                           "create_user", new_login + " / " + new_role)
                st.success("Пользователь " + new_login + " создан")
                st.rerun()

    with tab2:
        st.subheader("Журнал действий")
        rows = read_all("audit_log")
        rows = list(reversed(rows))[:200]
        if rows:
            st.dataframe(safe_df(rows), use_container_width=True)
        else:
            st.info("Пока пусто")

    with tab3:
        st.subheader("Журнал печати актов")
        rows = read_all("act_log")
        rows = list(reversed(rows))[:200]
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
        "tractor_number": tractor,
        "driver": driver,
        "route": route,
        "date_departure": dep,
        "date_return": ret,
        "created_by": created_by,
        "created_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "archived": "0",
    })


def update_trip(trip_id, tractor, driver, route, dep, ret):
    ws = get_ws_cached("trips")
    all_rows = ws.get_all_values()
    headers = SHEET_SCHEMAS["trips"]
    last_col = chr(64 + len(headers))
    for i, row in enumerate(all_rows[1:], start=2):
        if str(row[0]) == str(trip_id):
            created_by = row[6] if len(row) > 6 else ""
            created_at = row[7] if len(row) > 7 else ""
            archived = row[8] if len(row) > 8 else "0"
            new_row = [trip_id, tractor, driver, route, dep, ret, created_by, created_at, archived]
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
        if str(row[0]) == str(trip_id):
            created_by = row[6] if len(row) > 6 else ""
            created_at = row[7] if len(row) > 7 else ""
            new_row = [trip_id, row[1], row[2], row[3], row[4], row[5], created_by, created_at, "1"]
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
        if str(row[0]) == str(trip_id):
            created_by = row[6] if len(row) > 6 else ""
            created_at = row[7] if len(row) > 7 else ""
            new_row = [trip_id, row[1], row[2], row[3], row[4], row[5], created_by, created_at, "0"]
            ws.update("A" + str(i) + ":" + last_col + str(i), [new_row])
            invalidate_cache("trips")
            return True
    return False


def delete_trip(trip_id):
    ws = get_ws_cached("trips")
    all_rows = ws.get_all_values()
    for i, row in enumerate(all_rows[1:], start=2):
        if str(row[0]) == str(trip_id):
            ws.delete_rows(i)
            invalidate_cache("trips")
            break
    ws2 = get_ws_cached("shipments")
    rows2 = ws2.get_all_values()
    to_delete = []
    for i, row in enumerate(rows2[1:], start=2):
        if str(row[1]) == str(trip_id):
            to_delete.append(i)
    for i in reversed(to_delete):
        ws2.delete_rows(i)
    invalidate_cache("shipments")


def create_shipment(trip_id, position, car_model, client, amount,
                    date_pay, paid_to, delivery_city, vin,
                    advance, advance_date,
                    payer_type, customer, contract_number,
                    cdek_track, cdek_date,
                    nds_amount, amount_no_nds, created_by):
    append_row("shipments", {
        "id": next_id("shipments"),
        "trip_id": trip_id,
        "position": position,
        "car_model": car_model,
        "client": client,
        "amount": amount,
        "date_pay": date_pay,
        "paid_to": paid_to,
        "delivery_city": delivery_city,
        "vin": vin,
        "advance": advance,
        "advance_date": advance_date,
        "payer_type": payer_type,
        "customer": customer,
        "contract_number": contract_number,
        "cdek_track": cdek_track,
        "cdek_date": cdek_date,
        "nds_amount": nds_amount,
        "amount_no_nds": amount_no_nds,
        "created_by": created_by,
        "created_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "issued": "0",
        "archived": "0",
    })


def update_shipment(shipment_id, position, car_model, client, amount,
                    date_pay, paid_to, delivery_city, vin, advance, advance_date,
                    payer_type, customer, contract_number, cdek_track, cdek_date,
                    nds_amount, amount_no_nds):
    ws = get_ws_cached("shipments")
    all_rows = ws.get_all_values()
    headers = SHEET_SCHEMAS["shipments"]
    last_col = chr(64 + len(headers))
    for i, row in enumerate(all_rows[1:], start=2):
        if str(row[0]) == str(shipment_id):
            trip_id = row[1] if len(row) > 1 else ""
            created_by = row[19] if len(row) > 19 else ""
            created_at = row[20] if len(row) > 20 else ""
            issued_val = row[21] if len(row) > 21 else "0"
            archived_val = row[22] if len(row) > 22 else "0"
            new_row = [
                shipment_id, trip_id, position, car_model, client,
                amount, date_pay, paid_to,
                delivery_city, vin,
                advance, advance_date,
                payer_type, customer, contract_number,
                cdek_track, cdek_date,
                nds_amount, amount_no_nds,
                created_by, created_at, issued_val, archived_val,
            ]
            ws.update("A" + str(i) + ":" + last_col + str(i), [new_row])
            invalidate_cache("shipments")
            return True
    return False


def toggle_issued(shipment_id, current_value):
    ws = get_ws_cached("shipments")
    all_rows = ws.get_all_values()
    for i, row in enumerate(all_rows[1:], start=2):
        if str(row[0]) == str(shipment_id):
            new_val = "0" if str(current_value) == "1" else "1"
            ws.update_cell(i, 22, new_val)
            invalidate_cache("shipments")
            return new_val
    return current_value


def delete_shipment(shipment_id):
    ws = get_ws_cached("shipments")
    all_rows = ws.get_all_values()
    for i, row in enumerate(all_rows[1:], start=2):
        if str(row[0]) == str(shipment_id):
            ws.delete_rows(i)
            invalidate_cache("shipments")
            return


def log_act_print(login, trip_id, shipment_id, client):
    ws = get_ws_cached("act_log")
    row = [
        str(int(datetime.now().timestamp())),
        datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        login,
        trip_id,
        shipment_id,
        client,
    ]
    ws.append_row(row)
    invalidate_cache("act_log")


# ============================================================
# АКТ
# ============================================================

def render_act_html(trip, shipment):
    route = trip.get("route", "")
    car_model = shipment.get("car_model", "")
    client = shipment.get("client", "")
    customer = shipment.get("customer", "")
    vin = str(shipment.get("vin", "")).strip()[:17]
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
    route = trip.get("route", "")
    car_model = shipment.get("car_model", "")
    client = shipment.get("client", "")
    customer = shipment.get("customer", "")
    vin = str(shipment.get("vin", "")).strip()[:17]
    receiver = client if client else customer

    LINE_LONG = "_" * 30
    LINE_SHORT = "_" * 12

    lines = [
        "АКТ ПРИЕМА-ПЕРЕДАЧИ ТРАНСПОРТНОГО СРЕДСТВА",
        "",
        "Перевозчик: ИП Сагитдинов Максим Наильевич, тел. 8-987-131-00-62",
        "Заказчик / Получатель: " + receiver,
        "Марка автомобиля: " + car_model,
        "VIN: " + (vin if vin else "_" * 20),
        "Маршрут: " + route,
        "",
        "",
        "Груз сдал: " + LINE_LONG + " / Сагитдинов М.Н. /",
        "Груз принял: " + LINE_LONG + " / " + receiver + " /",
        "",
        "",
        "Дата вручения груза: " + LINE_SHORT + "   Время: " + LINE_SHORT,
        "",
        "При подписании акта приема-передачи на момент вручения груза Стороны каких-либо претензий друг к другу не имеют.",
    ]
    return "\n".join(lines)


def show_act(trip, shipment):
    u = st.session_state["user"]
    log_act_print(u["login"], trip.get("id"), shipment.get("id"), shipment.get("client"))

    st.info("Нажмите **Ctrl+P** для печати или скачайте акт кнопкой ниже.")

    safe_car = str(shipment.get("car_model", "")).replace("/", "-").replace("\\", "-")
    safe_client = str(shipment.get("client", "")).replace("/", "-").replace("\\", "-")
    base_name = "Акт_" + safe_car + "_" + safe_client

    col1, col2 = st.columns(2)
    with col1:
        st.download_button(
            "Скачать акт (.doc)",
            data=render_act_html(trip, shipment).encode("utf-8"),
            file_name=base_name + ".doc",
            mime="application/msword",
            key="dl_doc_" + str(shipment.get("id"))
        )
    with col2:
        st.download_button(
            "Скачать акт (.txt)",
            data=render_act_text(trip, shipment).encode("utf-8-sig"),
            file_name=base_name + ".txt",
            mime="text/plain",
            key="dl_txt_" + str(shipment.get("id"))
        )

    st.components.v1.html(render_act_html(trip, shipment), height=850, scrolling=True)


# ============================================================
# ФОРМА АВТО — с уникальным ключом формы
# ============================================================

def render_shipment_form(form_key, c=None, submit_label="Сохранить авто"):
    defaults = {
        "position": 1,
        "car_model": "",
        "client": "",
        "vin": "",
        "delivery_city": "",
        "amount": "",
        "date_pay": "",
        "paid_to": "",
        "advance": "",
        "advance_date": "",
        "payer_type": "нал",
        "customer": "",
        "contract_number": "",
        "cdek_track": "",
        "cdek_date": "",
    }
    if c:
        defaults["position"] = int(to_float(c.get("position")) or 1)
        defaults["car_model"] = c.get("car_model", "")
        defaults["client"] = c.get("client", "")
        defaults["vin"] = str(c.get("vin", ""))[:17]
        defaults["delivery_city"] = c.get("delivery_city", "")
        defaults["amount"] = str(int(to_float(c.get("amount"))))
        defaults["date_pay"] = date_to_display_safe(c.get("date_pay", ""))
        defaults["paid_to"] = c.get("paid_to", "")
        defaults["advance"] = str(int(to_float(c.get("advance"))))
        defaults["advance_date"] = date_to_display_safe(c.get("advance_date", ""))
        defaults["payer_type"] = c.get("payer_type", "нал") or "нал"
        defaults["customer"] = c.get("customer", "")
        defaults["contract_number"] = c.get("contract_number", "")
        defaults["cdek_track"] = c.get("cdek_track", "")
        defaults["cdek_date"] = date_to_display_safe(c.get("cdek_date", ""))

    payer_options = PAYER_TYPES
    try:
        payer_index = payer_options.index(defaults["payer_type"])
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
        client = col3.text_input("ФИО клиента (если нет Заказчика)", value=defaults["client"],
                                  key=form_key + "_client")
        vin = col4.text_input("VIN (до 17 символов)", value=defaults["vin"],
                              key=form_key + "_vin")

        delivery_city = st.text_input("Город доставки", value=defaults["delivery_city"],
                                      key=form_key + "_city")

        col5, col6 = st.columns(2)
        amount = col5.text_input("Сумма за перевозку", value=defaults["amount"],
                                 key=form_key + "_amount")
        payer_type = col6.selectbox("Способ оплаты", payer_options, index=payer_index,
                                     key=form_key + "_payer")

        col7, col8 = st.columns(2)
        date_pay = col7.text_input("Дата оплаты (ДД.ММ.ГГГГ)", value=defaults["date_pay"],
                                    key=form_key + "_dpay")
        paid_to = col8.text_input("Кому произведён перевод", value=defaults["paid_to"],
                                  key=form_key + "_paidto")

        col9, col10 = st.columns(2)
        advance = col9.text_input("Аванс (сумма)", value=defaults["advance"],
                                  key=form_key + "_adv")
        advance_date = col10.text_input("Дата аванса (ДД.ММ.ГГГГ)", value=defaults["advance_date"],
                                         key=form_key + "_advd")

        col11, col12 = st.columns(2)
        customer = col11.text_input("Заказчик (если нет ФИО клиента)", value=defaults["customer"],
                                    key=form_key + "_customer")
        contract_number = col12.text_input("№ договора", value=defaults["contract_number"],
                                            key=form_key + "_contract")

        col13, col14 = st.columns(2)
        cdek_track = col13.text_input("СДЭК: номер накладной", value=defaults["cdek_track"],
                                       key=form_key + "_cdek_track")
        cdek_date = col14.text_input("СДЭК: дата отправки (ДД.ММ.ГГГГ)", value=defaults["cdek_date"],
                                      key=form_key + "_cdek_date")

        save = st.form_submit_button(submit_label)
        cancel = st.form_submit_button("Отмена")

    return {
        "position": position,
        "car_model": car_model,
        "client": client,
        "vin": vin,
        "delivery_city": delivery_city,
        "amount": amount,
        "date_pay": date_pay,
        "paid_to": paid_to,
        "advance": advance,
        "advance_date": advance_date,
        "payer_type": payer_type,
        "customer": customer,
        "contract_number": contract_number,
        "cdek_track": cdek_track,
        "cdek_date": cdek_date,
        "save": save,
        "cancel": cancel,
    }


def main_page():
    u = st.session_state["user"]
    role = u["role"]

    st.sidebar.markdown("**" + u["full_name"] + "**  \nроль: `" + role + "`")
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
        shipments = get_shipments()
        trips = get_trips()
        ship = next((s for s in shipments if str(s.get("id")) == str(sid)), None)
        if ship:
            trip = next((t for t in trips if str(t.get("id")) == str(ship.get("trip_id"))), None)
            if trip:
                if st.button("Назад к списку", key="btn_back_from_act"):
                    st.session_state.pop("show_act_for", None)
                    st.rerun()
                st.subheader("Акт приема-передачи")
                show_act(trip, ship)
                return
        st.session_state.pop("show_act_for", None)

    trips = get_trips()
    shipments = get_shipments()

    if view_mode == "archive":
        filtered = [t for t in trips if is_archived(t.get("archived", "0"))]
        st.info("Показаны архивные рейсы")
    else:
        filtered = [t for t in trips if not is_archived(t.get("archived", "0"))]

    with st.expander("Сортировка и поиск", expanded=False):
        c1, c2, c3 = st.columns(3)
        sort_by = c1.selectbox("Сортировать по", [
            "Дата выезда (новые сверху)",
            "Дата выезда (старые сверху)",
            "Гос номер тягача (А-Я)",
            "Гос номер тягача (Я-А)",
        ])
        search_tractor = c2.text_input("Поиск по гос номеру тягача")
        search_driver = c3.text_input("Поиск по водителю")

    if search_tractor:
        filtered = [t for t in filtered if search_tractor.lower() in str(t.get("tractor_number", "")).lower()]
    if search_driver:
        filtered = [t for t in filtered if search_driver.lower() in str(t.get("driver", "")).lower()]

    if sort_by.startswith("Дата выезда"):
        reverse = "новые" in sort_by
        filtered = sorted(filtered, key=lambda t: date_sort_key(t.get("date_departure", "")), reverse=reverse)
    else:
        reverse = "Я-А" in sort_by
        filtered = sorted(filtered, key=lambda t: str(t.get("tractor_number", "")), reverse=reverse)

    if view_mode == "active":
        col1, col2 = st.columns([3, 1])
        with col2:
            if can("create_trip", role) and st.button(
                    "Добавить рейс",
                    key="btn_show_new_trip",
                    use_container_width=True):
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
                c5, c6 = st.columns(2)
                ret = c5.text_input("Дата возвращения (можно пусто)", placeholder="20.05.2026")
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
                                   tractor + " " + driver + " " + dep_fmt)
                        st.session_state.pop("show_new_trip", None)
                        st.success("Рейс добавлен")
                        st.rerun()

    st.subheader("Рейсы" + (" (архив)" if view_mode == "archive" else ""))

    if not filtered:
        st.info("Ничего не найдено.")
    else:
        for t in filtered:
            trip_id = t.get("id")
            tractor = t.get("tractor_number", "")
            driver = t.get("driver", "")
            route = t.get("route", "")
            dep = date_to_display_safe(t.get("date_departure", ""))
            ret = date_to_display_safe(t.get("date_return", ""))

            cars = [s for s in shipments if str(s.get("trip_id")) == str(trip_id)]
            total = sum(to_float(c.get("amount")) for c in cars)
            total_advance = sum(to_float(c.get("advance")) for c in cars)
            total_debt = total - total_advance
            total_nds = sum(to_float(c.get("nds_amount")) for c in cars)

            header = tractor + " — " + driver + " — " + route + " — выезд " + dep
            if ret:
                header += " — возврат " + ret

            summary = (
                "авто: " + str(len(cars)) + "/8"
                + "  |  сумма: " + fmt_money(total)
                + "  |  аванс: " + fmt_money(total_advance)
                + "  |  задолженность: " + fmt_money(total_debt)
            )
            if total_nds > 0:
                summary += "  |  НДС: " + fmt_money(total_nds)

            with st.expander(header + "  |  " + summary):

                c1, c2, c3, c4 = st.columns(4)
                if view_mode == "active":
                    if can("create_ship", role):
                        if c1.button("Добавить авто", key="btn_show_addcar_" + str(trip_id)):
                            st.session_state["open_addcar_" + str(trip_id)] = True
                    if can("edit_trip", role):
                        if c2.button("Редактировать рейс", key="btn_show_edittrip_" + str(trip_id)):
                            st.session_state["open_edittrip_" + str(trip_id)] = True
                    if can("archive", role):
                        if c3.button("📦 В архив", key="btn_arch_trip_" + str(trip_id)):
                            archive_trip(trip_id)
                            log_action(u["login"], role, "archive_trip", tractor + " " + driver)
                            st.success("Рейс отправлен в архив")
                            st.rerun()
                    if can("delete_trip", role):
                        if c4.button("Удалить рейс", key="btn_show_deltrip_" + str(trip_id)):
                            st.session_state["open_deltrip_" + str(trip_id)] = True
                else:
                    if can("archive", role):
                        if c1.button("♻ Вернуть из архива", key="btn_unarch_trip_" + str(trip_id)):
                            unarchive_trip(trip_id)
                            log_action(u["login"], role, "unarchive_trip", tractor + " " + driver)
                            st.success("Рейс возвращён из архива")
                            st.rerun()

                if st.session_state.get("open_edittrip_" + str(trip_id)):
                    with st.form("edit_trip_" + str(trip_id)):
                        st.markdown("**Редактировать рейс**")
                        ec1, ec2 = st.columns(2)
                        e_tractor = ec1.text_input("Гос номер тягача", value=tractor)
                        e_driver = ec2.text_input("Водитель ФИО", value=driver)
                        ec3, ec4 = st.columns(2)
                        e_route = ec3.text_input("Маршрут", value=route)
                        e_dep = ec4.text_input("Дата выезда (ДД.ММ.ГГГГ)", value=dep)
                        ec5, ec6 = st.columns(2)
                        e_ret = ec5.text_input("Дата возвращения (можно пусто)", value=ret)
                        e_ok = st.form_submit_button("Сохранить рейс")
                        e_cancel = st.form_submit_button("Отмена")
                    if e_cancel:
                        st.session_state.pop("open_edittrip_" + str(trip_id), None)
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
                                           e_tractor + " " + e_driver + " " + e_dep_fmt)
                                st.session_state.pop("open_edittrip_" + str(trip_id), None)
                                st.success("Рейс обновлён")
                                st.rerun()

                if st.session_state.get("open_deltrip_" + str(trip_id)):
                    st.warning("Удалить рейс «" + tractor + " — " + driver + "» вместе со всеми авто?")
                    cc1, cc2 = st.columns(2)
                    if cc1.button("Да, удалить", key="btn_yes_del_trip_" + str(trip_id)):
                        delete_trip(trip_id)
                        log_action(u["login"], role, "delete_trip", tractor + " " + driver)
                        st.session_state.pop("open_deltrip_" + str(trip_id), None)
                        st.rerun()
                    if cc2.button("Отмена", key="btn_no_del_trip_" + str(trip_id)):
                        st.session_state.pop("open_deltrip_" + str(trip_id), None)
                        st.rerun()

                if view_mode == "active" and st.session_state.get("open_addcar_" + str(trip_id)):
                    form_key = "newcar_" + str(trip_id)
                    f = render_shipment_form(form_key, c=None, submit_label="Сохранить авто")
                    if f["cancel"]:
                        st.session_state.pop("open_addcar_" + str(trip_id), None)
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
                                dcdek = parse_date_ui(f["cdek_date"])
                            except ValueError as e:
                                st.error(str(e))
                            else:
                                nds_val, no_nds_val = calc_nds(to_float(f["amount"]), f["payer_type"])
                                create_shipment(
                                    trip_id, f["position"], f["car_model"], f["client"],
                                    to_float(f["amount"]), dp, f["paid_to"],
                                    f["delivery_city"], str(f["vin"])[:17],
                                    to_float(f["advance"]), dpa,
                                    f["payer_type"], f["customer"], f["contract_number"],
                                    f["cdek_track"], dcdek,
                                    nds_val, no_nds_val,
                                    u["login"])
                                log_action(u["login"], role, "create_ship",
                                           "рейс " + str(trip_id) + ", поз " + str(f["position"]))
                                st.session_state.pop("open_addcar_" + str(trip_id), None)
                                st.success("Авто добавлено")
                                st.rerun()

                if cars:
                    st.markdown("**Список автомобилей в рейсе**")

                    # заголовок таблицы — 19 колонок, добавлена "Кому перевод"
                    hc = st.columns([1, 3, 3, 3, 2, 2, 2, 2, 2, 2, 2, 2, 2, 2, 2, 1, 1, 1, 1])
                    hc[0].markdown("**№**")
                    hc[1].markdown("**Марка / модель**")
                    hc[2].markdown("**Клиент**")
                    hc[3].markdown("**VIN**")
                    hc[4].markdown("**Сумма**")
                    hc[5].markdown("**Аванс**")
                    hc[6].markdown("**Задолж.**")
                    hc[7].markdown("**Город доставки**")
                    hc[8].markdown("**Дата аванса**")
                    hc[9].markdown("**Способ оплаты**")
                    hc[10].markdown("**Кому перевод**")
                    hc[11].markdown("**Заказчик**")
                    hc[12].markdown("**№ договора**")
                    hc[13].markdown("**СДЭК**")
                    hc[14].markdown("**НДС 22%**")
                    hc[15].markdown("**Выдан**")
                    hc[16].markdown("**Акт**")
                    hc[17].markdown("**✏**")
                    hc[18].markdown("**🗑**")

                    for c in sorted(cars, key=lambda x: int(to_float(x.get("position")))):
                        amount_val = to_float(c.get("amount"))
                        advance_val = to_float(c.get("advance"))
                        debt_val = amount_val - advance_val
                        nds_val = to_float(c.get("nds_amount"))
                        issued_val = str(c.get("issued", "0")).strip()
                        is_issued = issued_val in ("1", "1.0", "true", "True")
                        has_debt = debt_val > 0

                        if is_issued:
                            bg = "#c8e6c9"
                            bd = "#4caf50"
                        elif has_debt:
                            bg = "#ffcdd2"
                            bd = "#f44336"
                        else:
                            bg = "#ffffff"
                            bd = "#dddddd"

                        st.markdown(
                            '<div style="background-color:' + bg +
                            '; border:1px solid ' + bd +
                            '; border-radius:6px; padding:6px; margin-bottom:4px;">',
                            unsafe_allow_html=True
                        )

                        row_cols = st.columns([1, 3, 3, 3, 2, 2, 2, 2, 2, 2, 2, 2, 2, 2, 2, 1, 1, 1, 1])
                        row_cols[0].write(str(c.get("position", "")))
                        row_cols[1].write(str(c.get("car_model", "")))
                        row_cols[2].write(str(c.get("client", "")))
                        row_cols[3].write(str(c.get("vin", ""))[:17])
                        row_cols[4].write(fmt_money(amount_val))
                        row_cols[5].write(fmt_money(advance_val))
                        row_cols[6].write(fmt_money(debt_val))
                        row_cols[7].write(str(c.get("delivery_city", "")))
                        row_cols[8].write(date_to_display_safe(c.get("advance_date", "")))
                        row_cols[9].write(str(c.get("payer_type", "")))
                        row_cols[10].write(str(c.get("paid_to", "")))
                        row_cols[11].write(str(c.get("customer", "")))
                        row_cols[12].write(str(c.get("contract_number", "")))
                        cdek_info = str(c.get("cdek_track", ""))
                        if c.get("cdek_date"):
                            cdek_info += " / " + date_to_display_safe(c.get("cdek_date"))
                        row_cols[13].write(cdek_info)
                        row_cols[14].write(fmt_money(nds_val) if nds_val > 0 else "—")

                        if view_mode == "active":
                            issued_label = "Снять" if is_issued else "Выдан"
                            if row_cols[15].button(issued_label, key="btn_issued_" + str(c["id"])):
                                toggle_issued(c["id"], issued_val)
                                log_action(u["login"], role, "toggle_issued",
                                           "рейс " + str(trip_id) + ", поз " + str(c.get("position")))
                                st.rerun()
                        else:
                            row_cols[15].write("—")

                        if row_cols[16].button("Акт", key="btn_act_inline_" + str(c["id"])):
                            st.session_state["show_act_for"] = c["id"]
                            st.rerun()

                        if view_mode == "active":
                            if row_cols[17].button("✏", key="btn_edit_" + str(c["id"])):
                                st.session_state["edit_ship_" + str(c["id"])] = True
                            if can("delete_ship", role):
                                if row_cols[18].button("🗑", key="btn_delship_" + str(c["id"])):
                                    delete_shipment(c["id"])
                                    log_action(u["login"], role, "delete_ship",
                                               "рейс " + str(trip_id) + ", поз " + str(c.get("position")))
                                    st.rerun()

                        st.markdown("</div>", unsafe_allow_html=True)

                    st.markdown("---")
                    sum_cols = st.columns(5)
                    sum_cols[0].markdown("**Сумма:** " + fmt_money(total))
                    sum_cols[1].markdown("**Аванс:** " + fmt_money(total_advance))
                    sum_cols[2].markdown("**Задолженность:** " + fmt_money(total_debt))
                    sum_cols[3].markdown("**НДС 22%:** " + fmt_money(total_nds))
                    sum_cols[4].markdown("**Авто:** " + str(len(cars)) + "/8")

                    if view_mode == "active" and can("archive", role):
                        all_paid = all((to_float(c.get("amount")) - to_float(c.get("advance"))) <= 0 for c in cars)
                        if all_paid:
                            st.success("✅ Все авто оплачены — рейс можно отправить в архив")
                            if st.button("📦 Отправить рейс в архив", key="btn_arch_all_" + str(trip_id)):
                                archive_trip(trip_id)
                                log_action(u["login"], role, "archive_trip_full",
                                           tractor + " " + driver)
                                st.success("Рейс отправлен в архив")
                                st.rerun()
                        else:
                            st.warning("⚠ Есть задолженность по авто — рейс нельзя отправить в архив, пока не оплачен")

                    # формы редактирования авто
                    for c in sorted(cars, key=lambda x: int(to_float(x.get("position")))):
                        if st.session_state.get("edit_ship_" + str(c["id"])):
                            form_key = "editcar_" + str(c["id"])
                            st.markdown("**Редактировать авто (позиция " + str(c.get("position")) + ")**")
                            f = render_shipment_form(form_key, c=c, submit_label="Сохранить изменения")

                            if f["cancel"]:
                                st.session_state.pop("edit_ship_" + str(c["id"]), None)
                                st.rerun()
                            if f["save"]:
                                if not f["client"] and not f["customer"]:
                                    st.error("Заполните хотя бы одно: ФИО клиента или Заказчик")
                                else:
                                    try:
                                        e_dp = parse_date_ui(f["date_pay"])
                                        e_dpa = parse_date_ui(f["advance_date"])
                                        e_dcdek = parse_date_ui(f["cdek_date"])
                                    except ValueError as ex:
                                        st.error(str(ex))
                                    else:
                                        nds_val, no_nds_val = calc_nds(to_float(f["amount"]), f["payer_type"])
                                        update_shipment(
                                            c["id"], f["position"], f["car_model"], f["client"],
                                            to_float(f["amount"]), e_dp, f["paid_to"],
                                            f["delivery_city"], str(f["vin"])[:17],
                                            to_float(f["advance"]), e_dpa,
                                            f["payer_type"], f["customer"], f["contract_number"],
                                            f["cdek_track"], e_dcdek,
                                            nds_val, no_nds_val)
                                        log_action(u["login"], role, "edit_ship",
                                                   "рейс " + str(trip_id) + ", поз " + str(f["position"]))
                                        st.session_state.pop("edit_ship_" + str(c["id"]), None)
                                        st.success("Изменения сохранены")
                                        st.rerun()

                    if view_mode == "active" and can("create_ship", role) and len(cars) < 8:
                        if st.button("Добавить еще авто (позиция " + str(len(cars)+1) + ")",
                                     key="btn_more_addcar_" + str(trip_id)):
                            st.session_state["open_addcar_" + str(trip_id)] = True
                            st.rerun()
                else:
                    st.info("В этом рейсе ещё нет авто.")


def main():
    st.set_page_config(page_title="Учёт рейсов", page_icon="🚛", layout="wide")

    try:
        ensure_sheets_once()
    except Exception as e:
        st.error("Ошибка подключения к Google Sheets: " + type(e).__name__ + ": " + str(e))
        st.stop()

    ensure_first_admin()

    if "user" not in st.session_state:
        login_page()
    else:
        main_page()


if __name__ == "__main__":
    main()

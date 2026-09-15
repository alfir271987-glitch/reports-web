import streamlit as st
import gspread
from oauth2client.service_account import ServiceAccountCredentials
from datetime import datetime
import hashlib

SHEET_ID = "1MHslz3VRowoOLtS_AAQ5h-tFm-fIXBN8noqtyQpmKLw"
CRED_FILE = "gifted-mountain-508410-s3-96f38b5a0f63.json"

SCOPES = [
    "https://spreadsheets.google.com/feeds",
    "https://www.googleapis.com/auth/drive",
]

SHEET_SCHEMAS = {
    "users": ["login", "password_hash", "role", "full_name", "active"],
    "trips": ["id", "tractor_number", "driver", "route",
              "date_departure", "date_return", "created_by", "created_at"],
    "shipments": ["id", "trip_id", "position", "car_model", "client",
                  "amount", "pay_type", "date_pay", "created_by", "created_at"],
    "audit_log": ["id", "ts", "login", "role", "action", "details"],
    "act_log": ["id", "ts", "login", "trip_id", "shipment_id", "client"],
}

ROLES = ["admin", "director", "logist", "dispatcher"]


@st.cache_resource
def get_book():
    # На Streamlit Cloud ключ берётся из секретов.
    # Локально — читается из JSON-файла.
    if "gcp_service_account" in st.secrets:
        creds_dict = dict(st.secrets["gcp_service_account"])
        creds = ServiceAccountCredentials.from_json_keyfile_dict(creds_dict, SCOPES)
    else:
        creds = ServiceAccountCredentials.from_json_keyfile_name(CRED_FILE, SCOPES)
    client = gspread.authorize(creds)
    return client.open_by_key(SHEET_ID)


@st.cache_resource
def get_ws_cached(name):
    return get_book().worksheet(name)


def ensure_sheets_fresh():
    book = get_book()
    existing = {ws.title for ws in book.worksheets()}
    for name, headers in SHEET_SCHEMAS.items():
        if name not in existing:
            ws = book.add_worksheet(title=name, rows=1000, cols=len(headers))
            ws.append_row(headers)


def read_all(name, ttl=15):
    cache_key = f"_cache_read_{name}"
    now = datetime.now().timestamp()
    cached = st.session_state.get(cache_key)
    if cached and (now - cached["ts"]) < ttl:
        return cached["data"]
    ws = get_ws_cached(name)
    data = ws.get_all_records()
    st.session_state[cache_key] = {"ts": now, "data": data}
    return data


def invalidate_cache(name=None):
    if name:
        st.session_state.pop(f"_cache_read_{name}", None)
    else:
        for k in list(st.session_state.keys()):
            if k.startswith("_cache_read_"):
                st.session_state.pop(k, None)


def append_row(name, row_dict):
    ws = get_ws_cached(name)
    headers = SHEET_SCHEMAS[name]
    row = [row_dict.get(h, "") for h in headers]
    ws.append_row(row)
    invalidate_cache(name)


def next_id(name):
    ws = get_ws_cached(name)
    values = ws.col_values(1)
    return len(values)


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
    raise ValueError(f"Неверный формат даты: «{s}». Введите ДД.ММ.ГГГГ (например 12.05.2026)")


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


def is_active(u):
    val = u.get("active")
    return str(val).strip().lower() in ("1", "1.0", "true")


def safe_df(rows):
    if not rows:
        return rows
    clean = []
    for r in rows:
        clean.append({k: ("" if v is None else str(v)) for k, v in r.items()})
    return clean


def ensure_first_admin():
    users = read_all("users", ttl=10)
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
                st.success(f"Админ {login} создан. Войдите.")
                st.rerun()
        st.stop()


def can(action, role):
    rights = {
        "admin":      {"create_trip", "edit_trip", "delete_trip",
                       "create_ship", "edit_ship", "delete_ship",
                       "export", "print_act", "manage_users", "view_log"},
        "director":   {"create_trip", "edit_trip", "delete_trip",
                       "create_ship", "edit_ship", "delete_ship",
                       "export", "print_act"},
        "logist":     {"create_trip", "edit_trip",
                       "create_ship", "edit_ship",
                       "export", "print_act"},
        "dispatcher": {"create_trip", "edit_trip",
                       "create_ship", "edit_ship",
                       "print_act"},
    }
    return action in rights.get(role, set())


def login_page():
    st.title("Учёт рейсов — вход")
    with st.form("login"):
        login = st.text_input("Логин")
        pwd = st.text_input("Пароль", type="password")
        ok = st.form_submit_button("Войти")
    if ok:
        users = read_all("users", ttl=10)
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
    st.header("🛠 Админ-панель")
    tab1, tab2, tab3 = st.tabs(["Пользователи", "Журнал действий", "Журнал печати актов"])

    with tab1:
        st.subheader("Пользователи")
        users = read_all("users", ttl=5)
        for u in users:
            c1, c2, c3, c4, c5 = st.columns([2, 2, 2, 2, 2])
            c1.write(u["login"])
            c2.write(u["role"])
            c3.write(u["full_name"])
            active = is_active(u)
            c4.write("активен" if active else "отключён")
            if c5.button("Отключить" if active else "Включить",
                         key=f"toggle_user_{u['login']}"):
                ws = get_ws_cached("users")
                all_rows = ws.get_all_values()
                for i, row in enumerate(all_rows[1:], start=2):
                    if row[0] == u["login"]:
                        new_val = "0" if active else "1"
                        ws.update_cell(i, 5, new_val)
                        invalidate_cache("users")
                        log_action(st.session_state["user"]["login"],
                                   st.session_state["user"]["role"],
                                   "toggle_user", f"{u['login']} → {new_val}")
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
                           "create_user", f"{new_login} / {new_role}")
                st.success(f"Пользователь {new_login} создан")
                st.rerun()

    with tab2:
        st.subheader("Журнал действий")
        rows = read_all("audit_log", ttl=5)
        rows = list(reversed(rows))[:200]
        if rows:
            st.dataframe(safe_df(rows), use_container_width=True)
        else:
            st.info("Пока пусто")

    with tab3:
        st.subheader("Журнал печати актов")
        rows = read_all("act_log", ttl=5)
        rows = list(reversed(rows))[:200]
        if rows:
            st.dataframe(safe_df(rows), use_container_width=True)
        else:
            st.info("Пока пусто")


def get_trips():
    return read_all("trips", ttl=15)


def get_shipments():
    return read_all("shipments", ttl=15)


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
    })


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


def create_shipment(trip_id, position, car_model, client, amount, pay_type, date_pay, created_by):
    append_row("shipments", {
        "id": next_id("shipments"),
        "trip_id": trip_id,
        "position": position,
        "car_model": car_model,
        "client": client,
        "amount": amount,
        "pay_type": pay_type,
        "date_pay": date_pay,
        "created_by": created_by,
        "created_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    })


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


def render_act_html(trip, shipment):
    tractor = trip.get("tractor_number", "")
    driver = trip.get("driver", "")
    route = trip.get("route", "")
    dep = date_to_display_safe(trip.get("date_departure", ""))
    ret = date_to_display_safe(trip.get("date_return", ""))

    car_model = shipment.get("car_model", "")
    client = shipment.get("client", "")
    amount = int(to_float(shipment.get("amount")))
    pay_type = shipment.get("pay_type", "")
    date_pay = date_to_display_safe(shipment.get("date_pay", ""))

    today = datetime.now().strftime("%d.%m.%Y")

    html = f"""
    <!DOCTYPE html>
    <html lang="ru">
    <head>
    <meta charset="utf-8">
    <title>Акт приёма-передачи</title>
    <style>
        body {{ font-family: "Times New Roman", serif; margin: 30px; font-size: 14px; line-height: 1.6; }}
        h1 {{ text-align: center; font-size: 16px; text-transform: uppercase; margin-bottom: 25px; }}
        .row {{ margin: 8px 0; }}
        .label {{ font-weight: bold; }}
        .sign {{ margin-top: 40px; }}
        .sign-line {{ display: inline-block; border-bottom: 1px solid #000; min-width: 250px; }}
        .small {{ font-size: 12px; }}
    </style>
    </head>
    <body>
    <h1>Акт приёма-передачи транспортного средства</h1>

    <div class="row"><span class="label">Перевозчик:</span> ИП Сагитдинов Максим Наильевич, тел. 8-987-131-00-62</div>
    <div class="row"><span class="label">Заказчик / Получатель:</span> {client}</div>
    <div class="row"><span class="label">Марка автомобиля:</span> {car_model}</div>
    <div class="row"><span class="label">Гос номер тягача:</span> {tractor}</div>
    <div class="row"><span class="label">Водитель:</span> {driver}</div>
    <div class="row"><span class="label">Маршрут:</span> {route}</div>
    <div class="row"><span class="label">Дата выезда:</span> {dep or "____"} &nbsp;&nbsp;
        <span class="label">Дата возврата:</span> {ret or "____"}</div>
    <div class="row"><span class="label">Стоимость перевозки:</span> {amount:,} руб. &nbsp;&nbsp;
        <span class="label">Тип оплаты:</span> {pay_type} &nbsp;&nbsp;
        <span class="label">Дата оплаты:</span> {date_pay or "____"}</div>
    <div class="row"><span class="label">Дата приёма груза:</span> <span class="sign-line"></span>
        &nbsp;&nbsp;<span class="label">Время:</span> <span class="sign-line"></span></div>

    <div class="sign">
        <div class="row"><span class="label">Груз сдал:</span>
            <span class="sign-line"></span> / Сагитдинов М.Н. /</div>
        <div class="row"><span class="label">Груз принял:</span>
            <span class="sign-line"></span> / {client} /</div>
        <div class="row"><span class="label">Дата вручения груза:</span> <span class="sign-line"></span>
            &nbsp;&nbsp;<span class="label">Время:</span> <span class="sign-line"></span></div>
    </div>

    <p class="small" style="margin-top: 30px;">
        При подписании акта приёма-передачи на момент вручения груза Стороны каких-либо претензий
        друг к другу не имеют.
    </p>

    <p class="small" style="text-align: right; margin-top: 20px;">Дата печати: {today}</p>
    </body>
    </html>
    """
    return html


def show_act(trip, shipment):
    u = st.session_state["user"]
    log_act_print(u["login"], trip.get("id"), shipment.get("id"), shipment.get("client"))

    st.info("Нажмите **Ctrl+P** и выберите принтер или **«Сохранить как PDF»**.")
    st.components.v1.html(render_act_html(trip, shipment), height=850, scrolling=True)


def main_page():
    u = st.session_state["user"]
    role = u["role"]

    st.sidebar.markdown(f"**{u['full_name']}**  \nроль: `{role}`")
    if st.sidebar.button("Выйти", key="btn_logout"):
        logout()

    if role == "admin":
        with st.sidebar.expander("🛠 Админ-панель"):
            admin_panel()

    st.title("Учёт рейсов и перевозок")

    trips = get_trips()
    shipments = get_shipments()

    if st.session_state.get("show_act_for"):
        sid = st.session_state["show_act_for"]
        ship = next((s for s in shipments if str(s.get("id")) == str(sid)), None)
        if ship:
            trip = next((t for t in trips if str(t.get("id")) == str(ship.get("trip_id"))), None)
            if trip:
                if st.button("⬅ Назад к списку", key="btn_back_from_act"):
                    st.session_state.pop("show_act_for", None)
                    st.rerun()
                st.subheader("Акт приёма-передачи")
                show_act(trip, ship)
                return
        st.session_state.pop("show_act_for", None)

    col1, col2 = st.columns([3, 1])
    with col2:
        if can("create_trip", role) and st.button(
                "➕ Добавить рейс",
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
                               f"{tractor} {driver} {dep_fmt}")
                    st.session_state.pop("show_new_trip", None)
                    st.success("Рейс добавлен")
                    st.rerun()

    st.subheader("Рейсы")

    if not trips:
        st.info("Пока нет ни одного рейса. Нажмите «Добавить рейс».")
    else:
        for t in reversed(trips):
            trip_id = t.get("id")
            tractor = t.get("tractor_number", "")
            driver = t.get("driver", "")
            route = t.get("route", "")
            dep = date_to_display_safe(t.get("date_departure", ""))
            ret = date_to_display_safe(t.get("date_return", ""))

            cars = [s for s in shipments if str(s.get("trip_id")) == str(trip_id)]
            total = sum(to_float(c.get("amount")) for c in cars)

            header = f"🚛 {tractor} — {driver} — {route} — выезд {dep}"
            if ret:
                header += f" — возврат {ret}"
            with st.expander(f"{header}  |  авто: {len(cars)}/8  |  сумма: {int(total):,}"):

                c1, c2, c3, c4 = st.columns(4)
                if can("create_ship", role):
                    if c1.button("➕ Добавить авто", key=f"btn_show_addcar_{trip_id}"):
                        st.session_state[f"open_addcar_{trip_id}"] = True
                if can("delete_trip", role):
                    if c4.button("🗑 Удалить рейс", key=f"btn_show_deltrip_{trip_id}"):
                        st.session_state[f"open_deltrip_{trip_id}"] = True

                if st.session_state.get(f"open_deltrip_{trip_id}"):
                    st.warning(f"Удалить рейс «{tractor} — {driver}» вместе со всеми авто?")
                    cc1, cc2 = st.columns(2)
                    if cc1.button("Да, удалить", key=f"btn_yes_del_trip_{trip_id}"):
                        delete_trip(trip_id)
                        log_action(u["login"], role, "delete_trip", f"{tractor} {driver}")
                        st.session_state.pop(f"open_deltrip_{trip_id}", None)
                        st.rerun()
                    if cc2.button("Отмена", key=f"btn_no_del_trip_{trip_id}"):
                        st.session_state.pop(f"open_deltrip_{trip_id}", None)
                        st.rerun()

                if st.session_state.get(f"open_addcar_{trip_id}"):
                    with st.form(f"new_car_{trip_id}"):
                        st.markdown("**Добавить автомобиль**")
                        cc1, cc2 = st.columns(2)
                        position = cc1.number_input(
                            "Позиция (1–8)", min_value=1, max_value=8, step=1,
                            value=min(len(cars) + 1, 8))
                        car_model = cc2.text_input("Марка / модель авто")
                        cc3, cc4 = st.columns(2)
                        client = cc3.text_input("ФИО клиента")
                        amount = cc4.text_input("Сумма за перевозку")
                        cc5, cc6 = st.columns(2)
                        pay_type = cc5.selectbox("Тип оплаты", ["нал", "эквайринг"])
                        date_pay = cc6.text_input("Дата оплаты (ДД.ММ.ГГГГ)",
                                                  placeholder="15.05.2026")
                        ok = st.form_submit_button("Сохранить авто")
                        cancel = st.form_submit_button("Отмена")
                    if cancel:
                        st.session_state.pop(f"open_addcar_{trip_id}", None)
                        st.rerun()
                    if ok:
                        if len(cars) >= 8:
                            st.error("На автовозе максимум 8 авто")
                        elif not car_model or not client:
                            st.error("Заполните марку и клиента")
                        else:
                            try:
                                dp = parse_date_ui(date_pay)
                            except ValueError as e:
                                st.error(str(e))
                            else:
                                create_shipment(
                                    trip_id, position, car_model, client,
                                    to_float(amount), pay_type, dp, u["login"])
                                log_action(u["login"], role, "create_ship",
                                           f"рейс {trip_id}, поз {position}, {car_model}")
                                st.session_state.pop(f"open_addcar_{trip_id}", None)
                                st.success("Авто добавлено")
                                st.rerun()

                if cars:
                    rows = []
                    for c in sorted(cars, key=lambda x: int(to_float(x.get("position")))):
                        rows.append({
                            "№": c.get("position"),
                            "Марка / модель": c.get("car_model"),
                            "Клиент": c.get("client"),
                            "Сумма": int(to_float(c.get("amount"))),
                            "Оплата": c.get("pay_type"),
                            "Дата оплаты": date_to_display_safe(c.get("date_pay", "")),
                        })
                    st.dataframe(safe_df(rows), use_container_width=True, hide_index=True)

                    if can("print_act", role):
                        st.markdown("**Печать акта приёма-передачи:**")
                        cols = st.columns(min(len(cars), 4))
                        for idx, c in enumerate(sorted(cars, key=lambda x: int(to_float(x.get("position"))))):
                            if cols[idx % 4].button(
                                    f"🖨 Акт поз. {c.get('position')}",
                                    key=f"btn_act_{c['id']}"):
                                st.session_state["show_act_for"] = c["id"]
                                st.rerun()

                    if can("delete_ship", role):
                        st.markdown("**Удалить авто:**")
                        cols = st.columns(min(len(cars), 4))
                        for idx, c in enumerate(sorted(cars, key=lambda x: int(to_float(x.get("position"))))):
                            if cols[idx % 4].button(
                                    f"Удалить поз. {c.get('position')}",
                                    key=f"btn_delship_{c['id']}"):
                                delete_shipment(c["id"])
                                log_action(u["login"], role, "delete_ship",
                                           f"рейс {trip_id}, поз {c.get('position')}")
                                st.rerun()
                else:
                    st.info("В этом рейсе ещё нет авто.")


def main():
    st.set_page_config(page_title="Учёт рейсов", page_icon="🚛", layout="wide")

    try:
        ensure_sheets_fresh()
    except Exception as e:
        st.error(f"Ошибка подключения к Google Sheets: {type(e).__name__}: {e}")
        st.stop()

    ensure_first_admin()

    if "user" not in st.session_state:
        login_page()
    else:
        main_page()


if __name__ == "__main__":
    main()
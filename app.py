# ============================================================
# ГЛАВНАЯ
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

    # ========== ПЕЧАТЬ: ОБЩИЙ СПИСОК ==========
    if st.session_state.get("show_print_all"):
        if st.button("Назад к списку", key="btn_back_from_print"):
            st.session_state.pop("show_print_all", None)
            st.rerun()
        st.subheader("🖨 Печать списка автомобилей")
        print_rows = []
        active_trips = [t for t in get_trips()
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
                if not is_car_active_on_avtovoz(x):
                    continue
                fin = calculate_financials(x)
                print_rows.append({
                    "tractor": s(t.get("tractor_number", "")),
                    "driver": s(t.get("driver", "")),
                    "route": s(t.get("route", "")),
                    "dep": date_to_display_safe(t.get("date_departure", "")),
                    "position": s(x.get("position", "")),
                    "car_model": s(x.get("car_model", "")),
                    "client": s(x.get("client", "")),
                    "customer": s(x.get("customer", "")),
                    "delivery_city": s(x.get("delivery_city", "")),
                    "debt": fin["debt"],
                })
        if not print_rows:
            st.info("Нет активных авто для печати.")
        else:
            show_print_list(print_rows)
        return

    # ========== АКТ ==========
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

    # ========== ИСТОРИЯ ==========
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
                st.markdown(
                    "**" + s(h.get("ts")) + "** — "
                    + "Рейс #" + s(h.get("from_trip"))
                    + " (поз. " + s(h.get("from_position")) + ")  →  "
                    + "Рейс #" + s(h.get("to_trip"))
                    + " (поз. " + s(h.get("to_position")) + ")"
                    + ("  · " + s(h.get("user_login")) if h.get("user_login") else "")
                )
        return

    # ========== ПЕЧАТЬ: ОДИН РЕЙС ==========
    if st.session_state.get("show_print_trip"):
        rows = st.session_state["show_print_trip"]
        if st.button("Назад к списку", key="btn_back_from_print_trip"):
            st.session_state.pop("show_print_trip", None)
            st.rerun()
        st.subheader("🖨 Печать списка по рейсу")
        show_print_list(rows)
        return

    trips = get_trips()
    shipments = get_shipments()

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
            free_pos = next_free_position(cars)

            has_nds = any(s(x.get("payer_type", "")).strip() == NDS_PAYER
                          for x in cars)

            render_trip_header(trip_id, tractor, driver, route, dep, ret,
                               trip_completed, trip_completed_at,
                               active_count, issued_count,
                               total, total_advance, total_debt, total_nds,
                               has_nds=has_nds,
                               trip_invoice_number=trip_inv_num,
                               trip_invoice_date=trip_inv_date)

            # ----- КНОПКА «ОПЛАТИТЬ ВЕСЬ РЕЙС (БЕЗНАЛ С НДС)» -----
            # Теперь доступна и в активных, и в завершённых рейсах
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

                if cars:
                    if st.button("🖨 Печать списка по рейсу",
                                 key="btn_print_trip_" + s(trip_id),
                                 use_container_width=True):
                        rows = []
                        for x in sorted(cars,
                                        key=lambda z: int(to_float(z.get("position")))):
                            if not is_car_active_on_avtovoz(x):
                                continue
                            fin = calculate_financials(x)
                            rows.append({
                                "tractor": tractor,
                                "driver": driver,
                                "route": route,
                                "dep": dep,
                                "position": s(x.get("position", "")),
                                "car_model": s(x.get("car_model", "")),
                                "client": s(x.get("client", "")),
                                "customer": s(x.get("customer", "")),
                                "delivery_city": s(x.get("delivery_city", "")),
                                "debt": fin["debt"],
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
                        if active_count >= MAX_CARS:
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
                        is_trace = bool(tr_to)

                        if is_trace:
                            bg = "#eeeeee"; bd = "#bdbdbd"
                            status_text = "⚪ Перенесён"
                        elif is_issued_flag and not has_debt:
                            bg = "#c8e6c9"; bd = "#4caf50"; status_text = "🟢 Выдан"
                        elif is_paid_flag and not has_debt:
                            bg = "#fff9c4"; bd = "#ffeb3b"; status_text = "🟡 Оплачен"
                        elif has_debt and is_issued_flag:
                            bg = "#ffcdd2"; bd = "#f44336"; status_text = "🔴 Выдан с долгом"
                        elif has_debt:
                            bg = "#ffcdd2"; bd = "#f44336"; status_text = "🔴 Долг"
                        else:
                            bg = "#ffffff"; bd = "#dddddd"; status_text = "⚪ —"

                        st.markdown(
                            '<div style="background-color:' + bg +
                            '; border:1px solid ' + bd +
                            '; border-radius:6px; padding:8px; margin-bottom:6px; '
                            'font-size:14px; word-wrap:break-word;">',
                            unsafe_allow_html=True)

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

                        if view_mode == "active" and not is_trace:
                            bc1, bc2, bc3, bc4 = st.columns(4)
                            paid_label = "❌ Снять" if is_paid_flag else "✅ Оплачен"
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

                            tc1, tc2, tc3 = st.columns(3)
                            if can("transfer_ship", role):
                                if tc1.button("📦 Перенести",
                                              key="btn_transfer_" + s(x["id"]),
                                              use_container_width=True):
                                    st.session_state["open_transfer_" + s(x["id"])] = True
                            if tc2.button("📜 История",
                                          key="btn_hist_" + s(x["id"]),
                                          use_container_width=True):
                                st.session_state["show_history_for"] = x["id"]
                                st.rerun()
                            if can("delete_ship", role):
                                if tc3.button("🗑 Удалить авто", key="btn_delship_" + s(x["id"]),
                                              use_container_width=True):
                                    delete_shipment(x["id"])
                                    log_action(u["login"], role, "delete_ship",
                                               "рейс " + s(trip_id) + ", поз " + s(x.get("position")))
                                    st.rerun()

                        st.markdown("</div>", unsafe_allow_html=True)

                        if view_mode == "active" and not is_trace and \
                                st.session_state.get("open_transfer_" + s(x["id"])):
                            other_trips = [tt for tt in trips
                                           if not is_archived(tt.get("archived", "0"))
                                           and not is_deleted(tt.get("deleted_at", ""))
                                           and s(tt.get("id")) != s(trip_id)]
                            if not other_trips:
                                st.warning("Нет других активных рейсов для переноса")
                            else:
                                options = []
                                for tt in other_trips:
                                    label = (s(tt.get("tractor_number", ""))
                                             + " — " + s(tt.get("route", ""))
                                             + " — выезд " + date_to_display_safe(tt.get("date_departure", "")))
                                    options.append((label, tt.get("id")))
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

                    st.markdown("---")
                    sum_cols = st.columns(4)
                    sum_cols[0].markdown("**Сумма:** " + fmt_money(total))
                    sum_cols[1].markdown("**Аванс:** " + fmt_money(total_advance))
                    sum_cols[2].markdown("**Задолженность:** " + fmt_money(total_debt))
                    sum_cols[3].markdown("**Авто:** " + s(active_count) + "/" + s(MAX_CARS))
                    if total_nds > 0:
                        st.markdown("**НДС 22%:** " + fmt_money(total_nds))

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

                    if view_mode == "active" and can("create_ship", role) and active_count < MAX_CARS:
                        free_pos_btn = next_free_position(cars) or 1
                        if st.button("Добавить еще авто (позиция " + s(free_pos_btn) + ")",
                                     key="btn_more_addcar_" + s(trip_id),
                                     use_container_width=True):
                            st.session_state["open_addcar_" + s(trip_id)] = True
                            st.rerun()
                else:
                    st.info("В этом рейсе ещё нет авто.")

    # ============================================================
    # ИТОГО
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
        if not is_car_active_on_avtovoz(x):
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

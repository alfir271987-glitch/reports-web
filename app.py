# ============================================================
# ЧАСТЬ 2/2: главная страница и точка входа
# v2.12.6 — «Общая оплата» = сумма реально оплаченного
# ============================================================

def main_page():
    u = st.session_state["user"]
    role = u["role"]

    st.sidebar.markdown("**" + s(u["full_name"]) + "**  \nроль: `" + s(role) + "`")
    if st.sidebar.button("Выйти", key="btn_logout", use_container_width=True):
        logout()

    # v2.12.6: админ-панель доступна admin, director и dispatcher
    if role in ("admin", "director", "dispatcher"):
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
                loading_from = st.text_input(
                    "Погрузка от (пример: СюрЛогистика)",
                    placeholder="СюрЛогистика"
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
                        create_trip(tractor, driver, route, dep_fmt, ret_fmt,
                                    u["login"], loading_from=loading_from)
                        log_action(u["login"], role, "create_trip",
                                   s(tractor) + " " + s(driver) + " " + s(dep_fmt)
                                   + (" | погрузка от " + s(loading_from)
                                      if s(loading_from) else ""))
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
            loading_from = s(t.get("loading_from", ""))
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
                payment_date=payment_date,
                payment_comment=payment_comment,
                payments_info=payments_info,
            )

            open_key = "open_details_" + s(trip_id)
            is_open = st.session_state.get(open_key, False)

            col_hdr, col_btn = st.columns([12, 1])
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

            if view_mode == "completed" and has_nds and can("edit_payment", role):
                pay_key = "show_pay_form_" + s(trip_id)
                if st.button("💳 Изменить дату оплаты и комментарий",
                             key="btn_pay_form_" + s(trip_id)):
                    st.session_state[pay_key] = not st.session_state.get(pay_key, False)
                    st.rerun()

                if st.session_state.get(pay_key):
                    with st.form("pay_form_" + s(trip_id)):
                        st.markdown("**Оплата рейса (безнал с НДС)**")
                        c1, c2 = st.columns(2)
                        pd_val = date_to_display_safe(payment_date) if s(payment_date).strip() else ""
                        new_pd = c1.text_input(
                            "Дата оплаты (ДД.ММ.ГГГГ)",
                            value=pd_val,
                            key="paydate_trip_" + s(trip_id)
                        )
                        new_pc = c2.text_input(
                            "Комментарий (куда/кому ушли деньги)",
                            value=payment_comment,
                            key="paycomment_trip_" + s(trip_id)
                        )
                        save_pay = st.form_submit_button("💾 Сохранить оплату")
                        clear_pay = st.form_submit_button("🗑 Очистить")
                        cancel_pay = st.form_submit_button("Отмена")
                    if cancel_pay:
                        st.session_state.pop(pay_key, None)
                        st.rerun()
                    if clear_pay:
                        update_trip_payment(trip_id, "", "")
                        log_action(u["login"], role, "clear_trip_payment",
                                   s(tractor) + " " + s(driver))
                        st.session_state.pop(pay_key, None)
                        st.success("Данные оплаты очищены")
                        st.rerun()
                    if save_pay:
                        try:
                            pd_fmt = parse_date_ui(new_pd) if s(new_pd).strip() else ""
                        except ValueError as ex:
                            st.error(str(ex))
                        else:
                            update_trip_payment(trip_id, pd_fmt, new_pc)
                            log_action(u["login"], role, "edit_trip_payment",
                                       s(tractor) + " " + s(driver)
                                       + " | дата: " + s(pd_fmt)
                                       + " | " + s(new_pc))
                            st.session_state.pop(pay_key, None)
                            st.success("Данные оплаты сохранены")
                            st.rerun()

            # ---- Разделение счёта ----
            if has_nds and view_mode in ("active", "completed"):
                split_key = "open_split_" + s(trip_id)
                col_split1, col_split2 = st.columns([2, 3])
                with col_split1:
                    if can("split_payment", role):
                        parts_count = payments_info.get("parts_count", 0) if payments_info else 0
                        label = ("🧾 Разделить счёт ✏ (частей: " + s(parts_count) + ")"
                                 if parts_count > 0 else "🧾 Разделить счёт")
                        if st.button(label, key="btn_split_" + s(trip_id),
                                     use_container_width=True):
                            st.session_state[split_key] = not st.session_state.get(split_key, False)
                            st.rerun()

                if st.session_state.get(split_key):
                    nds_debt_val = payments_info.get("nds_debt", 0.0) if payments_info else 0.0
                    nds_total_val = payments_info.get("nds_total", 0.0) if payments_info else 0.0
                    render_trip_split_form(
                        trip_id, t, nds_debt_val, nds_total_val,
                        u["login"], role,
                    )

            # ---- Оплата рейса + счёт ----
            if has_nds and view_mode in ("active", "completed"):
                col_pay, col_inv = st.columns([3, 2])

                if can("edit_ship", role):
                    render_trip_payment_button(
                        trip_id, cars, role, u["login"],
                        trip_label=tractor + " " + driver,
                        container=col_pay,
                    )

                if can("edit_invoice", role):
                    inv_key = "show_inv_form_" + s(trip_id)
                    label = ("✏ Изменить счёт" if (trip_inv_num or trip_inv_date)
                             else "📄 Выставить счёт")
                    if col_inv.button(label, key="btn_inv_" + s(trip_id),
                                      use_container_width=True):
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

            # ---- Кнопки управления рейсом ----
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
                cc1, cc2 = st.columns(2)
                if can("complete_trip", role):
                    if cc1.button("↩ Вернуть в активные",
                                  key="btn_uncomplete_c_" + s(trip_id),
                                  use_container_width=True):
                        uncomplete_trip(trip_id)
                        log_action(u["login"], role, "uncomplete_trip",
                                   s(tractor) + " " + s(driver))
                        st.rerun()
                if can("archive", role):
                    if cc2.button("📦 В архив",
                                  key="btn_arch_c_" + s(trip_id),
                                  use_container_width=True):
                        archive_trip(trip_id)
                        log_action(u["login"], role, "archive_trip",
                                   s(tractor) + " " + s(driver) + " (из завершённых)")
                        st.success("Рейс отправлен в архив")
                        st.rerun()

            # ---- Удалить рейс ----
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

            # ---- Кнопка печати по рейсу ----
            if cars:
                if st.button("🖨 Печать списка по рейсу",
                             key="btn_print_trip_" + s(trip_id),
                             use_container_width=True):
                    rows = []
                    for x in sorted(cars,
                                    key=lambda z: int(to_float(z.get("position")))):
                        if is_effectively_issued(x):
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
                            "loading_from": loading_from,
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

            # ---- Форма завершения рейса ----
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

            # ---- Форма редактирования рейса ----
            if st.session_state.get("open_edittrip_" + s(trip_id)):
                with st.form("edit_trip_" + s(trip_id)):
                    st.markdown("**Редактировать рейс**")
                    ec1, ec2 = st.columns(2)
                    e_tractor = ec1.text_input("Гос номер тягача", value=s(tractor))
                    e_driver = ec2.text_input("Водитель ФИО", value=s(driver))
                    ec3, ec4 = st.columns(2)
                    e_route = ec3.text_input("Маршрут", value=s(route))
                    e_dep = ec4.text_input("Дата выезда (ДД.ММ.ГГГГ)", value=s(dep))
                    e_loading_from = st.text_input(
                        "Погрузка от (пример: СюрЛогистика)",
                        value=s(loading_from),
                        placeholder="СюрЛогистика"
                    )
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
                                        e_dep_fmt, e_ret_fmt,
                                        loading_from=e_loading_from,
                                        payment_date=payment_date,
                                        payment_comment=payment_comment)
                            log_action(u["login"], role, "edit_trip",
                                       s(e_tractor) + " " + s(e_driver) + " " + s(e_dep_fmt)
                                       + (" | погрузка от " + s(e_loading_from)
                                          if s(e_loading_from) else ""))
                            st.session_state.pop("open_edittrip_" + s(trip_id), None)
                            st.success("Рейс обновлён")
                            st.rerun()

            # ---- Опасная зона ----
            if role in ("admin", "director", "dispatcher") and view_mode == "active":
                with st.expander("Опасная зона"):
                    st.warning("Рейс будет помечен как удалённый. Данные сохранятся.")
                    if st.button("🗑 Пометить рейс удалённым",
                                 key="btn_softdel_" + s(trip_id)):
                        soft_delete_trip(trip_id)
                        log_action(u["login"], role, "soft_delete_trip",
                                   s(tractor) + " " + s(driver))
                        st.success("Рейс помечен удалённым")
                        st.rerun()

            # ---- Форма добавления авто ----
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
                            dpa2 = parse_date_ui(f["advance_date_2"])
                        except ValueError as e:
                            st.error(str(e))
                        else:
                            amt_val = money_value(f["amount"])
                            adv1_val = money_value(f["advance"])
                            adv2_val = money_value(f["advance_2"])
                            adv1_val, adv2_val, trimmed = _normalize_advances(
                                amt_val, adv1_val, adv2_val)
                            if trimmed:
                                st.warning(
                                    "Авансы приведены к сумме перевозки: "
                                    "Аванс № 1 = " + fmt_money(adv1_val)
                                    + " ₽, Аванс № 2 = " + fmt_money(adv2_val) + " ₽"
                                )
                            if f["payer_type"] == NDS_PAYER:
                                nds_val = amt_val * NDS_RATE / (1 + NDS_RATE)
                                no_nds_val = amt_val - nds_val
                            else:
                                nds_val = 0.0
                                no_nds_val = amt_val
                            create_shipment(
                                trip_id, f["position"], f["car_model"],
                                f["client"], amt_val, dp, f["paid_to"],
                                f["delivery_city"], f["vin"][:17],
                                adv1_val, dpa, f["payer_type"],
                                f["customer"], f["contract_number"],
                                nds_val, no_nds_val, u["login"],
                                advance_2=adv2_val,
                                advance_date_2=dpa2,
                                paid_to_2=f["paid_to_2"],
                            )
                            if (amt_val > 0.01
                                    and (adv1_val + adv2_val) >= amt_val - 0.01):
                                try:
                                    rows_now = read_all("shipments", fresh=True)
                                    last_ship = None
                                    for r in rows_now:
                                        if (s(r.get("trip_id")) == s(trip_id)
                                                and s(r.get("position")) == s(f["position"])):
                                            last_ship = r
                                    if last_ship is not None:
                                        toggle_issued(s(last_ship.get("id")),
                                                      "0", "0")
                                except Exception as e_iss:
                                    _log_system_error("auto_issue_on_create",
                                                      s(trip_id), e_iss)
                            log_action(u["login"], role, "create_ship",
                                       "рейс " + s(trip_id) + ", поз " + s(f["position"]))
                            st.session_state.pop("open_addcar_" + s(trip_id), None)
                            st.success("Авто добавлено")
                            st.rerun()

            # ---- Список автомобилей в рейсе ----
            if cars:
                st.markdown("**Список автомобилей в рейсе**")

                for x in sorted(cars, key=lambda z: int(to_float(z.get("position")))):
                    f = calculate_financials(x)
                    amount_val = f["amount"]
                    advance_val = f["advance"]
                    advance_1_val = f["advance_1"]
                    advance_2_val = f["advance_2"]
                    debt_val = f["debt"]
                    nds_val = f["nds"]

                    adv2_date_val = date_to_display_safe(x.get("advance_date_2", ""))
                    adv1_date_val = date_to_display_safe(x.get("advance_date", ""))
                    paid_to_2_val = s(x.get("paid_to_2", ""))
                    paid_to_1_val = s(x.get("paid_to", ""))

                    issued_val = s(x.get("issued", "0")).strip()
                    paid_val = s(x.get("paid", "0")).strip()
                    is_paid_flag = check_paid(paid_val)

                    auto_issued = is_advance_covers(x)
                    is_issued_flag = is_effectively_issued(x)
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

                    if is_trace:
                        bg = "#eeeeee"; bd = "#bdbdbd"
                        status_text = "⚪ Перенесён"
                    elif not has_debt and is_issued_flag and auto_issued and not check_issued(issued_val):
                        bg = "#c8e6c9"; bd = "#2e7d32"
                        status_text = "🟢 Выдан (аванс покрыл сумму)"
                    elif not has_debt and is_issued_flag:
                        bg = "#c8e6c9"; bd = "#2e7d32"
                        status_text = "🟢 Выдан"
                    elif not has_debt and is_paid_flag:
                        bg = "#c8e6c9"; bd = "#2e7d32"
                        status_text = "🟢 Оплачен"
                    elif not has_debt and auto_issued:
                        bg = "#c8e6c9"; bd = "#2e7d32"
                        status_text = "🟢 Аванс закрыт"
                    elif not has_debt:
                        bg = "#c8e6c9"; bd = "#2e7d32"
                        status_text = "🟢 Без долга"
                    elif has_debt and is_issued_flag:
                        bg = "#ffcdd2"; bd = "#c62828"
                        status_text = "🔴 Выдан с долгом"
                    else:
                        bg = "#ffcdd2"; bd = "#c62828"
                        status_text = "🔴 Долг"

                    st.markdown(
                        '<div style="background-color:' + bg + '; '
                        'border:2px solid ' + bd + '; '
                        'border-left:10px solid ' + bd + '; '
                        'border-radius:8px; padding:12px 14px; '
                        'margin-bottom:14px; '
                        'box-shadow:0 2px 4px rgba(0,0,0,0.12); '
                        'font-size:14px; word-wrap:break-word;">',
                        unsafe_allow_html=True)

                    if is_trace:
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

                        adv_block_warn = ""
                        if amount_val > 0.01 and advance_val > amount_val + 0.01:
                            adv_block_warn = (
                                '<div style="color:#c62828; font-weight:bold; '
                                'margin-top:4px;">⚠ Превышение: аванс '
                                + fmt_money(advance_val) + ' ₽ больше суммы '
                                + fmt_money(amount_val) + ' ₽</div>'
                            )
                        adv_details_html = (
                            '<div style="background:#fff8e1; '
                            'border-left:4px solid #ffb300; '
                            'padding:6px 10px; margin-top:6px; '
                            'border-radius:4px; font-size:13px; '
                            'word-wrap:break-word;">'
                            '<b>Авансы:</b><br>'
                            'Аванс № 1: <b>' + fmt_money(advance_1_val) + ' ₽</b>'
                            + (' · Дата: ' + adv1_date_val if adv1_date_val else '')
                            + (' · ' + paid_to_1_val if paid_to_1_val else '')
                            + '<br>'
                            'Аванс № 2: <b>' + fmt_money(advance_2_val) + ' ₽</b>'
                            + (' · Дата: ' + adv2_date_val if adv2_date_val else '')
                            + (' · ' + paid_to_2_val if paid_to_2_val else '')
                            + '<br>'
                            '<b>Общий аванс: ' + fmt_money(advance_val) + ' ₽</b>'
                            + ('  <span style="color:#2e7d32; font-weight:bold;">'
                               '✓ покрывает сумму</span>'
                               if auto_issued else '')
                            + adv_block_warn
                            + '</div>'
                        )
                        st.markdown(adv_details_html, unsafe_allow_html=True)

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
                        if s(x.get("payer_type", "")):
                            details.append("Оплата: " + s(x.get("payer_type", "")))
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
                        paid_label = "❌ Снять оплату" if is_paid_flag else "✅ Оплачен"
                        if bc1.button(paid_label, key="btn_paid_" + s(x["id"]),
                                      use_container_width=True):
                            toggle_paid(x["id"], paid_val, amount_val, advance_val)
                            log_action(u["login"], role, "toggle_paid",
                                       "рейс " + s(trip_id) + ", поз " + s(x.get("position")))
                            st.rerun()

                        if check_issued(issued_val):
                            if bc2.button("↩ Снять выдан", key="btn_issued_" + s(x["id"]),
                                          use_container_width=True):
                                toggle_issued(x["id"], issued_val, "0")
                                log_action(u["login"], role, "toggle_issued",
                                           "рейс " + s(trip_id) + ", поз " + s(x.get("position")))
                                st.rerun()
                        else:
                            issued_btn_label = "✅ Выдан"
                            if auto_issued:
                                issued_btn_label = "✅ Выдан (аванс)"
                            if bc2.button(issued_btn_label, key="btn_issued_" + s(x["id"]),
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
                        if check_issued(issued_val):
                            if bc2.button("↩ Снять выдан",
                                          key="btn_issued_c_" + s(x["id"]),
                                          use_container_width=True):
                                toggle_issued(x["id"], issued_val, "0")
                                log_action(u["login"], role, "toggle_issued",
                                           "рейс " + s(trip_id) + ", поз " + s(x.get("position")))
                                st.rerun()
                        else:
                            issued_btn_label = "✅ Выдан"
                            if auto_issued:
                                issued_btn_label = "✅ Выдан (аванс)"
                            if bc2.button(issued_btn_label,
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
                sum_cols[1].markdown("**Аванс (общий):** " + fmt_money(total_advance))
                sum_cols[2].markdown("**Задолженность:** " + fmt_money(total_debt))
                sum_cols[3].markdown("**Авто:** " + s(total_in_route) + "/" + s(MAX_CARS))
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
                                    e_dpa2 = parse_date_ui(fedit["advance_date_2"])
                                except ValueError as ex:
                                    st.error(str(ex))
                                else:
                                    amt_val = money_value(fedit["amount"])
                                    adv1_val = money_value(fedit["advance"])
                                    adv2_val = money_value(fedit["advance_2"])
                                    adv1_val, adv2_val, trimmed = _normalize_advances(
                                        amt_val, adv1_val, adv2_val)
                                    if trimmed:
                                        st.warning(
                                            "Авансы приведены к сумме перевозки: "
                                            "Аванс № 1 = " + fmt_money(adv1_val)
                                            + " ₽, Аванс № 2 = " + fmt_money(adv2_val) + " ₽"
                                        )
                                    if fedit["payer_type"] == NDS_PAYER:
                                        nds_val = amt_val * NDS_RATE / (1 + NDS_RATE)
                                        no_nds_val = amt_val - nds_val
                                    else:
                                        nds_val = 0.0
                                        no_nds_val = amt_val
                                    update_shipment(
                                        x["id"], fedit["position"], fedit["car_model"],
                                        fedit["client"], amt_val, e_dp, fedit["paid_to"],
                                        fedit["delivery_city"], fedit["vin"][:17],
                                        adv1_val, e_dpa, fedit["payer_type"],
                                        fedit["customer"], fedit["contract_number"],
                                        nds_val, no_nds_val,
                                        advance_2=adv2_val,
                                        advance_date_2=e_dpa2,
                                        paid_to_2=fedit["paid_to_2"],
                                    )
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

            st.markdown("---")

    # ============================================================
    # ИТОГИ (v2.12.6: «Общая оплата» = реально оплачено)
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

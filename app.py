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

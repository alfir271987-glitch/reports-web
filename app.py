# ============================================================
# УЧЁТ РЕЙСОВ И ПЕРЕВОЗОК — v2.17.19
# Streamlit + Google Sheets
# v2.17.19: город доставки и ФИО клиента в заголовках автомобилей и платежей.
# v2.17.17: пересчёт по актуальному «Кому», аннулирование старых отметок сдачи
#           при изменении суммы/получателя и обновление полей редактора.
# v2.17.18: аудит пропущенных наличных, архив, поиск по ФИО, защита от VIN-дедупликации.
# v2.17.15: сводка наличных по ФИО получателя (только активные и завершённые рейсы).
# v2.17.10: функционал v2.17.9 сохранён без изменений;
#           убран голубой фон интерфейса; основной фон нейтральный бело-серый;
#           зелёный используется для успешных/оплаченных состояний, красный — для долга/ошибок.
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
    # Только журнал фактической сдачи наличных. Исходные платежи не меняются.
    "cash_handovers": ["id", "ts", "action", "payment_key", "trip_id",
                       "trip_number", "shipment_id", "payment_number",
                       "recipient", "amount", "payment_date",
                       "tractor", "driver", "route", "car_model",
                       "vin", "client", "user_login"],
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

# -*- coding: utf-8 -*-
"""
Слой парсинга и расчёта метрик для стратегического дашборда CEO.

Идея: директор ничего не меняет в своей работе — он по-прежнему выгружает
отчёты из 1С в Excel. Мы лишь фиксируем структуру колонок трёх файлов
(продажи, ДДС, дебиторка) и здесь превращаем их в единый набор метрик
с логикой "светофора" (зелёный/жёлтый/красный).

Если реальная выгрузка 1С отличается по названию колонок — правится
только эта функция-парсер, дашборд (app.py) не трогаем.
"""

from dataclasses import dataclass
from datetime import datetime

import pandas as pd

# ---------------------------------------------------------------------------
# Пороговые значения светофора (Шаг 3 из ТЗ) — меняются под свою компанию
# ---------------------------------------------------------------------------
THRESHOLDS = {
    "plan_fact": {"green": 1.00, "yellow": 0.90},       # % выполнения плана
    "margin_target": 0.20,                               # целевая валовая маржа, 20%
    "margin_yellow_gap": 0.02,                           # жёлтая зона: цель минус 2 п.п.
    "ar_overdue_share": {"yellow": 0.10, "red": 0.25},   # доля просроченной дебиторки
    "opening_cash_balance": 15_000_000,                  # остаток ДС на начало периода данных
}


def traffic_light_plan_fact(ratio: float) -> str:
    """Светофор для план/факт: >=100% зелёный, 90-99% жёлтый, <90% красный."""
    if ratio >= THRESHOLDS["plan_fact"]["green"]:
        return "green"
    if ratio >= THRESHOLDS["plan_fact"]["yellow"]:
        return "yellow"
    return "red"


def traffic_light_margin(margin_pct: float) -> str:
    target = THRESHOLDS["margin_target"]
    gap = THRESHOLDS["margin_yellow_gap"]
    if margin_pct >= target:
        return "green"
    if margin_pct >= target - gap:
        return "yellow"
    return "red"


def traffic_light_cash(net_change: float) -> str:
    if net_change >= 0:
        return "green"
    return "red"


def traffic_light_ar(overdue_share: float) -> str:
    if overdue_share < THRESHOLDS["ar_overdue_share"]["yellow"]:
        return "green"
    if overdue_share < THRESHOLDS["ar_overdue_share"]["red"]:
        return "yellow"
    return "red"


ICONS = {"green": "✅", "yellow": "⚠️", "red": "\U0001f534"}


# ---------------------------------------------------------------------------
# 1. Продажи (план/факт по направлениям)
# ---------------------------------------------------------------------------
def load_sales(path_or_buffer) -> pd.DataFrame:
    """
    Ожидаемые колонки в Excel: Месяц, Направление, План_руб, Факт_руб, Себестоимость_руб
    """
    df = pd.read_excel(path_or_buffer)
    required = {"Месяц", "Направление", "План_руб", "Факт_руб", "Себестоимость_руб"}
    missing = required - set(df.columns)
    if missing:
        raise ValueError(f"В файле продаж не хватает колонок: {missing}")

    df["Месяц"] = pd.to_datetime(df["Месяц"])
    df["Выполнение_%"] = df["Факт_руб"] / df["План_руб"]
    df["Валовая_прибыль_руб"] = df["Факт_руб"] - df["Себестоимость_руб"]
    df["Маржа_%"] = df["Валовая_прибыль_руб"] / df["Факт_руб"]
    return df


def sales_monthly_summary(df: pd.DataFrame) -> pd.DataFrame:
    g = df.groupby("Месяц", as_index=False).agg(
        План_руб=("План_руб", "sum"),
        Факт_руб=("Факт_руб", "sum"),
        Валовая_прибыль_руб=("Валовая_прибыль_руб", "sum"),
    )
    g["Выполнение_%"] = g["Факт_руб"] / g["План_руб"]
    g["Маржа_%"] = g["Валовая_прибыль_руб"] / g["Факт_руб"]
    return g.sort_values("Месяц")


# ---------------------------------------------------------------------------
# 2. ДДС (водопад по видам деятельности)
# ---------------------------------------------------------------------------
def load_cash_flow(path_or_buffer) -> pd.DataFrame:
    """
    Ожидаемые колонки: Дата, Вид_деятельности, Статья, Тип (Приток/Отток), Сумма_руб
    """
    df = pd.read_excel(path_or_buffer)
    required = {"Дата", "Вид_деятельности", "Статья", "Тип", "Сумма_руб"}
    missing = required - set(df.columns)
    if missing:
        raise ValueError(f"В файле ДДС не хватает колонок: {missing}")
    df["Дата"] = pd.to_datetime(df["Дата"])
    df["Знак"] = df["Тип"].map({"Приток": 1, "Отток": -1})
    if df["Знак"].isna().any():
        raise ValueError("Колонка 'Тип' должна содержать только 'Приток' или 'Отток'")
    df["Сумма_со_знаком"] = df["Сумма_руб"] * df["Знак"]
    return df


def cash_flow_waterfall(df: pd.DataFrame, opening_balance: float | None = None) -> pd.DataFrame:
    """
    Возвращает помесячный водопад: остаток на начало, нетто по 3 видам
    деятельности, остаток на конец (нарастающим итогом).
    """
    if opening_balance is None:
        opening_balance = THRESHOLDS["opening_cash_balance"]

    pivot = df.pivot_table(
        index="Дата", columns="Вид_деятельности", values="Сумма_со_знаком", aggfunc="sum"
    ).fillna(0).sort_index()

    for col in ["Операционная", "Инвестиционная", "Финансовая"]:
        if col not in pivot.columns:
            pivot[col] = 0.0

    pivot["Нетто_период"] = pivot["Операционная"] + pivot["Инвестиционная"] + pivot["Финансовая"]
    pivot["Остаток_на_начало"] = opening_balance + pivot["Нетто_период"].cumsum().shift(1).fillna(0)
    pivot["Остаток_на_конец"] = pivot["Остаток_на_начало"] + pivot["Нетто_период"]
    pivot = pivot.reset_index().rename(columns={"Дата": "Месяц"})
    return pivot


# ---------------------------------------------------------------------------
# 3. Дебиторская задолженность (старение долга)
# ---------------------------------------------------------------------------
AR_BUCKETS = ["Текущая (в срок)", "1-30 дней", "31-60 дней", "61-90 дней", "90+ дней"]


def load_receivables(path_or_buffer) -> pd.DataFrame:
    """
    Ожидаемые колонки: Клиент, Дата_отгрузки, Плановая_дата_оплаты, Сумма_руб
    """
    df = pd.read_excel(path_or_buffer)
    required = {"Клиент", "Дата_отгрузки", "Плановая_дата_оплаты", "Сумма_руб"}
    missing = required - set(df.columns)
    if missing:
        raise ValueError(f"В файле дебиторки не хватает колонок: {missing}")
    df["Дата_отгрузки"] = pd.to_datetime(df["Дата_отгрузки"])
    df["Плановая_дата_оплаты"] = pd.to_datetime(df["Плановая_дата_оплаты"])
    return df


def receivables_aging(df: pd.DataFrame, report_date: datetime | None = None) -> pd.DataFrame:
    if report_date is None:
        report_date = pd.Timestamp.today().normalize()
    else:
        report_date = pd.Timestamp(report_date)

    out = df.copy()
    out["Дней_просрочки"] = (report_date - out["Плановая_дата_оплаты"]).dt.days

    def bucket(days):
        if days < 0:
            return AR_BUCKETS[0]
        if days <= 30:
            return AR_BUCKETS[1]
        if days <= 60:
            return AR_BUCKETS[2]
        if days <= 90:
            return AR_BUCKETS[3]
        return AR_BUCKETS[4]

    out["Бакет"] = out["Дней_просрочки"].apply(bucket)
    return out


def receivables_summary(df_aged: pd.DataFrame) -> pd.DataFrame:
    g = df_aged.groupby("Бакет", as_index=False)["Сумма_руб"].sum()
    g["Бакет"] = pd.Categorical(g["Бакет"], categories=AR_BUCKETS, ordered=True)
    return g.sort_values("Бакет")


# ---------------------------------------------------------------------------
# Сводный KPI-набор для верхнего яруса дашборда
# ---------------------------------------------------------------------------
@dataclass
class KPI:
    label: str
    value: str
    delta: str
    light: str


def build_kpi_cards(sales_df: pd.DataFrame, cash_wf: pd.DataFrame, ar_aged: pd.DataFrame) -> list[KPI]:
    monthly = sales_monthly_summary(sales_df)
    last = monthly.iloc[-1]
    prev = monthly.iloc[-2] if len(monthly) > 1 else last

    revenue_ratio = last["Выполнение_%"]
    revenue_growth = (last["Факт_руб"] / prev["Факт_руб"] - 1) if prev["Факт_руб"] else 0

    margin_pct = last["Маржа_%"]
    margin_delta = margin_pct - prev["Маржа_%"]

    cash_last = cash_wf.iloc[-1]
    cash_change = cash_last["Нетто_период"]

    ar_total = ar_aged["Сумма_руб"].sum()
    ar_overdue = ar_aged.loc[ar_aged["Бакет"] != AR_BUCKETS[0], "Сумма_руб"].sum()
    ar_overdue_share = ar_overdue / ar_total if ar_total else 0

    cards = [
        KPI(
            label="Выручка (посл. месяц)",
            value=f"{last['Факт_руб']:,.0f} ₽".replace(",", " "),
            delta=f"{revenue_ratio*100:.0f}% от плана, {revenue_growth*100:+.1f}% к пред. мес.",
            light=traffic_light_plan_fact(revenue_ratio),
        ),
        KPI(
            label="Валовая маржа",
            value=f"{margin_pct*100:.1f}%",
            delta=f"{margin_delta*100:+.1f} п.п. к пред. мес.",
            light=traffic_light_margin(margin_pct),
        ),
        KPI(
            label="Остаток денежных средств",
            value=f"{cash_last['Остаток_на_конец']:,.0f} ₽".replace(",", " "),
            delta=f"{cash_change:+,.0f} ₽ за месяц".replace(",", " "),
            light=traffic_light_cash(cash_change),
        ),
        KPI(
            label="Дебиторская задолженность",
            value=f"{ar_total:,.0f} ₽".replace(",", " "),
            delta=f"просрочено {ar_overdue_share*100:.0f}%",
            light=traffic_light_ar(ar_overdue_share),
        ),
    ]
    return cards

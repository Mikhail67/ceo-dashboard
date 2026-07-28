# -*- coding: utf-8 -*-
"""
Стратегический дашборд CEO — MVP на Streamlit.

Источник данных: три Excel-файла, которые директор выгружает из 1С
руками (продажи план/факт, ДДС, дебиторка). Никаких сводных таблиц
и правок в 1С не требуется — просто три файла со стабильной структурой
колонок (см. sample_data/ как образец).

Запуск:
    pip install -r requirements.txt
    streamlit run app.py
"""

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from data_pipeline import (
    AR_BUCKETS,
    ICONS,
    build_kpi_cards,
    cash_flow_waterfall,
    load_cash_flow,
    load_receivables,
    load_sales,
    receivables_aging,
    receivables_summary,
    sales_monthly_summary,
    traffic_light_plan_fact,
)

st.set_page_config(page_title="Стратегический дашборд CEO", layout="wide")

LIGHT_COLOR = {"green": "#1a9850", "yellow": "#e8a33d", "red": "#d73027"}

st.markdown(
    """
    <style>
    .kpi-card {
        border-left: 6px solid #ccc;
        border-radius: 6px;
        background: #fafafa;
        padding: 12px 16px;
        margin-bottom: 8px;
    }
    .kpi-label { font-size: 13px; color: #555; margin-bottom: 2px; }
    .kpi-value { font-size: 26px; font-weight: 700; margin-bottom: 2px; }
    .kpi-delta { font-size: 13px; color: #444; }
    </style>
    """,
    unsafe_allow_html=True,
)

# ---------------------------------------------------------------------------
# Sidebar: источники данных
# ---------------------------------------------------------------------------
st.sidebar.header("Источники данных")
st.sidebar.caption(
    "Загрузите свои выгрузки из 1С в формате трёх шаблонов. "
    "Если файл не загружен — используются демо-данные."
)

sales_file = st.sidebar.file_uploader("Продажи (план/факт)", type=["xlsx"], key="sales")
cash_file = st.sidebar.file_uploader("ДДС", type=["xlsx"], key="cash")
ar_file = st.sidebar.file_uploader("Дебиторская задолженность", type=["xlsx"], key="ar")

report_date = st.sidebar.date_input("Дата отчёта (для расчёта старения дебиторки)", value=pd.Timestamp("2026-07-28"))

opening_balance = st.sidebar.number_input(
    "Остаток ДС на начало периода данных, ₽",
    min_value=0,
    value=15_000_000,
    step=500_000,
)

sales_path = sales_file if sales_file is not None else "sample_data/sales_plan_fact.xlsx"
cash_path = cash_file if cash_file is not None else "sample_data/cash_flow.xlsx"
ar_path = ar_file if ar_file is not None else "sample_data/receivables.xlsx"

# ---------------------------------------------------------------------------
# Загрузка и расчёт
# ---------------------------------------------------------------------------
try:
    sales_df = load_sales(sales_path)
    monthly = sales_monthly_summary(sales_df)

    cash_df = load_cash_flow(cash_path)
    waterfall_df = cash_flow_waterfall(cash_df, opening_balance=opening_balance)

    ar_df = load_receivables(ar_path)
    ar_aged = receivables_aging(ar_df, report_date=report_date)
    ar_summary = receivables_summary(ar_aged)
except ValueError as e:
    st.error(f"Ошибка в структуре файла: {e}")
    st.stop()

# ---------------------------------------------------------------------------
# Верхний ярус: KPI-карточки
# ---------------------------------------------------------------------------
st.title("Стратегический дашборд CEO")
st.caption(f"Данные на {pd.Timestamp(report_date).strftime('%d.%m.%Y')}")

kpis = build_kpi_cards(sales_df, waterfall_df, ar_aged)
cols = st.columns(len(kpis))
for col, kpi in zip(cols, kpis):
    color = LIGHT_COLOR[kpi.light]
    icon = ICONS[kpi.light]
    col.markdown(
        f"""
        <div class="kpi-card" style="border-left-color:{color};">
            <div class="kpi-label">{icon} {kpi.label}</div>
            <div class="kpi-value">{kpi.value}</div>
            <div class="kpi-delta">{kpi.delta}</div>
        </div>
        """,
        unsafe_allow_html=True,
    )

st.divider()

# ---------------------------------------------------------------------------
# Средний ярус: план-факт по направлениям + тренд выполнения + водопад ДДС
# ---------------------------------------------------------------------------
col_left, col_right = st.columns(2)

with col_left:
    st.subheader("План/факт по направлениям (последний месяц)")
    last_month = sales_df["Месяц"].max()
    last_slice = sales_df[sales_df["Месяц"] == last_month]

    fig = go.Figure()
    fig.add_bar(name="План", x=last_slice["Направление"], y=last_slice["План_руб"], marker_color="#c9c9c9")
    fig.add_bar(
        name="Факт",
        x=last_slice["Направление"],
        y=last_slice["Факт_руб"],
        marker_color=[
            LIGHT_COLOR[traffic_light_plan_fact(r)] for r in (last_slice["Факт_руб"] / last_slice["План_руб"])
        ],
    )
    fig.update_layout(barmode="group", height=380, margin=dict(t=10, b=10))
    st.plotly_chart(fig, width="stretch")

    st.subheader("Тренд выполнения плана, %")
    fig2 = go.Figure()
    fig2.add_scatter(x=monthly["Месяц"], y=monthly["Выполнение_%"] * 100, mode="lines+markers", name="Выполнение плана")
    fig2.add_hline(y=100, line_dash="dash", line_color="#888")
    fig2.update_layout(height=280, margin=dict(t=10, b=10), yaxis_title="%")
    st.plotly_chart(fig2, width="stretch")

with col_right:
    st.subheader("Водопад движения денежных средств (последний месяц)")
    last_wf = waterfall_df.iloc[-1]
    fig3 = go.Figure(
        go.Waterfall(
            orientation="v",
            measure=["absolute", "relative", "relative", "relative", "total"],
            x=["Остаток на начало", "Операционная", "Инвестиционная", "Финансовая", "Остаток на конец"],
            y=[
                last_wf["Остаток_на_начало"],
                last_wf["Операционная"],
                last_wf["Инвестиционная"],
                last_wf["Финансовая"],
                last_wf["Остаток_на_конец"],
            ],
            decreasing={"marker": {"color": "#d73027"}},
            increasing={"marker": {"color": "#1a9850"}},
            totals={"marker": {"color": "#4575b4"}},
        )
    )
    fig3.update_layout(height=380, margin=dict(t=10, b=10))
    st.plotly_chart(fig3, width="stretch")

    st.subheader("Дебиторская задолженность по срокам")
    colors_ar = ["#1a9850", "#a6d96a", "#e8a33d", "#fc8d59", "#d73027"]
    fig4 = go.Figure(
        go.Bar(x=ar_summary["Бакет"].astype(str), y=ar_summary["Сумма_руб"], marker_color=colors_ar)
    )
    fig4.update_layout(height=280, margin=dict(t=10, b=10), yaxis_title="₽")
    st.plotly_chart(fig4, width="stretch")

st.divider()

# ---------------------------------------------------------------------------
# Нижний ярус: детализация (drill-down)
# ---------------------------------------------------------------------------
st.subheader("Детализация")

tab1, tab2 = st.tabs(["Продажи по направлениям (все месяцы)", "Дебиторка по клиентам"])

with tab1:
    show_df = sales_df.copy()
    show_df["Месяц"] = show_df["Месяц"].dt.strftime("%m.%Y")
    show_df["Выполнение_%"] = (show_df["Выполнение_%"] * 100).round(1)
    show_df["Маржа_%"] = (show_df["Маржа_%"] * 100).round(1)
    st.dataframe(
        show_df[["Месяц", "Направление", "План_руб", "Факт_руб", "Выполнение_%", "Маржа_%"]],
        width="stretch",
        hide_index=True,
    )

with tab2:
    bucket_filter = st.multiselect("Фильтр по сроку просрочки", AR_BUCKETS, default=AR_BUCKETS)
    filtered = ar_aged[ar_aged["Бакет"].isin(bucket_filter)].sort_values("Дней_просрочки", ascending=False)
    st.dataframe(
        filtered[["Клиент", "Дата_отгрузки", "Плановая_дата_оплаты", "Сумма_руб", "Дней_просрочки", "Бакет"]],
        width="stretch",
        hide_index=True,
    )
    st.caption(f"Итого по фильтру: {filtered['Сумма_руб'].sum():,.0f} ₽".replace(",", " "))

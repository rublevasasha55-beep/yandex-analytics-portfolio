"""
Самодостаточный интерактивный Dash-дашборд.

Пресеты селекторов (подписки по умолчанию, диапазоны дат) читаются из
dashboard_config.ini, если он есть рядом с файлом — иначе используются
встроенные значения по умолчанию.

Запуск: python3 dashboard_app.py, затем открыть http://127.0.0.1:8051
"""

import configparser
import os
import sqlite3

import dash
import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots
from dash import ctx, dcc, html, no_update
from dash.dependencies import Input, Output

VITRINE_DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), './database/vitrine.db')
CONFIG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'dashboard_config.ini')

ALL_SUBSCRIPTIONS = ('pro', 'lite')
SUBSCRIPTION_LABELS = {'pro': 'Pro', 'lite': 'Lite'}

BLUE = '#2a78d6'
ORANGE = '#eb6834'
AQUA = '#1baf7a'
YELLOW = '#eda100'

INK = '#0b0b0b'
INK_SECONDARY = '#52514e'
MUTED = '#898781'
GRID = '#e1e0d9'
SURFACE = '#fcfcfb'

COLOR_TOPUP = BLUE
COLOR_DEBIT = ORANGE
COLOR_TERTIARY = AQUA
COLOR_QUATERNARY = YELLOW

SUBPLOT_TITLES = [
    'DAU', 'Новые регистрации', 'Ретеншен когорты, %',
    'Сумма пополнений vs списаний, ₽', 'Средняя сумма на клиента в день, ₽', 'Распределение списаний: товары vs услуги, ₽',
    'Распределение товаров', 'Доходимость: запрошено vs оплачено', 'Итог за период',
]

PRODUCT_TITLES = {
    'smart_bulb': 'Умная лампа Wi-Fi',
    'smart_speaker': 'Умная колонка с ассистентом',
    'smart_plug': 'Умная розетка Wi-Fi',
    'robot_vacuum': 'Робот-пылесос',
    'video_doorbell': 'Умный видеодомофон',
    'smoke_sensor': 'Датчик дыма и температуры',
    'smart_lock': 'Умный дверной замок',
    'smart_thermostat': 'Wi-Fi термостат отопления',
    'outdoor_camera': 'Камера видеонаблюдения уличная',
    'smart_switch': 'Умный выключатель',
}

# Категории доходимости (name -> русское название) — см. vitrine/service_intent_dayly.py,
# CATEGORIES. Намерение берётся из events, факт оплаты — из transactions.
SERVICE_INTENT_TITLES = {
    'products': 'Товары',
    'express_delivery': 'Экспресс-доставка',
    'gift_wrapping': 'Подарочная упаковка',
    'extended_warranty': 'Продление гарантии',
}

def load_config(path=CONFIG_PATH):
    defaults = {
        'subscriptions': 'pro,lite',
        'date_presets': '30,90,180,365',
        'default_date_range_days': '90',
    }
    parser = configparser.ConfigParser()
    section = {}
    if os.path.exists(path):
        parser.read(path)
        if parser.has_section('selectors'):
            section = parser['selectors']

    subscriptions = [s.strip() for s in section.get('subscriptions', defaults['subscriptions']).split(',') if s.strip()]
    subscriptions = [s for s in subscriptions if s in ALL_SUBSCRIPTIONS] or list(ALL_SUBSCRIPTIONS)

    date_presets = [int(x.strip()) for x in section.get('date_presets', defaults['date_presets']).split(',') if x.strip()]
    date_presets = date_presets or [30, 90, 180, 365]

    default_days_raw = section.get('default_date_range_days', defaults['default_date_range_days']).strip()
    default_date_range_days = int(default_days_raw) if default_days_raw else None

    return {
        'subscriptions': subscriptions,
        'date_presets': date_presets,
        'default_date_range_days': default_date_range_days,
    }


CONFIG = load_config()


# ---------------------------------------------------------------------------
# Загрузка витрин
# ---------------------------------------------------------------------------

def load_vitrines(db_path=VITRINE_DB_PATH):
    with sqlite3.connect(db_path) as conn:
        cohort_dau = pd.read_sql_query("SELECT * FROM cohort_dau", conn, parse_dates=['date'])
        funnel = pd.read_sql_query("SELECT * FROM funnel", conn, parse_dates=['registration_date'])
        financial_daily = pd.read_sql_query("SELECT * FROM financial_daily", conn, parse_dates=['date'])
        debit_items = pd.read_sql_query("SELECT * FROM debit_items", conn, parse_dates=['date'])
        service_intent = pd.read_sql_query("SELECT * FROM service_intent_daily", conn, parse_dates=['date'])
    return cohort_dau, funnel, financial_daily, debit_items, service_intent


COHORT_DAU_RAW, FUNNEL_RAW, FINANCIAL_DAILY_RAW, DEBIT_ITEMS_RAW, SERVICE_INTENT_RAW = load_vitrines()

GLOBAL_MIN_DATE = min(COHORT_DAU_RAW['date'].min(), FUNNEL_RAW['registration_date'].min())
GLOBAL_MAX_DATE = max(COHORT_DAU_RAW['date'].max(), FUNNEL_RAW['registration_date'].max())

if CONFIG['default_date_range_days']:
    DEFAULT_END_DATE = GLOBAL_MAX_DATE
    DEFAULT_START_DATE = max(GLOBAL_MIN_DATE, DEFAULT_END_DATE - pd.Timedelta(days=CONFIG['default_date_range_days'] - 1))
else:
    DEFAULT_START_DATE, DEFAULT_END_DATE = GLOBAL_MIN_DATE, GLOBAL_MAX_DATE


# ---------------------------------------------------------------------------
# Агрегация: сегменты (pro/lite) на лету из дневных витрин
# ---------------------------------------------------------------------------

def filter_date_range(df, date_col, start, end):
    return df[(df[date_col] >= start) & (df[date_col] <= end)]


def sum_segments(df, metric, segments, min_count=0):
    """Складывает {metric}_{segment} колонки по выбранным сегментам (pro+lite = все)."""
    cols = [f'{metric}_{s}' for s in segments if f'{metric}_{s}' in df.columns]
    if not cols:
        return pd.Series(0, index=df.index)
    return df[cols].sum(axis=1, min_count=min_count)


def safe_pct(num, denom):
    return num / denom * 100 if denom else None


def safe_ratio(num, denom):
    return num / denom if denom else None


def compute_cohort_dau(segments, start, end):
    df = filter_date_range(COHORT_DAU_RAW, 'date', start, end).copy()
    df['new_registrations'] = sum_segments(df, 'new_registrations', segments)
    df['active_users'] = sum_segments(df, 'active_users', segments)
    df['monetized_users'] = sum_segments(df, 'monetized_users', segments)
    df['purchasing_users'] = sum_segments(df, 'purchasing_users', segments)
    df['purchasing_and_service_users'] = sum_segments(df, 'purchasing_and_service_users', segments)
    for offset in (3, 7, 30):
        df[f'retained_d{offset}'] = sum_segments(df, f'retained_d{offset}', segments, min_count=1)
        df[f'retention_d{offset}_pct'] = df.apply(
            lambda r, offset=offset: safe_pct(r[f'retained_d{offset}'], r['new_registrations']), axis=1,
        )
    return df


def compute_funnel(segments, start, end):
    df = filter_date_range(FUNNEL_RAW, 'registration_date', start, end).copy()
    df['users_registered'] = sum_segments(df, 'users_registered', segments)
    df['feature_users'] = sum_segments(df, 'feature_users', segments)
    df['topup_users'] = sum_segments(df, 'topup_users', segments)
    df['purchase_users'] = sum_segments(df, 'purchase_users', segments)
    df['purchase_and_service_users'] = sum_segments(df, 'purchase_and_service_users', segments)
    df['d1_active'] = sum_segments(df, 'd1_active', segments)

    df['used_feature_pct'] = df.apply(lambda r: safe_pct(r['feature_users'], r['users_registered']), axis=1)
    df['made_topup_pct'] = df.apply(lambda r: safe_pct(r['topup_users'], r['users_registered']), axis=1)
    df['dau_d1_pct'] = df.apply(lambda r: safe_pct(r['d1_active'], r['users_registered']), axis=1)
    return df


def compute_financial_daily(segments, start, end):
    df = filter_date_range(FINANCIAL_DAILY_RAW, 'date', start, end).copy()
    df['topup_count'] = sum_segments(df, 'topup_count', segments)
    df['topup_sum'] = sum_segments(df, 'topup_sum', segments)
    df['debit_count'] = sum_segments(df, 'debit_count', segments)
    df['debit_sum'] = sum_segments(df, 'debit_sum', segments)
    df['active_paying_users'] = sum_segments(df, 'active_paying_users', segments)
    df['debit_paying_users'] = sum_segments(df, 'debit_paying_users', segments)
    df['debit_sum_products'] = sum_segments(df, 'debit_sum_products', segments)
    df['debit_sum_services'] = sum_segments(df, 'debit_sum_services', segments)

    df['avg_topup_per_user'] = df.apply(
        lambda r: safe_ratio(r['topup_sum'], r['active_paying_users']), axis=1,
    )
    df['avg_debit_per_user'] = df.apply(
        lambda r: safe_ratio(r['debit_sum'], r['debit_paying_users']), axis=1,
    )
    return df


def compute_item_distribution(segments, start, end, titles):
    """Кол-во списаний по каждому наименованию из titles (товар или допуслуга)."""
    df = filter_date_range(DEBIT_ITEMS_RAW, 'date', start, end)
    df = df[df['subscription_type'].isin(segments) & df['service'].isin(titles)]
    counts = df['service'].value_counts()
    result = [(titles[name], int(counts.get(name, 0))) for name in titles]
    result.sort(key=lambda item: item[1], reverse=True)
    return result


def compute_service_intent(segments, start, end):
    """
    Доходимость от запроса до оплаты за период, по каждой категории.

    Returns:
        list of (название, запрошено, оплачено, доходимость в %)
    """
    df = filter_date_range(SERVICE_INTENT_RAW, 'date', start, end)
    result = []
    for category, title in SERVICE_INTENT_TITLES.items():
        requested = int(sum_segments(df, f'{category}_requested', segments).sum())
        paid = int(sum_segments(df, f'{category}_paid', segments).sum())
        result.append((title, requested, paid, safe_pct(paid, requested)))
    result.sort(key=lambda item: item[1], reverse=True)
    return result


def compute_dashboard(start_date, end_date, subscription_types):
    segments = list(subscription_types) if subscription_types else list(ALL_SUBSCRIPTIONS)
    start = pd.Timestamp(start_date)
    end = pd.Timestamp(end_date)

    cohort_dau = compute_cohort_dau(segments, start, end)
    funnel = compute_funnel(segments, start, end)
    financial_daily = compute_financial_daily(segments, start, end)
    product_distribution = compute_item_distribution(segments, start, end, PRODUCT_TITLES)
    service_intent = compute_service_intent(segments, start, end)
    return cohort_dau, funnel, financial_daily, product_distribution, service_intent


# ---------------------------------------------------------------------------
# Построение фигуры
# ---------------------------------------------------------------------------

def add_line(fig, row, col, x, y, name, color, hover_suffix=''):
    fig.add_trace(
        go.Scatter(
            x=x, y=y, mode='lines', name=name,
            line=dict(color=color, width=2),
            hovertemplate=f'%{{x|%Y-%m-%d}}<br>{name}: %{{y:,.1f}}{hover_suffix}<extra></extra>',
        ),
        row=row, col=col,
    )


def legend_layout_for(fig, idx):
    """Возвращает имя legend-слота ('legend', 'legend2', ...) и его позицию в углу subplot idx."""
    xaxis_key = 'xaxis' if idx == 1 else f'xaxis{idx}'
    yaxis_key = 'yaxis' if idx == 1 else f'yaxis{idx}'
    x_domain = fig.layout[xaxis_key].domain
    y_domain = fig.layout[yaxis_key].domain
    legend_name = 'legend' if idx == 1 else f'legend{idx}'
    position = dict(
        x=x_domain[0] + 0.01, y=y_domain[1] - 0.01,
        xanchor='left', yanchor='top',
        bgcolor='rgba(252,252,251,0.7)',
        font=dict(size=10, color=INK_SECONDARY),
    )
    return legend_name, position


def assign_legend(fig, row, col, idx, n_traces):
    """Назначает трейсам последнего subplot-а отдельный legend-слот (только если >=2 серии)."""
    if n_traces < 2:
        return None, None
    legend_name, position = legend_layout_for(fig, idx)
    traces = fig.select_traces(row=row, col=col)
    for trace in traces:
        trace.update(legend=legend_name, showlegend=True)
    return legend_name, position


def add_distribution_bar(fig, row, col, distribution, color, hover_unit):
    names = [name for name, _ in distribution]
    counts = [count for _, count in distribution]
    fig.add_trace(
        go.Bar(
            x=names, y=counts,
            marker_color=color,
            text=[f'{c:,}' for c in counts],
            textposition='outside',
            hovertemplate=f'%{{x}}: %{{y:,}} {hover_unit}<extra></extra>',
            showlegend=False,
        ),
        row=row, col=col,
    )
    fig.update_xaxes(tickangle=-25, row=row, col=col)


def add_intent_bars(fig, row, col, service_intent):
    """Сгруппированный бар «запрошено vs оплачено» с подписью доходимости над оплатой."""
    names = [name for name, _, _, _ in service_intent]
    requested = [value for _, value, _, _ in service_intent]
    paid = [value for _, _, value, _ in service_intent]
    pct_labels = ['' if pct is None else f'{pct:.0f}%' for _, _, _, pct in service_intent]

    fig.add_trace(
        go.Bar(
            x=names, y=requested, name='Запрошено',
            marker_color=COLOR_TOPUP,
            hovertemplate='%{x}<br>Запрошено: %{y:,}<extra></extra>',
        ),
        row=row, col=col,
    )
    fig.add_trace(
        go.Bar(
            x=names, y=paid, name='Оплачено',
            marker_color=COLOR_DEBIT,
            text=pct_labels, textposition='outside',
            hovertemplate='%{x}<br>Оплачено: %{y:,}<extra></extra>',
        ),
        row=row, col=col,
    )
    fig.update_xaxes(tickangle=-25, row=row, col=col)


def build_figure(cohort_dau, funnel, financial_daily, product_distribution, service_intent):
    fig = make_subplots(
        rows=3, cols=3,
        subplot_titles=SUBPLOT_TITLES,
        horizontal_spacing=0.06, vertical_spacing=0.12,
    )

    legends = {}

    # (1,1) DAU
    add_line(fig, 1, 1, cohort_dau['date'], cohort_dau['active_users'], 'Уникальные клиенты', COLOR_TOPUP)
    add_line(fig, 1, 1, cohort_dau['date'], cohort_dau['monetized_users'], 'Пополнили счёт', COLOR_DEBIT)
    add_line(fig, 1, 1, cohort_dau['date'], cohort_dau['purchasing_users'], 'Купили товар', COLOR_TERTIARY)
    add_line(fig, 1, 1, cohort_dau['date'], cohort_dau['purchasing_and_service_users'], 'Купили товар и услугу', COLOR_QUATERNARY)
    legends[1] = assign_legend(fig, 1, 1, 1, 4)

    # (1,2) Новые регистрации
    add_line(fig, 1, 2, cohort_dau['date'], cohort_dau['new_registrations'], 'Новые регистрации', COLOR_TOPUP)
    add_line(fig, 1, 2, funnel['registration_date'], funnel['topup_users'], 'Пополнили счёт', COLOR_DEBIT)
    add_line(fig, 1, 2, funnel['registration_date'], funnel['purchase_users'], 'Купили товар', COLOR_TERTIARY)
    add_line(fig, 1, 2, funnel['registration_date'], funnel['purchase_and_service_users'], 'Купили товар и услугу', COLOR_QUATERNARY)
    legends[2] = assign_legend(fig, 1, 2, 2, 4)

    # (1,3) Ретеншен когорты
    for offset, color in ((3, COLOR_TOPUP), (7, COLOR_DEBIT), (30, COLOR_TERTIARY)):
        retention = cohort_dau.dropna(subset=[f'retention_d{offset}_pct'])
        add_line(fig, 1, 3, retention['date'], retention[f'retention_d{offset}_pct'], f'D+{offset}', color, '%')
    legends[3] = assign_legend(fig, 1, 3, 3, 3)

    # (2,1) Сумма пополнений vs списаний, ₽
    add_line(fig, 2, 1, financial_daily['date'], financial_daily['topup_sum'], 'Пополнения', COLOR_TOPUP, ' ₽')
    add_line(fig, 2, 1, financial_daily['date'], financial_daily['debit_sum'], 'Списания', COLOR_DEBIT, ' ₽')
    legends[4] = assign_legend(fig, 2, 1, 4, 2)

    # (2,2) Средняя сумма на клиента в день, ₽
    add_line(fig, 2, 2, financial_daily['date'], financial_daily['avg_topup_per_user'], 'Ср. пополнение на клиента', COLOR_TOPUP, ' ₽')
    add_line(fig, 2, 2, financial_daily['date'], financial_daily['avg_debit_per_user'], 'Ср. списание на клиента', COLOR_DEBIT, ' ₽')
    legends[5] = assign_legend(fig, 2, 2, 5, 2)

    # (2,3) Распределение списаний: товары vs услуги, ₽
    add_line(fig, 2, 3, financial_daily['date'], financial_daily['debit_sum_products'], 'Товары', COLOR_TERTIARY, ' ₽')
    add_line(fig, 2, 3, financial_daily['date'], financial_daily['debit_sum_services'], 'Платные услуги', COLOR_QUATERNARY, ' ₽')
    legends[6] = assign_legend(fig, 2, 3, 6, 2)

    # (3,1) Распределение товаров — кол-во покупок по каждому наименованию
    add_distribution_bar(fig, 3, 1, product_distribution, COLOR_TERTIARY, 'покупок')

    # (3,2) Доходимость — сколько запросов из events дошло до списания
    add_intent_bars(fig, 3, 2, service_intent)
    legends[8] = assign_legend(fig, 3, 2, 8, 2)

    # (3,3) Итог за период — бар сравнение
    total_topup = financial_daily['topup_sum'].sum()
    total_debit = financial_daily['debit_sum'].sum()
    fig.add_trace(
        go.Bar(
            x=['Пополнения', 'Списания'], y=[total_topup, total_debit],
            marker_color=[COLOR_TOPUP, COLOR_DEBIT],
            text=[f'₽{total_topup:,.0f}', f'₽{total_debit:,.0f}'],
            textposition='outside',
            hovertemplate='%{x}: %{y:,.0f} ₽<extra></extra>',
            showlegend=False,
        ),
        row=3, col=3,
    )

    # Общий layout + отдельные легенды по углам нужных subplot-ов
    legend_layout = {}
    for idx, value in legends.items():
        if value is None or value[0] is None:
            continue
        name, position = value
        legend_layout[name] = position

    fig.update_layout(
        title=dict(text='Analytics Dashboard — интернет-магазин умного дома', font=dict(size=22, color=INK), x=0.5),
        paper_bgcolor=SURFACE,
        plot_bgcolor=SURFACE,
        font=dict(family='system-ui, -apple-system, "Segoe UI", sans-serif', color=INK_SECONDARY),
        hovermode='x unified',
        barmode='group',
        height=1100, margin=dict(t=110, l=60, r=40, b=90),
        **legend_layout,
    )
    fig.update_xaxes(showgrid=False, linecolor=MUTED, tickfont=dict(size=9, color=MUTED))
    fig.update_yaxes(showgrid=True, gridcolor=GRID, zeroline=False, tickfont=dict(size=9, color=MUTED))
    fig.update_annotations(font=dict(size=13, color=INK))

    return fig


# ---------------------------------------------------------------------------
# Dash-приложение
# ---------------------------------------------------------------------------

app = dash.Dash(__name__)
app.title = 'Analytics Dashboard — интернет-магазин умного дома'

DATE_PRESETS = CONFIG['date_presets']

PRESET_BUTTON_STYLE = {
    'fontFamily': 'system-ui, -apple-system, "Segoe UI", sans-serif',
    'fontSize': '13px', 'color': '#2a78d6', 'background': '#fcfcfb',
    'border': '1px solid #2a78d6', 'borderRadius': '4px',
    'padding': '4px 10px', 'cursor': 'pointer',
}

app.layout = html.Div(
    style={'fontFamily': 'system-ui, -apple-system, "Segoe UI", sans-serif', 'padding': '16px 24px'},
    children=[
        html.H2('Analytics Dashboard — интернет-магазин умного дома', style={'marginBottom': '4px'}),
        html.Div(
            style={
                'display': 'flex', 'gap': '32px', 'flexWrap': 'wrap',
                'alignItems': 'flex-end', 'padding': '12px 0 20px 0',
            },
            children=[
                html.Div([
                    html.Label('Диапазон дат', style={'fontSize': '13px', 'color': '#52514e'}),
                    html.Br(),
                    dcc.DatePickerRange(
                        id='date-range',
                        min_date_allowed=GLOBAL_MIN_DATE.date(),
                        max_date_allowed=GLOBAL_MAX_DATE.date(),
                        start_date=DEFAULT_START_DATE.date(),
                        end_date=DEFAULT_END_DATE.date(),
                        display_format='YYYY-MM-DD',
                    ),
                    html.Div(
                        style={'display': 'flex', 'gap': '6px', 'marginTop': '6px'},
                        children=[
                            html.Button(
                                f'{days} дн.', id=f'preset-{days}', n_clicks=0,
                                style=PRESET_BUTTON_STYLE,
                            )
                            for days in DATE_PRESETS
                        ],
                    ),
                ]),
                html.Div([
                    html.Label('Тип подписки', style={'fontSize': '13px', 'color': '#52514e'}),
                    dcc.Checklist(
                        id='subscription',
                        options=[{'label': SUBSCRIPTION_LABELS.get(s, s), 'value': s} for s in ALL_SUBSCRIPTIONS],
                        value=CONFIG['subscriptions'],
                        inline=True,
                        inputStyle={'marginRight': '4px', 'marginLeft': '12px'},
                    ),
                ]),
            ],
        ),
        dcc.Loading(
            type='circle',
            children=dcc.Graph(id='dashboard-graph', config={'responsive': True}),
        ),
    ],
)


@app.callback(
    Output('date-range', 'start_date'),
    Output('date-range', 'end_date'),
    [Input(f'preset-{days}', 'n_clicks') for days in DATE_PRESETS],
    prevent_initial_call=True,
)
def apply_date_preset(*_):
    triggered = ctx.triggered_id
    days_by_button = {f'preset-{days}': days for days in DATE_PRESETS}
    days = days_by_button.get(triggered)
    if days is None:
        return no_update, no_update

    end = GLOBAL_MAX_DATE
    start = max(GLOBAL_MIN_DATE, end - pd.Timedelta(days=days - 1))
    return start.date(), end.date()


@app.callback(
    Output('dashboard-graph', 'figure'),
    Input('date-range', 'start_date'),
    Input('date-range', 'end_date'),
    Input('subscription', 'value'),
)

def update_dashboard(start_date, end_date, subscription_types):

    # Если пользователь не выбрал ни одного тарифа
    if not subscription_types:
        fig = go.Figure()

        fig.add_annotation(
            text="Выберите хотя бы один тип подписки",
            x=0.5,
            y=0.5,
            xref="paper",
            yref="paper",
            showarrow=False,
            font=dict(size=18)
        )

        fig.update_xaxes(visible=False)
        fig.update_yaxes(visible=False)
        fig.update_layout(
            width=None,
            height=400,
            autosize=True
        )

        return fig

    # Если выбран хотя бы один тариф
    cohort_dau, funnel, financial_daily, product_distribution, service_intent = compute_dashboard(
        start_date, end_date, subscription_types,
    )

    fig = build_figure(
        cohort_dau, funnel, financial_daily, product_distribution, service_intent,
    )

    fig.update_layout(width=None, autosize=True)
    return fig


if __name__ == '__main__':
    app.run(debug=False, host='127.0.0.1', port=8051)

"""
Финансовые витрины — инкрементальное обновление по диапазону дат.

По умолчанию обновляются последние 3 дня (относительно последней даты,
присутствующей в исходных данных).

Запуск:
    python3 financial_dayly.py
    python3 financial_dayly.py --days 7
    python3 financial_dayly.py --start-date 2026-02-04 --end-date 2026-08-04
"""

import argparse
import sqlite3

import pandas as pd

VITRINE_DB_PATH = './database/vitrine.db'
DAILY_TABLE_NAME = 'financial_daily'
PERIOD_TABLE_NAME = 'financial_period'
DATE_COL = 'date'

# Списания на платные допуслуги (не товары) — см. generate_raw_data.py
EXTRA_SERVICES = {'express_delivery', 'gift_wrapping', 'extended_warranty'}


def load_data(db_path='./database/database.db'):
    """
    Загружает transactions из SQLite базы, добавляя subscription_type клиента.

    Returns:
        transactions_df (с колонкой subscription_type)
    """
    with sqlite3.connect(db_path) as conn:
        clients_df = pd.read_sql_query("SELECT client_id, subscription_type FROM clients", conn)
        transactions_df = pd.read_sql_query("SELECT * FROM transactions", conn)

    transactions_df['date'] = pd.to_datetime(transactions_df['date'])
    transactions_df = transactions_df.merge(clients_df, on='client_id', how='left')

    return transactions_df


def create_financial_daily(transactions_df):
    """
    Финансовые показатели по дням, раздельно по pro/lite.

    Columns (для segment in pro/lite):
    - date
    - topup_count_{segment}, topup_sum_{segment}
    - debit_count_{segment}, debit_sum_{segment}
    - active_paying_users_{segment}: кол-во уникальных клиентов с пополнением
    - debit_paying_users_{segment}: кол-во уникальных клиентов со списанием
    - debit_sum_products_{segment}: сумма списаний за товары
    - debit_sum_services_{segment}: сумма списаний за платные допуслуги
    """
    transactions_b = transactions_df.copy()
    transactions_b['date'] = transactions_b['date'].dt.normalize()
    segments = ('pro', 'lite')

    dates = sorted(transactions_b['date'].unique())
    result = pd.DataFrame({'date': dates}).set_index('date')

    for segment in segments:
        seg_txns = transactions_b[transactions_b['subscription_type'] == segment]
        topups = seg_txns[seg_txns['type'].isin(['topup', 'top-up'])]
        debits = seg_txns[seg_txns['type'] == 'debit']
        product_debits = debits[~debits['service'].isin(EXTRA_SERVICES)]
        service_debits = debits[debits['service'].isin(EXTRA_SERVICES)]

        topup_daily = topups.groupby('date').agg(
            **{
                f'active_paying_users_{segment}': ('client_id', 'nunique'),
                f'topup_count_{segment}': ('amount', 'count'),
                f'topup_sum_{segment}': ('amount', 'sum'),
            }
        )
        debit_daily = debits.groupby('date').agg(
            **{
                f'debit_count_{segment}': ('amount', 'count'),
                f'debit_sum_{segment}': ('amount', 'sum'),
                f'debit_paying_users_{segment}': ('client_id', 'nunique'),
            }
        )
        product_daily = product_debits.groupby('date').agg(
            **{f'debit_sum_products_{segment}': ('amount', 'sum')}
        )
        service_daily = service_debits.groupby('date').agg(
            **{f'debit_sum_services_{segment}': ('amount', 'sum')}
        )

        result = (
            result.join(topup_daily, how='left')
            .join(debit_daily, how='left')
            .join(product_daily, how='left')
            .join(service_daily, how='left')
        )

    result = result.fillna(0).astype(int).reset_index()
    return result


def create_financial_period(transactions_df, window_days=30):
    """
    Агрегированные метрики за последние window_days дней (скользящее окно по дням).

    Columns (для segment in pro/lite):
    - date
    - period_topup_sum_{segment}, period_debit_sum_{segment}
    - topup_users_{segment}, debit_users_{segment}: уникальные плательщики/списания за окно
    """
    segments = ('pro', 'lite')
    all_dates = pd.date_range(
        start=transactions_df['date'].min(),
        end=transactions_df['date'].max(),
        freq='D',
    )

    period_data = []
    for end_date in all_dates:
        start_date = end_date - pd.Timedelta(days=window_days - 1)
        window_txns = transactions_df[
            (transactions_df['date'] >= start_date) & (transactions_df['date'] <= end_date)
        ]

        row = {'date': end_date}
        for segment in segments:
            seg_txns = window_txns[window_txns['subscription_type'] == segment]
            topups = seg_txns[seg_txns['type'].isin(['topup', 'top-up'])]
            debits = seg_txns[seg_txns['type'] == 'debit']

            row[f'period_topup_sum_{segment}'] = int(topups['amount'].sum())
            row[f'period_debit_sum_{segment}'] = int(debits['amount'].sum())
            row[f'topup_users_{segment}'] = topups['client_id'].nunique()
            row[f'debit_users_{segment}'] = debits['client_id'].nunique()

        period_data.append(row)

    return pd.DataFrame(period_data)


def upsert_table(db_path, table_name, window_df, date_col, start, end):
    """
    Записывает window_df в table_name, заменяя только строки в диапазоне
    [start, end]; остальные существующие строки сохраняются как есть.
    """
    with sqlite3.connect(db_path) as conn:
        exists = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name=?", (table_name,),
        ).fetchone() is not None

        if exists:
            existing = pd.read_sql_query(f"SELECT * FROM {table_name}", conn, parse_dates=[date_col])
            existing = existing[(existing[date_col] < start) | (existing[date_col] > end)]
            combined = pd.concat([existing, window_df], ignore_index=True)
        else:
            combined = window_df

        combined = combined.sort_values(date_col).reset_index(drop=True)
        combined.to_sql(table_name, conn, if_exists='replace', index=False)

    return combined


def parse_args():
    parser = argparse.ArgumentParser(description='Инкрементальное обновление финансовых витрин в vitrine.db')
    parser.add_argument('--start-date', help='Начало диапазона, YYYY-MM-DD')
    parser.add_argument('--end-date', help='Конец диапазона, YYYY-MM-DD')
    parser.add_argument(
        '--days', type=int, default=3,
        help='Сколько последних дней обновлять, если --start-date не задан (по умолчанию 3)',
    )
    return parser.parse_args()


if __name__ == '__main__':
    args = parse_args()

    transactions_df = load_data()
    full_daily = create_financial_daily(transactions_df)
    full_period = create_financial_period(transactions_df, window_days=30)

    max_available_date = full_daily[DATE_COL].max()
    end = pd.Timestamp(args.end_date) if args.end_date else max_available_date
    start = pd.Timestamp(args.start_date) if args.start_date else end - pd.Timedelta(days=args.days - 1)

    daily_window = full_daily[(full_daily[DATE_COL] >= start) & (full_daily[DATE_COL] <= end)].copy()
    period_window = full_period[(full_period[DATE_COL] >= start) & (full_period[DATE_COL] <= end)].copy()

    daily_combined = upsert_table(VITRINE_DB_PATH, DAILY_TABLE_NAME, daily_window, DATE_COL, start, end)
    period_combined = upsert_table(VITRINE_DB_PATH, PERIOD_TABLE_NAME, period_window, DATE_COL, start, end)

    print("=== financial_daily (окно) ===")
    print(daily_window.to_string())
    print(
        f"Обновлён диапазон {start.date()} .. {end.date()}: {len(daily_window)} строк в окне, "
        f"{len(daily_combined)} строк всего в таблице '{DAILY_TABLE_NAME}' ({VITRINE_DB_PATH})\n",
    )

    print("=== financial_period (окно) ===")
    print(period_window.to_string())
    print(
        f"Обновлён диапазон {start.date()} .. {end.date()}: {len(period_window)} строк в окне, "
        f"{len(period_combined)} строк всего в таблице '{PERIOD_TABLE_NAME}' ({VITRINE_DB_PATH})",
    )

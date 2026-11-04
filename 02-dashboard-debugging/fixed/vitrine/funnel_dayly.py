"""
Витрина воронки конверсии — инкрементальное обновление по диапазону дат.

По умолчанию обновляются последние 3 дня когорт (относительно последней
даты регистрации, присутствующей в исходных данных).

Запуск:
    python3 funnel_dayly.py
    python3 funnel_dayly.py --days 7
    python3 funnel_dayly.py --start-date 2026-02-04 --end-date 2026-08-04
"""

import argparse
import sqlite3

import pandas as pd

VITRINE_DB_PATH = './database/vitrine.db'
TABLE_NAME = 'funnel'
DATE_COL = 'registration_date'

# Списания на платные допуслуги (не товары) — см. generate_raw_data.py
EXTRA_SERVICES = {'express_delivery', 'gift_wrapping', 'extended_warranty'}


def load_data(db_path='./database/database.db'):
    """
    Загружает clients, events, transactions из SQLite базы.

    Returns:
        (clients_df, events_df, transactions_df)
    """
    with sqlite3.connect(db_path) as conn:
        clients_df = pd.read_sql_query("SELECT * FROM clients", conn)
        events_df = pd.read_sql_query("SELECT * FROM events", conn)
        transactions_df = pd.read_sql_query("SELECT * FROM transactions", conn)

    clients_df['registration_date'] = pd.to_datetime(clients_df['registration_date'])
    events_df['date'] = pd.to_datetime(events_df['date'])
    transactions_df['date'] = pd.to_datetime(transactions_df['date'])

    return clients_df, events_df, transactions_df


def _suffix_union(per_date_sets, dates):
    """Для каждой даты — объединение множеств client_id из этой и всех следующих дат."""
    suffix = {}
    running = set()
    for d in reversed(dates):
        running = running | per_date_sets.get(d, set())
        suffix[d] = running
    return suffix


def create_funnel(clients_df, events_df, transactions_df):
    """
    Воронка конверсии для каждой когорты регистрации (по дням), раздельно по pro/lite.

    Columns (для segment in pro/lite):
    - registration_date: дата когорты
    - users_registered_{segment}: кол-во зарегистрировавшихся
    - feature_users_{segment}: кол-во использовавших feature_used когда-либо после регистрации
    - topup_users_{segment}: кол-во сделавших пополнение когда-либо после регистрации
    - purchase_users_{segment}: кол-во купивших товар когда-либо после регистрации
    - purchase_and_service_users_{segment}: кол-во купивших товар и оплативших допуслугу
      (в любой момент после регистрации, не обязательно в один день)
    - d1_active_{segment}: кол-во активных на следующий день после регистрации
    """
    sub_map = clients_df.set_index('client_id')['subscription_type']

    clients_b = clients_df.copy()
    clients_b['date'] = clients_b['registration_date'].dt.normalize()

    events_b = events_df.copy()
    events_b['date'] = events_b['date'].dt.normalize()
    events_b['subscription_type'] = events_b['client_id'].map(sub_map)

    transactions_b = transactions_df.copy()
    transactions_b['date'] = transactions_b['date'].dt.normalize()
    transactions_b['subscription_type'] = transactions_b['client_id'].map(sub_map)

    reg_dates = sorted(clients_b['date'].unique())
    segments = ('pro', 'lite')

    cohort_sets = {
        segment: clients_b[clients_b['subscription_type'] == segment].groupby('date')['client_id'].apply(set)
        for segment in segments
    }

    feature_events = events_b[events_b['event_type'] == 'feature_used']
    active_topups = transactions_b[
    transactions_b['type'].isin(['topup', 'top-up'])
]
    debits_b = transactions_b[transactions_b['type'] == 'debit']
    purchase_txns = debits_b[~debits_b['service'].isin(EXTRA_SERVICES)]
    service_txns = debits_b[debits_b['service'].isin(EXTRA_SERVICES)]

    feature_sets = {
        segment: feature_events[feature_events['subscription_type'] == segment].groupby('date')['client_id'].apply(set)
        for segment in segments
    }
    topup_sets = {
        segment: active_topups[active_topups['subscription_type'] == segment].groupby('date')['client_id'].apply(set)
        for segment in segments
    }
    purchase_sets = {
        segment: purchase_txns[purchase_txns['subscription_type'] == segment].groupby('date')['client_id'].apply(set)
        for segment in segments
    }
    service_sets = {
        segment: service_txns[service_txns['subscription_type'] == segment].groupby('date')['client_id'].apply(set)
        for segment in segments
    }
    active_sets = {
        segment: events_b[events_b['subscription_type'] == segment].groupby('date')['client_id'].apply(set)
        for segment in segments
    }

    all_event_dates = sorted(set(events_b['date'].unique()) | set(reg_dates))
    all_txn_dates = sorted(set(transactions_b['date'].unique()) | set(reg_dates))

    feature_suffix = {segment: _suffix_union(feature_sets[segment], all_event_dates) for segment in segments}
    topup_suffix = {segment: _suffix_union(topup_sets[segment], all_txn_dates) for segment in segments}
    purchase_suffix = {segment: _suffix_union(purchase_sets[segment], all_txn_dates) for segment in segments}
    service_suffix = {segment: _suffix_union(service_sets[segment], all_txn_dates) for segment in segments}

    funnel_data = []
    for reg_date in reg_dates:
        row = {'registration_date': reg_date}
        d1_date = reg_date + pd.Timedelta(days=1)

        for segment in segments:
            cohort_ids = cohort_sets[segment].get(reg_date, set())
            cohort_size = len(cohort_ids)
            feature_users = len(cohort_ids & feature_suffix[segment].get(reg_date, set()))
            topup_users = len(cohort_ids & topup_suffix[segment].get(reg_date, set()))
            purchase_ids = cohort_ids & purchase_suffix[segment].get(reg_date, set())
            service_ids = cohort_ids & service_suffix[segment].get(reg_date, set())
            d1_active = len(cohort_ids & active_sets[segment].get(d1_date, set()))

            row[f'users_registered_{segment}'] = cohort_size
            row[f'feature_users_{segment}'] = feature_users
            row[f'topup_users_{segment}'] = topup_users
            row[f'purchase_users_{segment}'] = len(purchase_ids)
            row[f'purchase_and_service_users_{segment}'] = len(purchase_ids & service_ids)
            row[f'd1_active_{segment}'] = d1_active

        funnel_data.append(row)

    return pd.DataFrame(funnel_data)


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
    parser = argparse.ArgumentParser(description='Инкрементальное обновление витрины funnel в vitrine.db')
    parser.add_argument('--start-date', help='Начало диапазона когорт, YYYY-MM-DD')
    parser.add_argument('--end-date', help='Конец диапазона когорт, YYYY-MM-DD')
    parser.add_argument(
        '--days', type=int, default=3,
        help='Сколько последних дней когорт обновлять, если --start-date не задан (по умолчанию 3)',
    )
    return parser.parse_args()


if __name__ == '__main__':
    args = parse_args()

    clients_df, events_df, transactions_df = load_data()
    full_result = create_funnel(clients_df, events_df, transactions_df)

    max_available_date = full_result[DATE_COL].max()
    end = pd.Timestamp(args.end_date) if args.end_date else max_available_date
    start = pd.Timestamp(args.start_date) if args.start_date else end - pd.Timedelta(days=args.days - 1)

    window = full_result[(full_result[DATE_COL] >= start) & (full_result[DATE_COL] <= end)].copy()

    combined = upsert_table(VITRINE_DB_PATH, TABLE_NAME, window, DATE_COL, start, end)

    print(window.to_string())
    print(
        f"Обновлён диапазон {start.date()} .. {end.date()}: {len(window)} строк в окне, "
        f"{len(combined)} строк всего в таблице '{TABLE_NAME}' ({VITRINE_DB_PATH})",
    )

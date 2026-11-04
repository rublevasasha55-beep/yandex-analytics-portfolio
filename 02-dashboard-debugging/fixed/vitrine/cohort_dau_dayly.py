"""
Витрина DAU и когорт — инкрементальное обновление по диапазону дат.

По умолчанию обновляются последние 3 дня (относительно последней даты,
присутствующей в исходных данных).

Запуск:
    python3 cohort_dau_dayly.py
    python3 cohort_dau_dayly.py --days 7
    python3 cohort_dau_dayly.py --start-date 2026-02-04 --end-date 2026-08-04
"""

import argparse
import sqlite3

import pandas as pd

VITRINE_DB_PATH = './database/vitrine.db'
TABLE_NAME = 'cohort_dau'
DATE_COL = 'date'

# Списания на платные допуслуги (не товары) — см. generate_raw_data.py
EXTRA_SERVICES = {'express_delivery', 'gift_wrapping', 'extended_warranty'}
RETENTION_OFFSETS = (3, 7, 30)


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


def create_cohort_dau(clients_df, events_df, transactions_df):
    """
    Витрина DAU и прогрессии когорт по дням.

    Columns:
    - date: календарный день
    - new_registrations_pro, new_registrations_lite: новые регистрации
    - active_users_pro, active_users_lite: уникальные активные пользователи (были события)
    - monetized_users_pro, monetized_users_lite: уникальные пользователи, пополнившие счёт
    - purchasing_users_pro, purchasing_users_lite: уникальные пользователи, купившие товар
    - purchasing_and_service_users_pro, purchasing_and_service_users_lite: уникальные
      пользователи, в этот же день купившие товар и оплатившие допуслугу
    - retained_d3_pro, retained_d3_lite: кол-во из когорты, активных на D+3
    - retained_d7_pro, retained_d7_lite: кол-во из когорты, активных на D+7
    - retained_d30_pro, retained_d30_lite: кол-во из когорты, активных на D+30
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

    def nunique_by_date(df, segment):
        subset = df[df['subscription_type'] == segment]
        return subset.groupby('date')['client_id'].nunique()

    def size_by_date(df, segment):
        subset = df[df['subscription_type'] == segment]
        return subset.groupby('date')['client_id'].size()

    def sets_by_date(df, segment):
        subset = df[df['subscription_type'] == segment]
        return subset.groupby('date')['client_id'].apply(set)

    new_reg_pro = size_by_date(clients_b, 'pro')
    new_reg_lite = size_by_date(clients_b, 'lite')
    active_pro = nunique_by_date(events_b, 'pro')
    active_lite = nunique_by_date(events_b, 'lite')

    topups_b = transactions_b[
    transactions_b['type'].isin(['topup', 'top-up'])
]
    monetized_pro = nunique_by_date(topups_b, 'pro')
    monetized_lite = nunique_by_date(topups_b, 'lite')

    debits_b = transactions_b[transactions_b['type'] == 'debit']
    purchase_txns = debits_b[~debits_b['service'].isin(EXTRA_SERVICES)]
    service_txns = debits_b[debits_b['service'].isin(EXTRA_SERVICES)]
    purchasing_pro = nunique_by_date(purchase_txns, 'pro')
    purchasing_lite = nunique_by_date(purchase_txns, 'lite')

    purchase_sets_pro = sets_by_date(purchase_txns, 'pro')
    purchase_sets_lite = sets_by_date(purchase_txns, 'lite')
    service_sets_pro = sets_by_date(service_txns, 'pro')
    service_sets_lite = sets_by_date(service_txns, 'lite')

    both_dates = sorted(set(purchase_txns['date'].unique()) | set(service_txns['date'].unique()))
    both_pro = {
        d: len(purchase_sets_pro.get(d, set()) & service_sets_pro.get(d, set()))
        for d in both_dates
    }
    both_lite = {
        d: len(purchase_sets_lite.get(d, set()) & service_sets_lite.get(d, set()))
        for d in both_dates
    }

    dates = sorted(clients_b['date'].unique())
    result = pd.DataFrame({'date': dates})
    result['new_registrations_pro'] = result['date'].map(new_reg_pro).fillna(0).astype(int)
    result['new_registrations_lite'] = result['date'].map(new_reg_lite).fillna(0).astype(int)
    result['active_users_pro'] = result['date'].map(active_pro).fillna(0).astype(int)
    result['active_users_lite'] = result['date'].map(active_lite).fillna(0).astype(int)
    result['monetized_users_pro'] = result['date'].map(monetized_pro).fillna(0).astype(int)
    result['monetized_users_lite'] = result['date'].map(monetized_lite).fillna(0).astype(int)
    result['purchasing_users_pro'] = result['date'].map(purchasing_pro).fillna(0).astype(int)
    result['purchasing_users_lite'] = result['date'].map(purchasing_lite).fillna(0).astype(int)
    result['purchasing_and_service_users_pro'] = result['date'].map(both_pro).fillna(0).astype(int)
    result['purchasing_and_service_users_lite'] = result['date'].map(both_lite).fillna(0).astype(int)


    # Ретеншен: D+3, D+7, D+30
    cohort_sets_pro = sets_by_date(clients_b, 'pro')
    cohort_sets_lite = sets_by_date(clients_b, 'lite')
    active_sets_pro = sets_by_date(events_b, 'pro')
    active_sets_lite = sets_by_date(events_b, 'lite')

    # Последняя дата, за которую есть события
    last_event_date = events_b['date'].max()

    for offset in RETENTION_OFFSETS:
        retained_pro, retained_lite = [], []

        for d in result['date']:
            target = d + pd.Timedelta(days=offset)

            # Период наблюдения ещё не завершён
            if target > last_event_date:
                retained_pro.append(pd.NA)
                retained_lite.append(pd.NA)
                continue

            cp = cohort_sets_pro.get(d, set())
            cl = cohort_sets_lite.get(d, set())

            retained_pro.append(
                len(cp & active_sets_pro.get(target, set()))
            )
            retained_lite.append(
                len(cl & active_sets_lite.get(target, set()))
            )

        result[f'retained_d{offset}_pro'] = pd.array(
            retained_pro, dtype="Int64"
        )
        result[f'retained_d{offset}_lite'] = pd.array(
            retained_lite, dtype="Int64"
        )

    return result



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
    parser = argparse.ArgumentParser(description='Инкрементальное обновление витрины cohort_dau в vitrine.db')
    parser.add_argument('--start-date', help='Начало диапазона, YYYY-MM-DD')
    parser.add_argument('--end-date', help='Конец диапазона, YYYY-MM-DD')
    parser.add_argument(
        '--days', type=int, default=3,
        help='Сколько последних дней обновлять, если --start-date не задан (по умолчанию 3)',
    )
    return parser.parse_args()


if __name__ == '__main__':
    args = parse_args()

    clients_df, events_df, transactions_df = load_data()
    full_result = create_cohort_dau(clients_df, events_df, transactions_df)

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

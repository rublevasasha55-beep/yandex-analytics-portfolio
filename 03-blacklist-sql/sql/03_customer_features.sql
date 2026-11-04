-- Кубик 3: итоговые признаки на уровне customer_id.
--
-- Кубик запускается после logs.sql и orders.sql. Он должен объединить
-- logs_features, orders_features и customer_ids.
--
-- В файле есть синтаксические ошибки и две незаполненные формулы TODO.
-- Ещё две формулы TODO находятся в предыдущих кубиках.

DROP TABLE IF EXISTS customer_features;

CREATE TABLE customer_features AS
WITH all_sessions AS (
    SELECT customer_id, session_id
    FROM logs_features

    UNION

    SELECT customer_id, session_id
    FROM orders_features
),
session_features AS (
    SELECT
        s.customer_id,
        s.session_id,
        COALESCE(l.bonus_blacklist_requests_cnt, 0)
            AS bonus_blacklist_requests_cnt,
        COALESCE(l.promo_blacklist_requests_cnt, 0)
            AS promo_blacklist_requests_cnt,
        COALESCE(l.blacklist_funnel_flg, 0) AS blacklist_funnel_flg,
        l.first_promo_blacklist_after_bonus_dttm,
        COALESCE(o.paid_orders_cnt, 0) AS paid_orders_cnt,
        COALESCE(o.paid_orders_without_promo_cnt, 0)
            AS paid_orders_without_promo_cnt,
        COALESCE(o.paid_orders_without_promo_gmv, 0)
            AS paid_orders_without_promo_gmv,
        o.first_paid_order_without_promo_dttm,
        CASE
            WHEN l.blacklist_funnel_flg = 1
             AND o.first_paid_order_without_promo_dttm
                    > l.first_promo_blacklist_after_bonus_dttm
            THEN 1
            ELSE 0
        END AS converted_blacklist_session_flg
    FROM all_sessions AS s
    LEFT JOIN logs_features AS l
      ON l.customer_id = s.customer_id
     AND l.session_id = s.session_id
    LEFT JOIN orders_features AS o
      ON o.customer_id = s.customer_id
     AND o.session_id = s.session_id
),
customer_metrics AS (
    SELECT
        customer_id,
        SUM(bonus_blacklist_requests_cnt) AS bonus_blacklist_requests_cnt,
        SUM(promo_blacklist_requests_cnt) AS promo_blacklist_requests_cnt,
        SUM(blacklist_funnel_flg) AS blacklist_funnel_sessions_cnt,
        SUM(paid_orders_cnt) AS paid_orders_cnt,
        SUM(paid_orders_without_promo_cnt) AS paid_orders_without_promo_cnt,
        SUM(paid_orders_without_promo_gmv) AS paid_orders_without_promo_gmv,
        SUM(converted_blacklist_session_flg) AS converted_blacklist_sessions_cnt
    FROM session_features
    GROUP BY customer_id
)
SELECT
    c.customer_id,
    COALESCE(m.bonus_blacklist_requests_cnt, 0)
        AS bonus_blacklist_requests_cnt,
    COALESCE(m.promo_blacklist_requests_cnt, 0)
        AS promo_blacklist_requests_cnt,
    COALESCE(m.blacklist_funnel_sessions_cnt, 0)
        AS blacklist_funnel_sessions_cnt,
    COALESCE(m.paid_orders_cnt, 0) AS paid_orders_cnt,
    COALESCE(m.paid_orders_without_promo_cnt, 0)
        AS paid_orders_without_promo_cnt,
    COALESCE(m.paid_orders_without_promo_gmv, 0)
        AS paid_orders_without_promo_gmv,
    COALESCE(m.converted_blacklist_sessions_cnt, 0)
        AS converted_blacklist_sessions_cnt,
    
CASE
    WHEN COALESCE(m.blacklist_funnel_sessions_cnt, 0) > 0
    THEN 1.0 * COALESCE(m.converted_blacklist_sessions_cnt, 0)
         / m.blacklist_funnel_sessions_cnt
    ELSE 0.0
END AS blacklist_to_order_conversion_rate

FROM customer_ids AS c
LEFT JOIN customer_metrics AS m
  ON m.customer_id = c.customer_id
ORDER BY c.customer_id;

-- Кубик 1: признаки по логам на уровне customer_id × session_id.
--
-- В файле есть синтаксические ошибки и незавершённые выражения.
-- Исправьте их и создайте таблицу logs_features с полями из условия.

DROP TABLE IF EXISTS logs_features;

CREATE TABLE logs_features AS
WITH blacklist_events AS (
    SELECT
        l.log_id,
        l.event_dttm,
        l.customer_id,
        l.session_id,
        l.endpoint,
        CASE
            WHEN l.triggered_rules > 0
             AND l.request_status = 'rejected'
             AND EXISTS (
                 SELECT 1
                 FROM json_each(l.triggered_rule_codes) AS rule
                 WHERE rule.value = 'blacklist'
             )
            THEN 1
            ELSE 0
        END AS blacklist_flg
    FROM logs AS l
),
session_stats AS (
    SELECT
        customer_id,
        session_id,
        SUM(
            CASE
                WHEN endpoint = 'bonuses' AND blacklist_flg = 1 THEN 1
                ELSE 0
            END
        ) AS bonus_blacklist_requests_cnt,
        SUM(
            CASE
                WHEN endpoint = 'promo' AND blacklist_flg = 1 THEN 1
                ELSE 0
            END
        ) AS promo_blacklist_requests_cnt,
        MIN(
            CASE
                WHEN endpoint = 'bonuses' AND blacklist_flg = 1
                THEN event_dttm
            END
        ) AS first_bonus_blacklist_dttm
    FROM blacklist_events
    GROUP BY customer_id, session_id
),
promo_after_bonus AS (
    SELECT
        s.*,
        (
            SELECT MIN(e.event_dttm)
            FROM blacklist_events AS e
            WHERE e.customer_id = s.customer_id
              AND e.session_id = s.session_id
              AND e.endpoint = 'promo'
              AND e.blacklist_flg = 1
              AND e.event_dttm > s.first_bonus_blacklist_dttm
        ) AS first_promo_blacklist_after_bonus_dttm
    FROM session_stats AS s
)
SELECT
    customer_id,
    session_id,
    bonus_blacklist_requests_cnt,
    promo_blacklist_requests_cnt,
    first_bonus_blacklist_dttm,
    first_promo_blacklist_after_bonus_dttm,
    CASE
    WHEN first_bonus_blacklist_dttm IS NOT NULL
     AND first_promo_blacklist_after_bonus_dttm IS NOT NULL
    THEN 1
    ELSE 0
END AS blacklist_funnel_flg
FROM promo_after_bonus;

-- Кубик 2: признаки по заказам на уровне customer_id × session_id.
--
-- В файле есть синтаксические ошибки и незавершённые выражения.
-- Исправьте их и создайте таблицу orders_features с полями из условия.

DROP TABLE IF EXISTS orders_features;

CREATE TABLE orders_features AS
SELECT
    customer_id,
    session_id,
    SUM(
        CASE
            WHEN order_status IN ('paid', 'completed') THEN 1
            ELSE 0
        END
    ) AS paid_orders_cnt,
    SUM(
        CASE
            WHEN order_status IN ('paid', 'completed')
             AND promo_id IS NULL
            THEN 1
            ELSE 0
        END
    ) AS paid_orders_without_promo_cnt,
    SUM(
    CASE
        WHEN order_status IN ('paid', 'completed')
         AND promo_id IS NULL
        THEN order_amount
        ELSE 0
    END
) AS paid_orders_without_promo_gmv,
    MIN(
        CASE
            WHEN order_status IN ('paid', 'completed')
             AND promo_id IS NULL
            THEN created_dttm
        END
    ) AS first_paid_order_without_promo_dttm
FROM orders
GROUP BY customer_id, session_id;

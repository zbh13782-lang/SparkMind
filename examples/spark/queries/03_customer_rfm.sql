WITH customer_orders AS (
  SELECT
    customer_id,
    DATEDIFF(DATE '2026-02-01', MAX(dt)) AS recency_days,
    COUNT(DISTINCT order_id) AS frequency,
    SUM(total_amount) AS monetary
  FROM sparkmind_demo.fact_order
  WHERE status IN ('paid', 'shipped', 'completed')
    AND total_amount >= 0
  GROUP BY customer_id
), scored AS (
  SELECT
    *,
    NTILE(5) OVER (ORDER BY recency_days DESC) AS r_score,
    NTILE(5) OVER (ORDER BY frequency) AS f_score,
    NTILE(5) OVER (ORDER BY monetary) AS m_score
  FROM customer_orders
)
SELECT
  CONCAT(r_score, f_score, m_score) AS rfm_segment,
  COUNT(*) AS customer_count,
  ROUND(AVG(monetary), 2) AS avg_monetary
FROM scored
GROUP BY r_score, f_score, m_score
ORDER BY customer_count DESC
LIMIT 20

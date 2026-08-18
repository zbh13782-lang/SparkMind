SELECT
  dt,
  channel,
  COUNT(DISTINCT order_id) AS order_count,
  ROUND(SUM(total_amount), 2) AS revenue,
  ROUND(AVG(total_amount), 2) AS average_order_value
FROM sparkmind_demo.fact_order
WHERE dt BETWEEN DATE '2026-01-01' AND DATE '2026-01-07'
  AND status IN ('paid', 'shipped', 'completed')
  AND total_amount >= 0
GROUP BY dt, channel
ORDER BY dt, channel

SELECT
  customer_id,
  COUNT(*) AS event_count,
  ROUND(COUNT(*) / SUM(COUNT(*)) OVER (), 6) AS event_share
FROM sparkmind_demo.fact_event
WHERE dt BETWEEN DATE '2026-01-01' AND DATE '2026-01-07'
GROUP BY customer_id
ORDER BY event_count DESC
LIMIT 20

WITH session_flags AS (
  SELECT
    dt,
    session_id,
    MAX(CASE WHEN event_type = 'page_view' THEN 1 ELSE 0 END) AS viewed,
    MAX(CASE WHEN event_type = 'add_to_cart' THEN 1 ELSE 0 END) AS added,
    MAX(CASE WHEN event_type = 'checkout' THEN 1 ELSE 0 END) AS checked_out,
    MAX(CASE WHEN event_type = 'purchase' THEN 1 ELSE 0 END) AS purchased
  FROM sparkmind_demo.fact_event
  WHERE dt BETWEEN DATE '2026-01-01' AND DATE '2026-01-07'
    AND _corrupt_record IS NULL
  GROUP BY dt, session_id
)
SELECT
  dt,
  SUM(viewed) AS view_sessions,
  SUM(added) AS cart_sessions,
  SUM(checked_out) AS checkout_sessions,
  SUM(purchased) AS purchase_sessions,
  ROUND(SUM(purchased) / NULLIF(SUM(viewed), 0), 4) AS view_to_purchase_rate
FROM session_flags
GROUP BY dt
ORDER BY dt

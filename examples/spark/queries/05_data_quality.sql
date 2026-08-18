SELECT 'duplicate_order_id' AS rule, COUNT(*) AS violations
FROM (
  SELECT order_id
  FROM sparkmind_demo.fact_order
  GROUP BY order_id
  HAVING COUNT(*) > 1
)
UNION ALL
SELECT 'negative_order_amount', COUNT(*)
FROM sparkmind_demo.fact_order
WHERE total_amount < 0
UNION ALL
SELECT 'blank_customer_email', COUNT(*)
FROM sparkmind_demo.dim_customer
WHERE email IS NULL OR TRIM(email) = ''
UNION ALL
SELECT 'corrupt_json_record', COUNT(*)
FROM sparkmind_demo.fact_event
WHERE _corrupt_record IS NOT NULL
UNION ALL
SELECT 'late_event_over_24h', COUNT(*)
FROM sparkmind_demo.fact_event
WHERE ingest_time > event_time + INTERVAL 24 HOURS

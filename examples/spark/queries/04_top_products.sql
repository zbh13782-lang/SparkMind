WITH ranked_products AS (
  SELECT
    p.category,
    i.product_id,
    p.brand,
    SUM(i.quantity) AS units,
    ROUND(SUM(i.item_amount), 2) AS item_revenue,
    DENSE_RANK() OVER (
      PARTITION BY p.category
      ORDER BY SUM(i.item_amount) DESC
    ) AS category_rank
  FROM sparkmind_demo.fact_order_item i
  JOIN sparkmind_demo.dim_product p
    ON i.product_id = p.product_id
  WHERE i.dt BETWEEN DATE '2026-01-01' AND DATE '2026-01-07'
  GROUP BY p.category, i.product_id, p.brand
)
SELECT category, product_id, brand, units, item_revenue, category_rank
FROM ranked_products
WHERE category_rank <= 5
ORDER BY category, category_rank, product_id

CREATE OR REPLACE VIEW `product-analytics-389809.retail_stores.v_sarene_order_summary` AS
SELECT
  CONCAT(COALESCE(customer_name, ''), '|', COALESCE(address_full, '')) AS sarene_id,
  customer_name,
  address_full,
  customer_type,
  MIN(CAST(order_date AS DATE))                  AS first_order_date,
  MAX(CAST(order_date AS DATE))                  AS last_order_date,
  SUM(CAST(case_equiv AS FLOAT64))               AS total_cases,
  COUNT(DISTINCT CAST(order_date AS DATE))       AS order_days,
  STRING_AGG(DISTINCT salesman ORDER BY salesman) AS salesmen
FROM `product-analytics-389809.encompass_sarene.comparison_daily_report`
WHERE customer_name IS NOT NULL
GROUP BY 1, 2, 3, 4;

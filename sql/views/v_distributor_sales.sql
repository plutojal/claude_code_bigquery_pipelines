-- Union of all distributor daily sales.
-- Sarene: sourced from encompass_sarene.comparison_daily_report (loaded daily via GCS → Cloud Fn).
-- Future distributors: add a UNION ALL block below.
CREATE OR REPLACE VIEW `product-analytics-389809.retail_stores.v_distributor_sales` AS

SELECT
  'sarene'                                                               AS distributor_name,
  CONCAT(COALESCE(customer_name, ''), '|', COALESCE(address_full, '')) AS customer_id,
  customer_name,
  address_full,
  customer_type,
  CAST(order_date AS DATE)        AS order_date,
  CAST(case_equiv AS FLOAT64)     AS case_equiv,
  salesman
FROM `product-analytics-389809.encompass_sarene.comparison_daily_report`
WHERE customer_name IS NOT NULL

-- UNION ALL
-- SELECT
--   'distributor_1'  AS distributor_name,
--   customer_id,
--   customer_name,
--   address_full,
--   customer_type,
--   order_date,
--   case_equiv,
--   salesman
-- FROM `product-analytics-389809.encompass_sarene.distributor_1_sales`
-- WHERE customer_name IS NOT NULL
;

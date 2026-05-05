-- Chain-level rollup for the Chain Overview table.
CREATE OR REPLACE VIEW `product-analytics-389809.retail_stores.v_dashboard_chain_overview` AS
SELECT
  chain_name,
  state,
  COUNT(*)                       AS total_stores,
  COUNTIF(on_any_distributor)    AS stores_we_are_in,
  ROUND(AVG(rating), 2)          AS avg_rating,
  SUM(review_count)              AS total_reviews,
  SUM(total_cases)               AS total_cases_ordered,
  MIN(first_order_date)          AS first_order_date
FROM `product-analytics-389809.retail_stores.v_dashboard_stores`
GROUP BY 1, 2
ORDER BY total_stores DESC;

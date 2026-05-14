-- Union of all distributor account universes.
-- Sarene: currently sourced from comparison_daily_report as a proxy until
-- encompass_sarene.account_universe weekly data is validated (see Ed's note).
-- Future distributors: add a UNION ALL block below.
CREATE OR REPLACE VIEW `product-analytics-389809.retail_stores.v_distributor_account_universe` AS

SELECT
  'sarene'      AS distributor_name,
  sarene_id     AS customer_id,
  original_name AS customer_name,
  clean_name,
  clean_address,
  parsed_city,
  parsed_state  AS state,
  zip_code      AS zip
FROM `product-analytics-389809.retail_stores.v_sarene_clean`

-- UNION ALL
-- SELECT
--   'distributor_1'  AS distributor_name,
--   customer_id,
--   customer_name,
--   clean_name,
--   clean_address,
--   parsed_city,
--   state,
--   zip
-- FROM `product-analytics-389809.retail_stores.v_distributor_1_clean`
;

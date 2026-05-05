-- Main dashboard feed: one row per store with location, match status, and Sarene order data.
-- latitude/longitude prefers the Places API value (when available) and falls back to zip centroid.
CREATE OR REPLACE VIEW `product-analytics-389809.retail_stores.v_dashboard_stores` AS
WITH sarene_match AS (
  -- Extract the matched Sarene customer_id for each store (NULL if unmatched)
  SELECT
    ub.store_id,
    dm.customer_id AS sarene_id
  FROM `product-analytics-389809.retail_stores.unified_businesses` ub,
  UNNEST(ub.distributor_matches) AS dm
  WHERE dm.distributor_name = 'sarene'
    AND dm.matched = TRUE
)
SELECT
  ub.store_id,
  ub.brand,
  ub.store_name,
  ub.chain_name,
  ub.address,
  ub.phone,
  ub.email,
  ub.parsed_country,
  ub.parsed_city,
  ub.house_number,
  ub.road,
  ub.zip,
  ub.state,
  ub.is_chain,

  -- Scraped quality signals (populated once scraper is live)
  ub.rating,
  ub.review_count,

  -- Match flags
  ub.on_salesforce,
  ub.on_any_distributor,
  ub.sf_account_id,
  ub.sf_account_name,
  ub.sf_match_status,

  -- Location: prefer precise Places API coords, fall back to zip centroid
  COALESCE(ub.lat, zl.latitude)  AS latitude,
  COALESCE(ub.lng, zl.longitude) AS longitude,
  zl.pop_density_sqmi,
  zl.area_type,
  zl.county,

  -- Sarene order history (NULL for stores not yet Sarene customers)
  so.first_order_date,
  so.last_order_date,
  so.total_cases,
  so.order_days,
  so.salesmen        AS sarene_salesmen,
  so.customer_type   AS sarene_customer_type,

  ub.matched_at,
  ub.run_date
FROM `product-analytics-389809.retail_stores.unified_businesses` ub
LEFT JOIN `product-analytics-389809.retail_stores.zip_lookup` zl
  ON ub.zip = zl.zip
LEFT JOIN sarene_match sm
  ON ub.store_id = sm.store_id
LEFT JOIN `product-analytics-389809.retail_stores.v_sarene_order_summary` so
  ON sm.sarene_id = so.sarene_id;

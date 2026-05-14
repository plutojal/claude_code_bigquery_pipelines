-- Main dashboard feed: one row per store with location, match status, and Sarene order data.
-- latitude/longitude prefers the Places API value (when available) and falls back to zip centroid.
CREATE OR REPLACE VIEW `product-analytics-389809.retail_stores.v_dashboard_stores` AS
WITH sarene_match AS (
  -- Extract the matched Sarene customer_id for each store (NULL if unmatched)
  SELECT
    au.store_id,
    dm.customer_id AS sarene_id
  FROM `product-analytics-389809.retail_stores.account_universe` au,
  UNNEST(au.distributor_matches) AS dm
  WHERE dm.distributor_name = 'sarene'
    AND dm.matched = TRUE
)
SELECT
  au.store_id,
  au.brand,
  au.store_name,
  au.chain_name,
  au.address,
  au.phone,
  au.email,
  au.parsed_country,
  au.parsed_city,
  au.house_number,
  au.road,
  au.zip,
  au.state,
  au.is_chain,

  -- Scraped quality signals (populated once scraper is live)
  au.rating,
  au.review_count,

  -- Match flags
  au.sarene_flag,

  -- Location: prefer precise Places API coords, fall back to zip centroid
  COALESCE(au.lat, zl.latitude)  AS latitude,
  COALESCE(au.lng, zl.longitude) AS longitude,
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

  au.matched_at,
  au.run_date
FROM `product-analytics-389809.retail_stores.account_universe` au
LEFT JOIN `product-analytics-389809.retail_stores.zip_lookup` zl
  ON au.zip = zl.zip
LEFT JOIN sarene_match sm
  ON au.store_id = sm.store_id
LEFT JOIN `product-analytics-389809.retail_stores.v_sarene_order_summary` so
  ON sm.sarene_id = so.sarene_id;

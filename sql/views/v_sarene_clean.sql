CREATE OR REPLACE VIEW `product-analytics-389809.retail_stores.v_sarene_clean` AS
SELECT
  CONCAT(COALESCE(customer_name, ''), '|', COALESCE(address_full, '')) AS sarene_id,
  customer_name AS original_name,
  LOWER(TRIM(REGEXP_REPLACE(
    REGEXP_REPLACE(
      COALESCE(customer_name, ''),
      r'(?i)\s*\b(LLC|L\.L\.C\.|Inc\.?|Corp\.?|Ltd\.?|Co\.?|Company|Group|Holdings)\b',
      ''
    ),
    r'[^\w\s]', ' '
  ))) AS clean_name,
  LOWER(TRIM(COALESCE(address_full, ''))) AS clean_address,
  REGEXP_EXTRACT(address_full, r',\s*([A-Z]{2})\s+\d{5}') AS parsed_state
FROM `product-analytics-389809.encompass_sarene.comparison_daily_report`
WHERE customer_name IS NOT NULL;

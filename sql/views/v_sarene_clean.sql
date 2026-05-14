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
  REGEXP_EXTRACT(address_full, r',\s*([^,]+),\s*[A-Z]{2}\s+\d{5}') AS parsed_city,
  REGEXP_EXTRACT(address_full, r',\s*([A-Z]{2})\s+\d{5}') AS parsed_state,
  REGEXP_EXTRACT(address_full, r'[A-Z]{2}\s+(\d{5})') AS zip_code
FROM `product-analytics-389809.encompass_sarene.comparison_daily_report`
WHERE customer_name IS NOT NULL;

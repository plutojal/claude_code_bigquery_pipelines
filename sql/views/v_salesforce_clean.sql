CREATE OR REPLACE VIEW `product-analytics-389809.retail_stores.v_salesforce_clean` AS
SELECT
  Id,
  Name AS original_name,
  LOWER(TRIM(REGEXP_REPLACE(
    REGEXP_REPLACE(
      COALESCE(Name, ''),
      r'(?i)\s*\b(LLC|L\.L\.C\.|Inc\.?|Corp\.?|Ltd\.?|Co\.?|Company|Group|Holdings|Incorporated)\b',
      ''
    ),
    r'[^\w\s]', ' '
  ))) AS clean_name,
  BillingStreet,
  BillingCity,
  BillingState,
  BillingPostalCode,
  LOWER(TRIM(CONCAT(
    COALESCE(BillingStreet, ''), ' ',
    COALESCE(BillingCity, ''), ' ',
    COALESCE(BillingState, '')
  ))) AS clean_address
FROM `product-analytics-389809.salesforce_portable.account`;
